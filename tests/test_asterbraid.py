"""asterbraid: 기존 autonomous 테스트를 그대로 다시 돌리고, 확장한 스케줄러 기능을 검사한다."""

import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import test_autonomous as base

MODS = "asterbraid"


def legacy_gates_off(case):
    """옛 autonomous 테스트는 검증자 배정·설계 검증 없이 쓴다. 그 두 관문만 끄고 나머지 규칙은 그대로 돌린다."""
    p = case.repo.path / ".claude/hooks/auto.config.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data.update(verify_assignment=False, design_review="off")
    p.write_text(json.dumps(data), encoding="utf-8")
    case.repo.git("commit", "-qam", "test: 옛 관문 끔")  # 메인 체크아웃이 깨끗해야 통합한다


class TestSchedulerRegression(base.TestScheduler):
    template = MODS

    def setUp(self):
        super().setUp()
        legacy_gates_off(self)


class TestIntegrationRegression(base.TestIntegration):
    template = MODS

    def setUp(self):
        super().setUp()
        legacy_gates_off(self)


class ModsCase(base.AutoCase):
    template = MODS

    def ready_to_merge(self, tid, files=None, paths=base.PATHS, **kw):
        self.add(tid, paths=paths, **kw)
        self.ok("start", tid, "--pool", "implement", "--agent", "impl")
        wt = self.worktree(tid, files or {"src/a.py": f"A = '{tid}'\n"})
        self.ok("submit", tid, cwd=wt)
        self.ok("start", tid, "--pool", "verify", "--agent", "v1")
        self.ok("evidence", tid, "--verifier", "v1", "--verdict", "PASS", "--sha", self.repo.git("rev-parse", "HEAD", cwd=wt))
        return wt

    def snapshot(self):
        path = Path(self.ok("snapshot")["snapshot"])
        return json.loads(path.read_text(encoding="utf-8"))

    def task(self, snap, tid):
        return next(t for t in snap["tasks"] if t["id"] == tid)

    def preview_ready(self, tid="UI", versions=1):
        self.add(tid, paths="ui/", preview=True)
        page = self.tmp / f"{tid}.html"
        for n in range(1, versions + 1):
            if n > 1:
                self.ok("revise", tid, "--feedback", f"v{n - 1} 수정")
            self.ok("start", tid, "--pool", "preview", "--agent", "designer")
            page.write_text(f"<h1>{tid} v{n}</h1>", encoding="utf-8")
            self.ok("preview", tid, "--file", str(page))
        return [v["sha256"] for v in self.state(tid)["preview"]["versions"]]


class TestSnapshot(ModsCase):
    def test_every_write_refreshes_snapshot_with_schema(self):
        self.add("A")
        snap = json.loads(Path(self.ok("snapshot")["snapshot"]).read_text(encoding="utf-8"))
        self.assertEqual(snap["schema_version"], 1)
        self.assertEqual(snap["integration"]["branch"], "integration")
        self.assertEqual(snap["integration"]["head"], self.head())
        self.add("B", paths="b/")  # snapshot 명령 없이 상태 기록만으로 갱신된다
        path = Path(self.ok("snapshot")["snapshot"])
        self.add("C", paths="c/")
        snap = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(t["id"] for t in snap["tasks"]), ["A", "B", "C"])
        self.assertEqual(snap["total"], 3)
        self.assertEqual(snap["slots"]["implement"], {"used": 0, "limit": 3})

    def test_snapshot_explains_why_tasks_wait(self):
        self.add("A", paths="src/a/")
        self.add("B", paths="src/a/x.py")
        self.add("C", paths="src/c/", deps="A")
        self.ok("start", "A", "--pool", "implement", "--agent", "i1")
        snap = self.snapshot()
        self.assertIn("겹친다", self.task(snap, "B")["waiting"])
        self.assertIn("선행 작업", self.task(snap, "C")["waiting"])
        self.assertFalse(self.task(snap, "B")["runnable"])
        self.assertEqual(self.task(snap, "A")["attempts"], 1)
        self.assertTrue(any(e["task"] == "A" and e["to"] == "IMPLEMENTING" for e in snap["events"]))
        self.assertEqual(self.task(snap, "A")["timeline"][-1]["state"], "IMPLEMENTING")

    def test_snapshot_lives_outside_worktree(self):
        self.add("A")
        self.snapshot()
        self.assertEqual(self.repo.git("status", "--porcelain"), "")


class TestAgentMarker(ModsCase):
    def test_start_returns_marker_and_counts_attempts(self):
        self.add("A")
        first = self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        self.assertEqual(first["attempt"], 1)
        self.assertEqual(first["marker"], "[auto-task id=A pool=implement attempt=1]")
        self.ok("fail", "A", "--reason", "다시")
        self.assertEqual(self.ok("start", "A", "--pool", "implement", "--agent", "impl")["attempt"], 2)

    def test_task_id_must_be_safe_for_paths_and_markers(self):
        for bad in ("../x", "a b", "", "-x"):
            with self.subTest(bad=bad):
                self.refused("add", "--id", bad, "--title", "t", "--criteria", "c", "--paths", "p/")


class TestDecisions(ModsCase):
    def decide(self, did="D-1", tasks="UI", **kw):
        args = ["decide", "add", "--id", did, "--title", "레이아웃", "--question", "어느 안?", "--tasks", tasks,
                "--option", "A: 카드", "--option", "B: 표", "--pros", "A: 한눈에", "--cons", "B: 좁은 화면",
                "--recommend", "A", "--why", "모바일 비중", "--kind", "ux"]
        for key, value in kw.items():
            args += [f"--{key}", value]
        return self.ok(*args)

    def test_open_decision_holds_only_linked_tasks(self):
        self.add("UI", paths="ui/")
        self.add("API", paths="api/")
        self.decide()
        nxt = self.ok("next", "--pool", "implement")
        self.assertEqual([t["id"] for t in nxt["dispatch"]], ["API"])
        self.assertIn("D-1", {w["id"]: w["why"] for w in nxt["waiting"]}["UI"])
        self.assertIn("D-1", self.refused("start", "UI", "--pool", "implement", "--agent", "i"))
        self.ok("decide", "answer", "D-1", "--choice", "B", "--note", "표가 낫다")
        self.ok("start", "UI", "--pool", "implement", "--agent", "i")
        d = next(x for x in self.snapshot()["decisions"] if x["id"] == "D-1")
        self.assertEqual((d["state"], d["choice"], d["by"]), ("ANSWERED", "B", "user"))
        self.assertEqual(d["options"][0], {"key": "A", "label": "카드", "pros": "한눈에", "cons": ""})

    def test_invalid_duplicate_and_unanswered(self):
        self.add("UI", paths="ui/")
        self.assertIn("두 개 이상", self.refused("decide", "add", "--id", "D-1", "--title", "t", "--question", "q",
                                              "--option", "A: 하나", "--tasks", "UI"))
        self.assertIn("작업이 없다", self.refused("decide", "add", "--id", "D-1", "--title", "t", "--question", "q",
                                             "--option", "A: a", "--option", "B: b", "--tasks", "X"))
        self.decide()
        self.assertIn("이미 있다", self.refused("decide", "add", "--id", "D-1", "--title", "t", "--question", "q",
                                            "--option", "A: a", "--option", "B: b", "--tasks", "UI"))
        self.assertIn("선택지", self.refused("decide", "answer", "D-1", "--choice", "Z"))
        self.assertEqual(self.task(self.snapshot(), "UI")["decisions_open"], ["D-1"])  # 응답 전에는 열린 채로 남는다
        self.ok("decide", "answer", "D-1", "--choice", "A")
        self.assertIn("OPEN이 아니다", self.refused("decide", "answer", "D-1", "--choice", "B"))
        self.decide("D-2")
        self.ok("decide", "cancel", "D-2", "--reason", "요구사항 변경")
        self.assertEqual(self.task(self.snapshot(), "UI")["decisions_open"], [])

    def test_open_decision_blocks_merge(self):
        self.ready_to_merge("A")
        self.decide("D-9", tasks="A")
        code, status, out = self.integrate("A")
        self.assertEqual((code, status), (2, "REJECTED"))
        self.assertIn("D-9", out)
        self.ok("decide", "answer", "D-9", "--choice", "A")
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))


