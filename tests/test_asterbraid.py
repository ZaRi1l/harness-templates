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


class TestSchedulerRegression(base.TestScheduler):
    template = MODS


class TestIntegrationRegression(base.TestIntegration):
    template = MODS


class ModsCase(base.AutoCase):
    template = MODS

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
        rules = (root / "CLAUDE.md").read_text(encoding="utf-8")
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
        self.assertIn("TODO", (repo / "CLAUDE.md").read_text(encoding="utf-8"))  # manager가 준비가 필요하다고 판단하는 근거
        self.assertNotEqual(sched("init").returncode, 0)  # main은 통합 브랜치가 될 수 없다
        # 지침 순서: 커밋하기 전에 integration으로 바꾼다(main에는 커밋하지 않는다)
        self.assertEqual(git("switch", "-qc", "integration").returncode, 0)
        git("add", "-A")
        self.assertEqual(git("commit", "-qm", "chore: 하네스 시작 준비").returncode, 0)
        self.assertNotEqual(git("rev-parse", "--verify", "-q", "main").returncode, 0)  # main은 비어 있다
        r = sched("init")
        self.assertEqual(r.returncode, 0, r.stderr)
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


class TestCommonCode(ModsCase):
    def test_verify_py_identical_to_autonomous(self):
        root = Path(base.__file__).resolve().parent.parent
        self.assertEqual((root / MODS / ".claude/hooks/verify.py").read_bytes(),
                         (root / "autonomous-parallel-dev/.claude/hooks/verify.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
