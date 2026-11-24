"""autonomous-mods-dev: 기존 autonomous 테스트를 그대로 다시 돌리고, 확장한 스케줄러 기능을 검사한다."""

import hashlib
import json
import re
import unittest
from pathlib import Path

import test_autonomous as base

MODS = "autonomous-mods-dev"


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
    MOD = Path(base.__file__).resolve().parent.parent / MODS / ".claude/skills/autonomous-dashboard"

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


class TestCommonCode(ModsCase):
    def test_verify_py_identical_to_autonomous(self):
        root = Path(base.__file__).resolve().parent.parent
        self.assertEqual((root / MODS / ".claude/hooks/verify.py").read_bytes(),
                         (root / "autonomous-parallel-dev/.claude/hooks/verify.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