class TestPauseAndPriority(ModsCase):
    def test_pause_stops_new_assignments_only(self):
        self.add("A")
        self.add("B", paths="b/")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        self.ok("pause", "--reason", "점검")
        nxt = self.ok("next", "--pool", "implement")
        self.assertEqual(nxt["dispatch"], [])
        self.assertIn("일시 중지", nxt["waiting"][0]["why"])
        self.assertIn("일시 중지", self.refused("start", "B", "--pool", "implement", "--agent", "i2"))
        self.assertEqual(self.state("A")["state"], "IMPLEMENTING")  # 실행 중인 작업은 그대로
        self.assertEqual(self.snapshot()["paused"]["reason"], "점검")
        self.ok("resume")
        self.ok("start", "B", "--pool", "implement", "--agent", "i2")

    def test_priority_changes_dispatch_order(self):
        self.add("A")
        self.add("B", paths="b/")
        self.ok("priority", "B", "5")
        self.assertEqual([t["id"] for t in self.ok("next", "--pool", "implement")["dispatch"]], ["B", "A"])


class TestCloseResearch(ModsCase):
    """조사·설계만 하는 작업은 구현 단계 없이 결과 노트와 함께 완료로 닫는다."""

    def note(self, rel="notes/ENV.md"):
        path = self.repo.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 결과\n", encoding="utf-8")
        return rel

    def test_close_research_stage_with_notes(self):
        self.add("ENV", profile="research", paths="docs/env.md")
        self.add("CORE", deps="ENV")
        self.ok("start", "ENV", "--pool", "research", "--agent", "r")
        self.assertIn("노트", self.refused("done", "ENV", "--close"))  # 결과 없이 닫지 않는다
        # e2e3: 결과를 메시지로만 보내고 파일은 없었다. 노트 경로만 적고 파일이 없으면 닫지 않는다
        self.assertIn("파일", self.refused("done", "ENV", "--close", "--notes", "notes/ENV.md"))
        out = self.ok("done", "ENV", "--close", "--notes", self.note())
        self.assertEqual(out["state"], "COMPLETED")
        self.assertEqual(self.ok("next", "--pool", "implement")["dispatch"][0]["id"], "CORE")  # 의존이 풀린다

    def test_close_blocked_research_without_branch(self):
        self.add("ENV", profile="research", paths="docs/env.md")
        self.ok("start", "ENV", "--pool", "research", "--agent", "r")
        self.ok("done", "ENV", "--notes", self.note())
        self.ok("block", "ENV", "--reason", "구현 불필요")
        self.assertEqual(self.ok("done", "ENV", "--close")["state"], "COMPLETED")  # 앞서 적은 노트 파일이 있다

    def test_close_refuses_work_with_branch(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        self.ok("block", "A", "--reason", "환경")
        self.assertRegex(self.refused("done", "A", "--close", "--notes", "n.md"), "브랜치|단계가 아니다")
        self.assertEqual(self.state("A")["state"], "BLOCKED")


class TestNeedsDesign(ModsCase):
    """설계 필요성을 위험도와 따로 정한다: 위험이 낮아도 공통 계약이면 architect를 거친다."""

    def test_low_risk_task_can_require_design(self):
        out = self.add("API", risk="low", needs_design=True)
        self.assertEqual((out["state"], out["profile"]), ("PENDING", "fast"))  # 검증 정책(프로파일)은 그대로
        nxt = self.ok("next", "--pool", "design")
        self.assertEqual([(t["id"], t["design_depth"]) for t in nxt["dispatch"]], [("API", "standard")])
        self.ok("start", "API", "--pool", "design", "--agent", "architect")
        self.assertEqual(self.ok("done", "API", "--notes", "notes/API/design.md")["state"], "READY")
        self.assertEqual(self.task(self.snapshot(), "API")["design_depth"], "standard")

    def test_depth_choice_and_deep_profile_default(self):
        self.add("A", risk="low", needs_design="light")
        self.assertEqual(self.ok("next", "--pool", "design")["dispatch"][0]["design_depth"], "light")
        self.add("B", paths="b/", risk="high")
        self.assertEqual(self.task(self.snapshot(), "B")["design_depth"], "deep")  # deep 프로파일은 deep 설계
        self.assertIn("invalid choice", self.refused("add", "--id", "C", "--title", "c", "--criteria", "c",
                                                     "--paths", "c/", "--needs-design", "huge"))

    def test_plain_task_needs_no_design(self):
        out = self.add("X", risk="low")
        self.assertEqual(out["state"], "READY")
        self.assertIsNone(self.task(self.snapshot(), "X")["design_depth"])


class TestDepsCommand(ModsCase):
    """e2e3: 의존을 바꿀 명령이 없어 manager가 작업을 취소·재등록했고, 승인된 미리보기 연결이 끊겼다."""

    def test_change_deps_before_implementation(self):
        self.add("A")
        self.add("B", paths="b/")
        self.add("C", paths="c/", deps="A")
        self.assertEqual(self.ok("deps", "C", "--set", "B")["deps"], ["B"])
        self.assertEqual(self.ok("deps", "C", "--set", "")["deps"], [])
        self.assertIn("C", [t["id"] for t in self.ok("next", "--pool", "implement")["dispatch"]])
        self.assertIn("없다", self.refused("deps", "C", "--set", "NOPE"))
        self.ok("deps", "B", "--set", "A")
        self.assertIn("순환", self.refused("deps", "A", "--set", "B"))
        self.assertEqual(self.state("A")["deps"], [])  # 거부되면 그대로

    def test_refuse_after_work_started(self):
        self.add("A")
        self.add("B", paths="b/")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        self.assertIn("IMPLEMENTING", self.refused("deps", "A", "--set", "B"))

    def test_keeps_preview_approval_and_cancel_guard(self):
        shas = self.preview_ready()
        self.ok("approve", "UI", "--version", "1", "--sha", shas[0][:12])
        self.add("API", paths="api/")
        self.ok("deps", "UI", "--set", "API")
        ui = self.state("UI")
        self.assertEqual((ui["deps"], ui["preview"]["approved"]), (["API"], 1))  # 승인은 그대로
        self.assertIn("승인된 미리보기", self.refused("cancel", "UI", "--reason", "다시 등록"))
        self.assertEqual(self.ok("cancel", "UI", "--reason", "범위 제외", "--force")["state"], "CANCELLED")


class TestCleanupDryRun(ModsCase):
    """e2e2: 자동 모드가 대상이 안 보이는 cleanup을 거부했다. 먼저 지울 목록만 보여 줄 수 있어야 한다."""

    def test_dry_run_lists_targets_without_removing(self):
        wa = self.ready_to_merge("A", {"src/a.py": "A = 1\n"}, paths="src/a.py")
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))
        plan = self.ok("cleanup", "--dry-run")
        self.assertEqual(plan["A"]["cleaned"], False)
        self.assertTrue(os.path.samefile(plan["A"]["would_remove"]["worktree"], wa))  # Windows 짧은 이름(RUNNER~1)과 긴 이름을 같게 본다
        self.assertIn("task-A", plan["A"]["would_remove"]["branches"])
        self.assertTrue(wa.is_dir())  # 아무것도 지우지 않는다
        self.assertNotEqual(self.repo.git("branch", "--list", "task-A"), "")
        self.assertFalse(self.state("A")["cleaned"])
        self.assertEqual(self.ok("cleanup")["A"], {"cleaned": True})  # 그다음 실제 정리
        self.assertFalse(wa.exists())

    def test_closed_task_without_commit_does_not_crash(self):
        """e2e4: 문서만 남기고 닫은 작업(head 없음)이 있으면 인자 없는 cleanup이 traceback으로 멈췄다."""
        note = self.repo.path / "notes" / "ENV.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# 결과\n", encoding="utf-8")
        self.add("ENV", profile="research", paths="docs/env.md")
        self.ok("start", "ENV", "--pool", "research", "--agent", "r")
        self.ok("done", "ENV", "--close", "--notes", "notes/ENV.md")
        self.assertEqual(self.ok("cleanup", "--dry-run")["ENV"]["would_remove"], {"worktree": None, "branches": []})
        self.assertEqual(self.ok("cleanup")["ENV"], {"cleaned": True})
        self.assertTrue(self.state("ENV")["cleaned"])


class TestAwayMode(ModsCase):
    """4-5: 밤새 맡길 때 사용자가 미리 위임한 범위(화면·방향 결정, 원하면 미리보기)만 추천안으로 정한다."""

    def decide(self, did="D-1", kind="ux", tasks="UI", recommend="A"):
        args = ["decide", "add", "--id", did, "--title", "레이아웃", "--question", "어느 안?", "--tasks", tasks,
                "--option", "A: 카드", "--option", "B: 표", "--kind", kind, "--why", "모바일 비중"]
        if recommend:
            args += ["--recommend", recommend]
        return self.ok(*args)

    def test_adopt_only_while_away_and_for_delegated_kinds(self):
        self.add("UI", paths="ui/")
        self.decide()
        self.assertIn("자리 비움", self.refused("decide", "adopt", "D-1"))
        self.ok("away")
        self.assertEqual(self.snapshot()["away"]["previews"], False)
        d = self.ok("decide", "adopt", "D-1")
        self.assertEqual((d["state"], d["choice"], d["by"]), ("ANSWERED", "A", "away"))
        for kind in ("dependency", "breaking", "environment", "data", "limit", "other"):
            with self.subTest(kind=kind):
                self.decide(f"D-{kind}", kind=kind)
                self.assertIn("위임", self.refused("decide", "adopt", f"D-{kind}"))
        self.decide("D-none", recommend="")
        self.assertIn("추천", self.refused("decide", "adopt", "D-none"))
        self.assertIn("adopt", self.refused("decide", "answer", "D-none", "--choice", "A", "--by", "away"))

    def test_preview_auto_approval_needs_previews_flag(self):
        self.preview_ready()
        self.assertIn("자리 비움", self.refused("approve", "UI", "--by", "away"))
        self.ok("away")
        self.assertIn("--previews", self.refused("approve", "UI", "--by", "away"))
        self.ok("away", "--previews")
        self.assertEqual(self.ok("approve", "UI", "--by", "away")["state"], "READY")
        self.assertEqual(self.state("UI")["preview"]["approval"]["by"], "away")

    def test_back_lists_what_was_decided_and_user_can_keep_or_redo(self):
        self.preview_ready()
        self.add("API", paths="api/")
        self.decide("D-1")
        self.decide("D-2", tasks="API")
        self.ok("away", "--previews")
        self.ok("decide", "adopt", "D-1")
        self.ok("decide", "adopt", "D-2")
        self.ok("approve", "UI", "--by", "away")
        back = self.ok("back")
        self.assertEqual(sorted(x["key"] for x in back["auto"]), ["D-1", "D-2", "UI"])
        snap = self.snapshot()
        self.assertIsNone(snap["away"])
        self.assertEqual(len([x for x in snap["away_log"] if x["reviewed"] is None]), 3)
        self.ok("away-review", "D-1", "--keep")
        out = self.ok("away-review", "D-2", "--redo")
        self.assertEqual(out["reopened"], "D-2-r1")
        new = next(x for x in self.snapshot()["decisions"] if x["id"] == "D-2-r1")
        self.assertEqual((new["state"], new["tasks"], [o["key"] for o in new["options"]]), ("OPEN", ["API"], ["A", "B"]))
        self.assertIn("D-2-r1", self.refused("start", "API", "--pool", "implement", "--agent", "i"))
        self.assertIn("미리보기", self.refused("away-review", "UI", "--redo"))
        self.ok("away-review", "UI", "--keep")
        self.assertIn("이미", self.refused("away-review", "D-1", "--keep"))
        self.assertEqual([x for x in self.snapshot()["away_log"] if x["reviewed"] is None], [])


class TestSettingsWarning(ModsCase):
    """e2e4 N1: 하네스 settings.json 없이(Hook·push 거부 없이) 끝까지 돌았다. status가 알려야 한다."""

    def test_status_warns_when_harness_hooks_missing(self):
        self.assertNotIn("asterbraid.py check", self.ok("status"))
        (self.repo.path / ".claude/settings.json").write_text('{"permissions": {"allow": []}}', encoding="utf-8")
        self.assertIn("asterbraid.py check", self.ok("status"))
        self.assertTrue(self.ok("status", "--json")["warnings"])


class TestProjectMap(ModsCase):
    """시작 준비 9: 기존 프로젝트의 지도는 researcher 작업(MAP)으로 만들고 노트와 함께 닫는다."""

    def test_bootstrap_map_command_runs(self):
        skill = (Path(base.__file__).resolve().parent.parent / MODS / ".claude/skills/asterbraid-bootstrap/SKILL.md").read_text(encoding="utf-8")
        cmd = '--id MAP --title "프로젝트 지도" --profile research --risk low --paths docs/asterbraid/research/ --criteria "구조 지도 문서"'
        self.assertIn(cmd, skill)
        self.ok("add", "--id", "MAP", "--title", "프로젝트 지도", "--profile", "research", "--risk", "low",
                "--paths", "docs/asterbraid/research/", "--criteria", "구조 지도 문서")
        self.add("A", paths="src/")
        self.assertEqual([t["id"] for t in self.ok("next", "--pool", "implement")["dispatch"]], ["A"])  # 지도를 기다리지 않는다
        self.ok("start", "MAP", "--pool", "research", "--agent", "researcher")
        rel = "docs/asterbraid/research/2026-11-26-0100-MAP.md"
        (self.repo.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.repo.path / rel).write_text("색인이다. 판단은 실제 코드로 한다\n", encoding="utf-8")
        self.assertEqual(self.ok("done", "MAP", "--close", "--notes", rel)["state"], "COMPLETED")


class TestTaskTiming(ModsCase):
    """4-12: Mod가 완료 작업의 걸린 시간을 보이도록 스냅샷에 첫 배정 시각을 둔다."""

    def test_snapshot_has_first_start(self):
        self.add("A")
        self.assertIsNone(self.task(self.snapshot(), "A")["started"])
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        first = self.state("A")["history"][-1]["at"]
        self.ok("fail", "A", "--reason", "다시")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        self.assertEqual(self.task(self.snapshot(), "A")["started"], first)


class TestPreviewContract(ModsCase):
    def test_approve_checks_version_and_hash(self):
        shas = self.preview_ready(versions=2)
        self.assertIn("일치하지 않는다", self.refused("approve", "UI", "--version", "2", "--sha", shas[0][:12]))
        self.assertIn("8자", self.refused("approve", "UI", "--version", "2", "--sha", shas[1][:4]))
        out = self.ok("approve", "UI", "--version", "2", "--sha", shas[1][:12], "--by", "tui")
        self.assertEqual(out["state"], "READY")
        approval = self.state("UI")["preview"]["approval"]
        self.assertEqual((approval["version"], approval["sha256"], approval["by"]), (2, shas[1], "tui"))
        self.assertEqual(approval["criteria_sha"], hashlib.sha256("테스트 통과".encode()).hexdigest()[:16])

    def test_only_latest_version_can_be_approved(self):
        shas = self.preview_ready(versions=2)  # v1은 수정 요청으로 거절됐다
        self.assertIn("최신", self.refused("approve", "UI", "--version", "1", "--sha", shas[0][:12]))
        self.assertEqual(self.state("UI")["state"], "AWAITING_APPROVAL")

    def test_only_html_previews_are_registered(self):
        self.add("UI", paths="ui/", preview=True)
        self.ok("start", "UI", "--pool", "preview", "--agent", "designer")
        for name in ("x.bat", "x.lnk", "x", "x.html.cmd"):
            with self.subTest(name=name):
                bad = self.tmp / name
                bad.write_text("echo hi", encoding="utf-8")
                self.assertIn(".html", self.refused("preview", "UI", "--file", str(bad)))
        self.assertEqual(self.state("UI")["preview"]["versions"], [])

    def test_tampered_preview_file_cannot_be_approved(self):
        self.preview_ready()
        Path(self.state("UI")["preview"]["versions"][0]["file"]).write_text("<h1>바뀜</h1>", encoding="utf-8")
        self.assertIn("등록 뒤 바뀌었다", self.refused("approve", "UI"))

    def test_revise_on_stale_version_is_refused(self):
        self.preview_ready(versions=2)
        self.assertIn("v2", self.refused("revise", "UI", "--version", "1", "--feedback", "옛 화면"))
        self.assertEqual(self.ok("revise", "UI", "--version", "2", "--feedback", "색")["state"], "PENDING")
        self.assertEqual(self.state("UI")["preview"]["feedback"][-1]["on"], 2)

    def test_dashboard_keeps_file_and_adds_review_tools(self):
        shas = self.preview_ready(versions=2)
        dash = Path(self.ok("dashboard")["dashboard"])
        self.assertEqual(dash.name, "index.html")
        page = dash.read_text(encoding="utf-8")
        for expected in ("Development Preview", "v1 수정", "UI/v1.html", "UI/v2.html", shas[1][:12],
                         "allow-scripts", "--version 2 --sha " + shas[1][:12]):
            self.assertIn(expected, page)
        self.assertNotIn("<script src", page)  # 외부 스크립트 없이 열린다
        self.assertNotIn("__AUTO_DATA__", page)

    def test_dashboard_ui_source_lives_in_claude_folder(self):
        self.add("A")
        template = self.repo.path / ".claude/previewUI/preview.html"
        self.assertIn("__AUTO_DATA__", template.read_text(encoding="utf-8"))
        template.write_text(template.read_text(encoding="utf-8").replace("Development Preview", "고친 화면"),
                            encoding="utf-8")
        self.assertIn("고친 화면", Path(self.ok("dashboard")["dashboard"]).read_text(encoding="utf-8"))

    def test_dashboard_data_cannot_break_out_of_script(self):
        self.ok("add", "--id", "X", "--title", "</script><img src=x onerror=alert(1)>", "--criteria", "c",
                "--paths", "x/")
        page = Path(self.ok("dashboard")["dashboard"]).read_text(encoding="utf-8")
        self.assertNotIn("<img src=x", page)
        self.assertEqual(page.count("</script>"), page.count("<script"))


class TestAnomalies(ModsCase):
    def age(self, tid, state, minutes):
        """테스트용: 상태 파일의 해당 상태 진입 시각을 과거로 옮긴다."""
        path = Path(self.repo.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "claude-auto/state.json"
        st = json.loads(path.read_text(encoding="utf-8"))
        from datetime import datetime, timedelta
        at = (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        t = st["tasks"][tid]
        t["created"] = min(t["created"], at)
        for h in t["history"]:
            if h["to"] == state:
                h["at"] = at
        path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

    def test_long_stage_needs_history_and_is_reported(self):
        for i in range(3):  # 구현 2분짜리 과거 기록 3개
            tid = f"P{i}"
            self.add(tid, paths=f"p{i}/")
            self.ok("start", tid, "--pool", "implement", "--agent", "i")
            self.age(tid, "IMPLEMENTING", 2)
            self.ok("fail", tid, "--reason", "끝")  # IMPLEMENTING 구간을 닫는다
        self.add("SLOW", paths="slow/")
        self.ok("start", "SLOW", "--pool", "implement", "--agent", "i")
        self.age("SLOW", "IMPLEMENTING", 30)
        snap = self.snapshot()
        hit = [a for a in snap["anomalies"] if a["task"] == "SLOW"]
        self.assertEqual((hit[0]["kind"], hit[0]["state"]), ("long-stage", "IMPLEMENTING"))
        self.assertIn("SLOW", self.ok("status"))

    def test_no_warning_without_enough_history(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        self.age("A", "IMPLEMENTING", 120)
        self.assertEqual(self.snapshot()["anomalies"], [])


class TestNotes(ModsCase):
    def test_manager_notes_reach_snapshot(self):
        self.add("A")
        self.ok("note", "--kind", "strategy", "--text", "API 먼저", "--task", "A")
        self.assertIn("모르는 kind", self.refused("note", "--kind", "gossip", "--text", "x"))
        note = self.snapshot()["notes"][-1]
        self.assertEqual((note["kind"], note["text"], note["task"]), ("strategy", "API 먼저", "A"))


class TestSnapshotFailure(ModsCase):
    def test_broken_snapshot_does_not_block_state_writes(self):
        self.add("A")
        path = Path(self.ok("snapshot")["snapshot"])
        path.unlink()
        path.mkdir()  # 스냅샷 자리를 디렉터리로 막아 기록을 실패시킨다
        self.add("B", paths="b/")
        self.assertEqual(sorted(t["id"] for t in self.ok("status", "--json")["tasks"]), ["A", "B"])
        err = json.loads((path.parent / "dashboard.error.json").read_text(encoding="utf-8"))
        self.assertIn("error", err)


class TestModManifest(unittest.TestCase):
    """Mod 파일끼리의 약속. Mod 자체 동작은 `claude plugin test`가 검사한다."""
    MOD = Path(base.__file__).resolve().parent.parent / MODS / ".claude/skills/asterbraid-dashboard"

    def test_settings_enable_the_mod_by_its_manifest_name(self):
        name = json.loads((self.MOD / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["name"]
        settings = json.loads((self.MOD.parent.parent / "settings.json").read_text(encoding="utf-8"))
        self.assertIs(settings["enabledPlugins"].get(f"{name}@skills-dir"), True)
        hooks = json.loads((self.MOD / "hooks/hooks.json").read_text(encoding="utf-8"))
        for module in hooks["modules"]:
            self.assertTrue((self.MOD / "hooks" / module).is_file(), module)

    def test_every_user_option_is_read(self):
        options = set(json.loads((self.MOD / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["userConfig"])
        core = (self.MOD / "hooks/core.ts").read_text(encoding="utf-8")
        parse = core[core.index("export const parseConfig"):]
        self.assertEqual(options, set(re.findall(r"\bo\.(\w+)", parse)))


class TestAgentSkills(unittest.TestCase):
    """에이전트가 미리 불러오는 스킬은 템플릿 안에 있어야 한다(없으면 에이전트가 읽었다고 착각한다)."""
    CLAUDE = Path(base.__file__).resolve().parent.parent / MODS / ".claude"

    def frontmatter(self, path):
        text = path.read_text(encoding="utf-8")
        return text.split("---", 2)[1]

    def test_preloaded_skills_exist_and_agents_can_call_skill(self):
        seen = {}
        for agent in sorted((self.CLAUDE / "agents").glob("*.md")):
            fm = self.frontmatter(agent)
            skills = re.findall(r"^\s+-\s+([\w-]+)\s*$", fm.split("skills:", 1)[1], re.M) if "skills:" in fm else []
            for name in skills:
                skill = self.CLAUDE / "skills" / name / "SKILL.md"
                self.assertTrue(skill.is_file(), f"{agent.name}: {name}")
                self.assertIn(f"name: {name}", self.frontmatter(skill))
                self.assertIn("Skill", re.search(r"^tools:(.*)$", fm, re.M).group(1), agent.name)
            seen[agent.stem] = skills
        self.assertEqual(seen["architect"], ["architecture-design"])
        self.assertEqual(seen["designer"], ["preview-workflow"])

    def test_every_agent_can_call_skill(self):
        """사용자가 설치한 스킬(playwright-cli 등)을 어느 역할이든 쓸 수 있어야 한다."""
        agents = sorted((self.CLAUDE / "agents").glob("*.md"))
        self.assertEqual(len(agents), 6)
        for agent in agents:
            tools = re.search(r"^tools:(.*)$", self.frontmatter(agent), re.M).group(1)
            self.assertIn("Skill", [t.strip() for t in tools.split(",")], agent.name)

    def test_ai_docs_folder_is_committed_and_skips_tests(self):
        """AI 문서는 docs/asterbraid/에 커밋하고(worktree에서 보이게), 문서만 바뀌면 테스트를 돌리지 않는다."""
        root = self.CLAUDE.parent
        rules = (self.CLAUDE / "asterbraid.md").read_text(encoding="utf-8")  # 하네스 규칙(CLAUDE.md가 가져온다)
        self.assertIn("@.claude/asterbraid.md", (root / "CLAUDE.md").read_text(encoding="utf-8"))
        for folder in ("requirements/", "research/", "design/", "reports/"):
            self.assertIn(f"`{folder}`", rules)
        for agent, folder in (("manager", "requirements/"), ("researcher", "research/"), ("architect", "design/")):
            self.assertIn(f"docs/asterbraid/{folder}", (self.CLAUDE / "agents" / f"{agent}.md").read_text(encoding="utf-8"), agent)
        cfg = json.loads((self.CLAUDE / "hooks/verify.config.json").read_text(encoding="utf-8"))
        self.assertTrue(any("docs/asterbraid/x.md".startswith(p) for p in cfg["ignored_prefixes"]))
        gitignore = root / ".gitignore"
        self.assertFalse(gitignore.exists() and "docs" in gitignore.read_text(encoding="utf-8"))

    def test_skill_references_resolve(self):
        for skill in (self.CLAUDE / "skills").glob("*/SKILL.md"):
            text = skill.read_text(encoding="utf-8")
            for ref in re.findall(r"`(references/[\w./-]+)`", text):
                self.assertTrue((skill.parent / ref).is_file(), f"{skill.parent.name}: {ref}")


class TestBootstrap(unittest.TestCase):
    """manager의 '시작 준비' 절차가 프로그램과 맞는지: 템플릿 + 설계 문서만 있는 저장소에서 시작한다."""
    TEMPLATE = Path(base.__file__).resolve().parent.parent / MODS

    def bootstrap_text(self):
        """시작 준비 절차는 asterbraid-bootstrap 스킬에 있고, manager.md는 그 스킬을 가리킨다."""
        manager = (self.TEMPLATE / ".claude/agents/manager.md").read_text(encoding="utf-8")
        self.assertIn("asterbraid-bootstrap", manager)
        self.assertIn("Skill", manager.split("---", 2)[1])
        return (self.TEMPLATE / ".claude/skills/asterbraid-bootstrap/SKILL.md").read_text(encoding="utf-8")

    def test_fresh_repo_with_only_template_reaches_init(self):
        import shutil
        import tempfile
        from helpers import rmtree
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree, tmp)
        repo = tmp / "새 프로젝트"
        repo.mkdir()
        git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8")
        sched = lambda *a: subprocess.run([sys.executable, str(repo / ".claude/hooks/sched.py"), *a], cwd=str(repo),
                                          capture_output=True, text=True, encoding="utf-8")
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@e")
        git("config", "user.name", "t")
        shutil.copytree(self.TEMPLATE / ".claude", repo / ".claude")
        shutil.copy(self.TEMPLATE / "CLAUDE.md", repo / "CLAUDE.md")
        (repo / "docs").mkdir()
        (repo / "docs/design.md").write_text("# 설계\n", encoding="utf-8")
        self.assertEqual(sched("status").returncode, 0)  # 커밋이 없어도 상태를 본다
        self.assertEqual(sched("snapshot").returncode, 0)  # Mod가 시작 준비 전에 빈 화면을 그린다
        self.assertEqual(sched("away", "--previews").returncode, 0)  # 시작 준비 전에도 자리 비움을 켤 수 있다
        self.assertIn("TODO", (repo / "CLAUDE.md").read_text(encoding="utf-8"))  # manager가 준비가 필요하다고 판단하는 근거
        self.assertNotEqual(sched("init").returncode, 0)  # main은 통합 브랜치가 될 수 없다
        # 지침 순서: 커밋하기 전에 integration으로 바꾼다(main에는 커밋하지 않는다)
        self.assertEqual(git("switch", "-qc", "integration").returncode, 0)
        git("add", "-A")
        self.assertEqual(git("commit", "-qm", "chore: 하네스 시작 준비").returncode, 0)
        self.assertNotEqual(git("rev-parse", "--verify", "-q", "main").returncode, 0)  # main은 비어 있다
        r = sched("init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("자리 비움", sched("status").stdout)  # init이 자리 비움을 지우지 않는다
        self.assertEqual(json.loads(r.stdout)["integration_branch"], "integration")
        manager = self.bootstrap_text()
        self.assertLess(manager.index("커밋하기 전에** `git switch -c integration`"), manager.index("커밋한다(`chore: 하네스 시작 준비`)"))

    def test_gitignore_lines_cover_paths_the_harness_creates(self):
        manager = self.bootstrap_text()
        integrate = (self.TEMPLATE / ".claude/hooks/integrate.py").read_text(encoding="utf-8")
        self.assertIn('root / ".claude" / "worktrees"', integrate)
        for line in (".claude/worktrees/", ".claude/settings.local.json"):
            self.assertIn(f"`{line}`", manager)

    def test_every_verify_config_key_is_explained_to_manager(self):
        manager = self.bootstrap_text()
        keys = set(json.loads((self.TEMPLATE / ".claude/hooks/verify.config.json").read_text(encoding="utf-8"))) - {"_comment"}
        self.assertEqual(sorted(k for k in keys if f"`{k}`" not in manager), [])


class TestInstaller(unittest.TestCase):
    """N10: 사용자가 실행하는 설치·업데이트 프로그램. 에이전트가 자기 권한 설정을 고치지 않게 한다(e2e4 N1)."""
    TEMPLATE = Path(base.__file__).resolve().parent.parent / MODS

    def setUp(self):
        import tempfile
        from helpers import rmtree
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree, self.tmp)
        self.target = self.tmp / "프로젝트"
        self.target.mkdir()

    def run_tool(self, *args, template=None):
        script = (template or self.TEMPLATE) / "asterbraid.py"
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL)  # 터미널이 아니면 묻지 않는다

    def ok(self, *args, **kw):
        r = self.run_tool(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def settings(self):
        return json.loads((self.target / ".claude/settings.json").read_text(encoding="utf-8"))

    def test_fresh_install_dry_run_then_apply_then_check(self):
        plan = self.ok("install", str(self.target))
        self.assertIn("--yes", plan)  # 물을 수 없으면 보여 주기만 하고 방법을 알린다
        self.assertNotIn("적용할까", plan)
        self.assertEqual(list(self.target.iterdir()), [])  # 미리보기만
        self.assertIn("세션을 다시", self.ok("install", str(self.target), "--yes"))
        for rel in (".claude/agents/manager.md", ".claude/hooks/sched.py", ".claude/asterbraid.md",
                    ".claude/skills/asterbraid-dashboard/.claude-plugin/plugin.json", ".claude/hooks/verify.config.json",
                    "CLAUDE.md", ".claude/asterbraid.lock.json"):
            self.assertTrue((self.target / rel).is_file(), rel)
        self.assertEqual(self.settings(), json.loads((self.TEMPLATE / ".claude/settings.json").read_text(encoding="utf-8")))
        self.assertIn(".playwright-cli/", (self.target / ".gitignore").read_text(encoding="utf-8"))
        self.ok("check", str(self.target))
        self.assertIn("update", self.run_tool("install", str(self.target)).stderr)  # 두 번 설치하지 않는다

    def test_existing_project_keeps_its_files_and_merges_settings(self):
        (self.target / "CLAUDE.md").write_text("# 내 프로젝트\n\n- 내 규칙\n", encoding="utf-8")
        (self.target / ".gitignore").write_text("node_modules/\n.claude/worktrees/", encoding="utf-8")
        (self.target / ".claude/agents").mkdir(parents=True)
        (self.target / ".claude/agents/manager.md").write_text("내 manager\n", encoding="utf-8")
        mine = {"permissions": {"allow": ["Bash(npm test)"], "deny": ["Read(secret/**)"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}, "env": {"X": "1"}}
        (self.target / ".claude/settings.json").write_text(json.dumps(mine), encoding="utf-8")
        self.ok("install", str(self.target), "--apply")
        claude = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude.startswith("# 내 프로젝트\n\n- 내 규칙\n"))
        self.assertEqual(claude.count("@.claude/asterbraid.md"), 1)
        self.assertEqual((self.target / ".claude/agents/manager.md").read_text(encoding="utf-8"), "내 manager\n")
        s = self.settings()
        self.assertEqual(s["permissions"]["allow"][0], "Bash(npm test)")
        self.assertIn("Bash(git push *)", s["permissions"]["deny"])
        self.assertIn("Read(secret/**)", s["permissions"]["deny"])
        self.assertEqual(len(s["hooks"]["Stop"]), 2)
        self.assertEqual(s["env"]["X"], "1")
        lines = (self.target / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines.count(".claude/worktrees/"), 1)
        self.assertIn(".claude/settings.local.json", lines)
        r = self.run_tool("check", str(self.target))
        self.assertNotEqual(r.returncode, 0)  # manager.md가 하네스 것이 아니다
        self.assertIn("manager.md", r.stdout)
        # 다시 돌려도 아무것도 늘지 않는다
        before = {p: p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        self.ok("update", str(self.target), "--apply")
        self.assertEqual({p: p.read_bytes() for p in self.target.rglob("*") if p.is_file()}, before)

    def test_update_replaces_only_untouched_harness_files(self):
        import shutil
        tpl = self.tmp / "tpl"
        shutil.copytree(self.TEMPLATE, tpl)
        self.ok("install", str(self.target), "--apply", template=tpl)
        (self.target / ".claude/agents/verifier.md").write_text("내가 고침\n", encoding="utf-8")
        (self.target / ".claude/hooks/verify.config.json").write_text("{}", encoding="utf-8")
        for rel in (".claude/agents/manager.md", ".claude/agents/verifier.md", ".claude/hooks/verify.config.json"):
            with (tpl / rel).open("a", encoding="utf-8") as fh:
                fh.write("\n새 버전\n")
        (tpl / ".claude/agents/researcher.md").unlink()
        plan = self.ok("update", str(self.target), template=tpl)
        self.assertIn("verifier.md", plan)
        self.ok("update", str(self.target), "--apply", template=tpl)
        self.assertTrue((self.target / ".claude/agents/manager.md").read_text(encoding="utf-8").endswith("새 버전\n"))
        self.assertEqual((self.target / ".claude/agents/verifier.md").read_text(encoding="utf-8"), "내가 고침\n")
        self.assertEqual((self.target / ".claude/hooks/verify.config.json").read_text(encoding="utf-8"), "{}")  # 프로젝트 설정
        self.assertFalse((self.target / ".claude/agents/researcher.md").exists())

    def test_refuses_template_itself(self):
        self.assertNotEqual(self.run_tool("install", str(self.TEMPLATE)).returncode, 0)
        self.assertNotEqual(self.run_tool("update", str(self.target)).returncode, 0)  # 설치 전


class TestVerifierAssignment(ModsCase):
    """e2e5 E5-2·5-16·E5-6: 한 verifier가 이름을 바꿔 검증 2명을 채웠고, 같은 작업에 verifier가 3명 떴다."""

    def submitted(self, tid="A", **kw):
        self.add(tid, **kw)
        self.ok("start", tid, "--pool", "implement", "--agent", "impl")
        wt = self.worktree(tid, {"src/a.py": "A = 1\n"})
        self.ok("submit", tid, cwd=wt)
        return self.repo.git("rev-parse", "HEAD", cwd=wt)

    def test_only_assigned_verifiers_count(self):
        sha = self.submitted(risk="high", profile="standard")  # verifier 2명
        self.assertEqual(self.ok("next", "--pool", "verify")["dispatch"][0]["verifiers_needed"], 2)
        out = self.ok("start", "A", "--pool", "verify", "--agent", "v1")
        self.assertEqual(out["marker"], "[auto-task id=A pool=verify attempt=1]")
        self.assertEqual(self.ok("next", "--pool", "verify")["dispatch"][0]["verifiers_needed"], 1)  # 배정된 만큼 줄어든다
        self.assertIn("배정", self.refused("evidence", "A", "--verifier", "v2", "--verdict", "PASS", "--sha", sha))
        self.ok("evidence", "A", "--verifier", "v1", "--verdict", "PASS", "--sha", sha)
        self.ok("start", "A", "--pool", "verify", "--agent", "v2")
        self.assertIn("이미", self.refused("start", "A", "--pool", "verify", "--agent", "v3"))  # 필요한 수를 넘겨 띄우지 않는다
        self.ok("start", "A", "--pool", "verify", "--agent", "v3", "--replace", "v2")  # 멈춘 verifier는 바꿔 띄운다
        self.assertIn("배정", self.refused("evidence", "A", "--verifier", "v2", "--verdict", "PASS", "--sha", sha))
        self.assertEqual(self.ok("evidence", "A", "--verifier", "v3", "--verdict", "PASS", "--sha", sha)["state"],
                         "READY_TO_MERGE")

    def test_verify_start_needs_verifying_state(self):
        self.add("A")
        self.assertIn("VERIFYING", self.refused("start", "A", "--pool", "verify", "--agent", "v1"))

    def test_resubmit_needs_new_assignment(self):
        sha = self.submitted()
        self.ok("start", "A", "--pool", "verify", "--agent", "v1")
        self.ok("evidence", "A", "--verifier", "v1", "--verdict", "FAIL", "--sha", sha, "--notes", "결함")
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        wt = self.tmp / "wt-A"
        self.commit(wt, {"src/a.py": "A = 2\n"}, "fix")
        self.ok("submit", "A", cwd=wt)
        sha2 = self.repo.git("rev-parse", "HEAD", cwd=wt)
        self.assertIn("배정", self.refused("evidence", "A", "--verifier", "v1", "--verdict", "PASS", "--sha", sha2))
        self.assertEqual(self.ok("start", "A", "--pool", "verify", "--agent", "v1")["marker"],
                         "[auto-task id=A pool=verify attempt=2]")


class TestStartOnlyFromMain(ModsCase):
    """e2e5 E5-5: RETRY 재개 때 implementer가 worktree에서 직접 `sched start`를 했다. 배정은 manager만 한다."""

    def test_start_from_worktree_is_refused(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        wt = self.worktree("A", {"src/a.py": "A = 1\n"})
        self.ok("fail", "A", "--reason", "다시")
        self.assertIn("메인 체크아웃", self.refused("start", "A", "--pool", "implement", "--agent", "x", cwd=wt))
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")


class TestDecisionHolds(ModsCase):
    """e2e5 5-1·5-13·E5-8: 사용자가 정한다고 한 저장 방식이 자동 채택됐고, 데이터 결정 때문에 구현까지 멈췄다."""

    def decide(self, did="D-1", tasks="A", kind="architecture", *extra):
        return self.ok("decide", "add", "--id", did, "--title", "저장", "--question", "어디에?", "--tasks", tasks,
                       "--option", "A: 새 테이블", "--option", "B: 기존 표", "--kind", kind, "--recommend", "A",
                       "--why", "분리", *extra)

    def test_ask_user_refuses_adopt(self):
        self.add("A")
        self.decide("D-1", "A", "architecture", "--ask-user")
        self.ok("away")
        self.assertIn("사용자", self.refused("decide", "adopt", "D-1"))

    def test_hold_merge_lets_work_start_but_not_merge(self):
        self.ready_to_merge("A")
        self.add("B", paths="b/")
        self.decide("D-2", "A,B", "data", "--hold", "merge")
        self.assertIn("B", [t["id"] for t in self.ok("next", "--pool", "implement")["dispatch"]])  # 구현은 시작한다
        self.ok("start", "B", "--pool", "implement", "--agent", "i2")
        merge = self.ok("next", "--pool", "merge")
        self.assertEqual(merge["dispatch"], [])
        self.assertIn("D-2", merge["waiting"][0]["why"])
        code, status, out = self.integrate("A")
        self.assertEqual((code, status), (2, "REJECTED"))
        self.ok("decide", "answer", "D-2", "--choice", "A")
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))

    def test_hold_value_is_checked_and_shown(self):
        self.add("A")
        self.assertIn("invalid choice", self.refused("decide", "add", "--id", "D-1", "--title", "t", "--question", "q",
                                                     "--option", "A: a", "--option", "B: b", "--tasks", "A", "--hold", "x"))
        self.decide("D-1", "A", "data", "--hold", "merge", "--ask-user")
        d = next(x for x in self.snapshot()["decisions"] if x["id"] == "D-1")
        self.assertEqual((d["hold"], d["ask_user"]), ("merge", True))


class TestNextAttention(ModsCase):
    """e2e5 E5-1: manager가 `next --pool merge`만 491번 불러 RETRY를 65분 동안 몰랐다. 어느 next든 주의할 작업을 보인다."""

    def test_every_next_lists_retry_and_open_decisions(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        self.ok("fail", "A", "--reason", "verifier FAIL")
        self.add("B", paths="b/")
        self.ok("decide", "add", "--id", "D-1", "--title", "t", "--question", "q", "--tasks", "B",
                "--option", "A: a", "--option", "B: b")
        for pool in ("merge", "verify", "research", "implement"):
            with self.subTest(pool=pool):
                att = self.ok("next", "--pool", pool)["attention"]
                self.assertIn({"id": "A", "state": "RETRY", "why": "verifier FAIL"}, att)
                self.assertIn("D-1", " ".join(x["why"] for x in att))


class TestNotesDirInWorktree(ModsCase):
    """e2e5 5-12: worktree implementer가 공유 notes 폴더(worktree 밖)에 Write를 거부당해 작업이 멈췄다."""

    def test_notes_dir_is_inside_the_caller_checkout_and_ignored(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        wt = self.worktree("A", {"src/a.py": "A = 1\n"})
        d = Path(self.ok("notes-dir", "A", cwd=wt)["notes_dir"])
        self.assertTrue(os.path.samefile(d.parent.parent, wt))
        (d / "check.py").write_text("print(1)\n", encoding="utf-8")
        self.assertEqual(self.repo.git("status", "--porcelain", cwd=wt), "")  # 커밋 대상이 아니다
        main = Path(self.ok("notes-dir", "A")["notes_dir"])
        self.assertTrue(os.path.samefile(main.parent.parent, self.repo.path))
        self.assertEqual(self.repo.git("status", "--porcelain"), "")


class TestDesignReview(ModsCase):
    """5-19: 큰 설계는 다른 검토자가 PASS해야 구현을 배정한다. 끄거나(off) 매번 묻게(ask) 할 수 있다."""

    def designed(self, tid="A", depth="deep"):
        self.add(tid, risk="low", needs_design=depth)
        self.ok("start", tid, "--pool", "design", "--agent", "architect")
        return self.ok("done", tid, "--notes", f"docs/{tid}.md")

    def test_deep_design_waits_for_review(self):
        self.assertEqual(self.designed()["state"], "PENDING")
        self.assertEqual(self.ok("next", "--pool", "design")["dispatch"], [])  # architect에게 다시 주지 않는다
        rev = self.ok("next", "--pool", "review")["dispatch"]
        self.assertEqual([(t["id"], t["marker"]) for t in rev], [("A", "[auto-task id=A pool=review attempt=1]")])
        self.assertIn("구현을 시작할 수 없다", self.refused("start", "A", "--pool", "implement", "--agent", "i"))
        out = self.ok("design-review", "A", "--verifier", "rv", "--verdict", "FAIL", "--notes", "오류 계약 빠짐")
        self.assertEqual(out["state"], "PENDING")
        self.assertEqual([t["id"] for t in self.ok("next", "--pool", "design")["dispatch"]], ["A"])  # 다시 설계
        self.ok("start", "A", "--pool", "design", "--agent", "architect")
        self.ok("done", "A", "--notes", "docs/A.md")
        self.assertEqual(self.ok("design-review", "A", "--verifier", "rv", "--verdict", "PASS")["state"], "READY")
        self.assertEqual(self.task(self.snapshot(), "A")["design_review"]["verdict"], "PASS")

    def test_standard_design_skips_review_and_off_mode(self):
        self.assertEqual(self.designed("S", "standard")["state"], "READY")
        self.ok("review-mode", "off")
        self.assertEqual(self.designed("B")["state"], "READY")
        self.assertEqual(self.snapshot()["design_review_mode"], "off")

    def test_ask_mode_skip_needs_answered_decision(self):
        self.ok("review-mode", "ask")
        self.assertEqual(self.designed()["state"], "PENDING")
        self.ok("decide", "add", "--id", "D-R", "--title", "설계 검증", "--question", "받을까?", "--tasks", "A",
                "--option", "Y: 받는다", "--option", "N: 건너뛴다", "--kind", "direction", "--recommend", "Y")
        self.assertIn("D-R", self.refused("design-review", "A", "--skip", "--decision", "D-R"))
        self.ok("decide", "answer", "D-R", "--choice", "N")
        self.assertEqual(self.ok("design-review", "A", "--skip", "--decision", "D-R")["state"], "READY")
        self.assertIn("invalid choice", self.refused("review-mode", "sometimes"))


class TestHelpEncoding(ModsCase):
    """manager는 `sched -h`로 명령을 본다. Windows 콘솔 인코딩(cp949)으로 나오면 깨진 글자를 읽는다."""

    def test_help_is_utf8(self):
        p = self.script("sched.py", "-h", env={"PYTHONIOENCODING": "cp949"})
        self.assertEqual(p.returncode, 0)
        text = p.stdout.decode("utf-8")
        self.assertIn("설계 검토", text)
        self.assertIn("attention", text)


class TestGitTimeout(ModsCase):
    """5-20: 잠금을 쥔 채 git이 멈추면 120초 뒤 다른 프로세스가 잠금을 치우고 함께 쓴다. git에 시간 제한을 둔다."""

    def test_hung_git_is_refused_not_waited(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sched_t", self.repo.path / ".claude/hooks/sched.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        seen = {}

        def hung(cmd, **kw):  # git이 멈춘 것처럼: 시간 제한이 넘어오면 그 시간에 끊긴다
            seen.update(kw)
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

        from unittest import mock
        with mock.patch.object(mod.subprocess, "run", hung), self.assertRaises(mod.Refused) as cm:
            mod.run_git(self.repo.path, "status")
        self.assertEqual(seen.get("timeout"), mod.GIT_TIMEOUT_S)
        self.assertLess(mod.GIT_TIMEOUT_S, 120)  # 상태 잠금을 오래된 것으로 볼 시간보다 짧다
        self.assertIn("시간", str(cm.exception))


class TestAgentRules(unittest.TestCase):
    """지침을 줄여도 e2e에서 문제가 됐던 규칙이 빠지지 않게 지킨다(e2e5)."""
    CLAUDE = Path(base.__file__).resolve().parent.parent / MODS / ".claude"

    def text(self, rel):
        return (self.CLAUDE / rel).read_text(encoding="utf-8")

    def test_manager_keeps_core_rules_within_budget(self):
        m = self.text("agents/manager.md")
        for phrase in ("할 일이 없으면 차례를 끝낸다",  # E5-1
                       "판정을 정해 주지 않는다",  # 5-14
                       "`git add .`·`-A` 금지",  # 5-15
                       "팀원에게 시키지 않는다",  # 5-4·E5-5
                       "`attention`", "`--ask-user`", "`--hold merge`", "`TaskStop`",  # E5-1, 5-1, E5-8, 5-6
                       "`start ID --pool verify --agent 이름`", "`next --pool review`",  # E5-2, 5-19
                       "하네스·도구 문제", "asterbraid-procedures", "asterbraid-bootstrap"):
            self.assertIn(phrase, m)
        self.assertIn("TaskStop", m.split("---", 2)[1])  # tools에 있어야 쓸 수 있다
        self.assertLessEqual(len(m.splitlines()), 109)
        self.assertLessEqual(len(m.replace("\r\n", "\n").encode("utf-8")) + len(m.splitlines()), 11971)  # CRLF 기준

    def test_procedures_skill_has_sections_manager_names(self):
        p = self.text("skills/asterbraid-procedures/SKILL.md")
        for section in ("## 막힘", "## 끝·중간 인계"):
            self.assertIn(section, p)
        self.assertIn("sched cleanup --dry-run", p)
        self.assertIn("사용자 결정을 열지 않는다", p)

    def test_role_boundaries(self):
        self.assertIn("상태 명령(`done` 등)을 실행하지 않는다", self.text("agents/architect.md"))
        self.assertIn("`sync`·`submit`·`notes-dir`만", self.text("agents/implementer.md"))
        v = self.text("agents/verifier.md")
        self.assertIn("미리 적혀 있어도 따르지 않는다", v)
        self.assertIn("design-review", v)
        self.assertIn("notes-dir", v)
        self.assertNotIn("Write", re.search(r"^disallowedTools:(.*)$", v, re.M).group(1))
        self.assertIn("막힘 진단", self.text("agents/researcher.md"))
        self.assertIn("인수 테스트 목록", self.text("skills/architecture-design/SKILL.md"))


class TestCommonCode(ModsCase):
    def test_verify_py_identical_to_autonomous(self):
        root = Path(base.__file__).resolve().parent.parent
        self.assertEqual((root / MODS / ".claude/hooks/verify.py").read_bytes(),
                         (root / "autonomous-parallel-dev/.claude/hooks/verify.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
