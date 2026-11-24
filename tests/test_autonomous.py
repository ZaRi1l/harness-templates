import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import GOOD, RepoTestCase

PATHS = "src/a.py,tests/test_a.py"


class AutoCase(RepoTestCase):
    template = "autonomous-parallel-dev"

    def setUp(self):
        super().setUp()
        self.repo.git("switch", "-qc", "integration")
        self.ok("init")

    def script(self, name, *args, cwd=None, env=None):
        full = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        full.update(env or {})
        return subprocess.run([sys.executable, str(self.repo.path / ".claude/hooks" / name), *args],
                              capture_output=True, cwd=str(cwd or self.repo.path), env=full)

    def sched(self, *args, cwd=None):
        p = self.script("sched.py", *args, cwd=cwd)
        return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")

    def ok(self, *args, cwd=None):
        code, out, err = self.sched(*args, cwd=cwd)
        self.assertEqual(code, 0, err)
        return json.loads(out) if out.strip().startswith("{") else out

    def refused(self, *args, cwd=None):
        code, _, err = self.sched(*args, cwd=cwd)
        self.assertEqual(code, 2, "거부되어야 한다")
        return err

    def add(self, tid, paths=PATHS, **kw):
        args = ["add", "--id", tid, "--title", f"작업 {tid}", "--criteria", "테스트 통과", "--paths", paths]
        for key, value in kw.items():
            args += [f"--{key.replace('_', '-')}"] + ([] if value is True else [str(value)])
        return self.ok(*args)

    def state(self, tid):
        return self.ok("status", "--json")["tasks_by_id"][tid] if False else \
            next(t for t in self.ok("status", "--json")["tasks"] if t["id"] == tid)

    def worktree(self, tid, files, verify=True):
        """implementer 흉내: worktree에서 구현·커밋·테스트·제출."""
        wt = self.tmp / f"wt-{tid}"
        self.repo.git("worktree", "add", "-q", "-b", f"task-{tid}", str(wt), "integration")
        self.commit(wt, files, tid)
        if verify:
            self.assertEqual(self.repo.run("verify.py", args=("--target", str(wt))).returncode, 0)
        return wt

    def commit(self, wt, files, message):
        for rel, text in files.items():
            (wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (wt / rel).write_text(text, encoding="utf-8")
        self.repo.git("add", "-A", cwd=wt)
        self.repo.git("commit", "-qm", message, cwd=wt)

    def ready_to_merge(self, tid, files=None, paths=PATHS, **kw):
        self.add(tid, paths=paths, **kw)
        self.ok("start", tid, "--pool", "implement", "--agent", "impl")
        wt = self.worktree(tid, files or {"src/a.py": f"A = '{tid}'\n"})
        self.ok("submit", tid, cwd=wt)
        self.ok("evidence", tid, "--verifier", "v1", "--verdict", "PASS", "--sha", self.repo.git("rev-parse", "HEAD", cwd=wt))
        return wt

    def integrate(self, tid, env=None):
        p = self.script("integrate.py", tid, env=env)
        out = p.stdout.decode("utf-8")
        return p.returncode, out.split(":")[0].replace("[integrate] ", "").strip(), out

    def head(self, ref="integration"):
        return self.repo.git("rev-parse", ref)


class TestScheduler(AutoCase):
    def test_dependency_cycle_and_unknown_dep_rejected(self):
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps([
            {"id": "A", "title": "a", "criteria": "c", "paths": ["a/"], "deps": ["B"]},
            {"id": "B", "title": "b", "criteria": "c", "paths": ["b/"], "deps": ["A"]}]), encoding="utf-8")
        self.assertIn("순환", self.refused("load", str(plan)))
        self.assertEqual(self.ok("status", "--json")["tasks"], [])  # 일부만 들어가지 않는다
        self.assertIn("의존 작업이 없다", self.refused("add", "--id", "C", "--title", "c", "--criteria", "c",
                                                 "--paths", "c/", "--deps", "X"))
        self.add("A")
        self.assertIn("이미 있다", self.refused("add", "--id", "A", "--title", "a", "--criteria", "c", "--paths", "z/"))

    def test_duplicate_assignment_and_invalid_transitions_rejected(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "one")
        self.assertIn("IMPLEMENTING", self.refused("start", "A", "--pool", "implement", "--agent", "two"))
        self.assertIn("검증 증거를 받을 수 없다",
                      self.refused("evidence", "A", "--verifier", "v", "--verdict", "PASS", "--sha", self.head()))
        self.assertIn("조사·설계 중이 아니다", self.refused("done", "A"))
        self.assertEqual(self.integrate("A")[:2], (2, "REJECTED"))

    def test_dependency_path_conflict_and_slot_limits(self):
        self.add("A", paths="src/a/")
        self.add("B", paths="src/a/x.py")
        self.add("C", paths="src/c/", deps="A")
        self.add("D", paths="src/d/")
        self.add("E", paths="src/e/")
        self.add("F", paths="src/f/")
        nxt = self.ok("next", "--pool", "implement")
        self.assertEqual([t["id"] for t in nxt["dispatch"]], ["A", "D", "E"])  # 슬롯 3, B는 경로 겹침, C는 의존
        why = {w["id"]: w["why"] for w in nxt["waiting"]}
        self.assertIn("겹친다", why["B"])
        self.assertIn("선행 작업", why["C"])
        self.assertIn("슬롯", why["F"])
        self.ok("start", "A", "--pool", "implement", "--agent", "i1")
        self.assertIn("겹친다", self.refused("start", "B", "--pool", "implement", "--agent", "i2"))
        self.assertIn("선행 작업", self.refused("start", "C", "--pool", "implement", "--agent", "i2"))
        self.ok("start", "D", "--pool", "implement", "--agent", "i2")
        self.ok("start", "E", "--pool", "implement", "--agent", "i3")
        self.assertIn("슬롯", self.refused("start", "F", "--pool", "implement", "--agent", "i4"))
        self.ok("limit", "implement", "4")
        self.ok("start", "F", "--pool", "implement", "--agent", "i4")

    def test_profiles_gate_research_and_design(self):
        self.assertEqual(self.add("A", risk="high")["profile"], "deep")
        self.assertEqual(self.state("A")["state"], "PENDING")
        self.assertIn("구현을 시작할 수 없다", self.refused("start", "A", "--pool", "implement", "--agent", "i"))
        self.assertIn("설계 단계가 아니다", self.refused("start", "A", "--pool", "design", "--agent", "a"))
        self.ok("start", "A", "--pool", "research", "--agent", "r")
        self.assertEqual(self.ok("done", "A", "--notes", "n.md")["state"], "PENDING")
        self.ok("start", "A", "--pool", "design", "--agent", "a")
        self.assertEqual(self.ok("done", "A")["state"], "READY")
        self.assertEqual(self.add("B", paths="b/", risk="low")["state"], "READY")

    def test_concurrent_writes_keep_every_task(self):
        procs = [subprocess.Popen([sys.executable, str(self.repo.path / ".claude/hooks/sched.py"), "add", "--id", f"T{i}",
                                   "--title", "t", "--criteria", "c", "--paths", f"p{i}/"],
                                  cwd=str(self.repo.path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for i in range(8)]
        self.assertEqual([p.wait() for p in procs], [0] * 8)
        self.assertEqual(sorted(t["id"] for t in self.ok("status", "--json")["tasks"]), [f"T{i}" for i in range(8)])

    def test_submit_rejects_changes_outside_owned_paths(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        wt = self.worktree("A", {"src/a.py": "A = 1\n", "src/other.py": "X = 1\n"})
        self.assertIn("src/other.py", self.refused("submit", "A", cwd=wt))
        self.assertIn("메인 체크아웃", self.refused("submit", "A"))
        self.assertEqual(self.state("A")["state"], "IMPLEMENTING")

    def test_evidence_must_match_submitted_commit_and_fail_sends_to_retry(self):
        self.add("A", risk="high", profile="standard")  # high 위험: verifier 2명
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        wt = self.worktree("A", {"src/a.py": "A = 1\n"})
        self.ok("submit", "A", cwd=wt)
        sha = self.repo.git("rev-parse", "HEAD", cwd=wt)
        self.assertEqual(self.ok("evidence", "A", "--verifier", "v1", "--verdict", "PASS", "--sha", sha)["state"], "VERIFYING")
        self.ok("evidence", "A", "--verifier", "v1", "--verdict", "PASS", "--sha", sha)  # 같은 verifier는 한 번만 센다
        self.assertEqual(self.state("A")["state"], "VERIFYING")
        self.commit(wt, {"src/a.py": "A = 2\n"}, "later")
        self.assertIn("다르다", self.refused("evidence", "A", "--verifier", "v2", "--verdict", "PASS", "--sha", sha))
        self.repo.git("reset", "-q", "--hard", sha, cwd=wt)
        self.assertEqual(self.ok("evidence", "A", "--verifier", "v2", "--verdict", "PASS", "--sha", sha)["state"],
                         "READY_TO_MERGE")
        self.add("B", paths="b/")
        self.ok("start", "B", "--pool", "implement", "--agent", "i")
        wb = self.worktree("B", {"b/x.py": "X = 1\n"})
        self.ok("submit", "B", cwd=wb)
        out = self.ok("evidence", "B", "--verifier", "v1", "--verdict", "FAIL", "--sha", self.repo.git("rev-parse", "HEAD", cwd=wb))
        self.assertEqual(out["state"], "RETRY")

    def test_retry_limit_blocks_task(self):
        self.add("A")
        for _ in range(3):
            self.ok("start", "A", "--pool", "implement", "--agent", "i")
            self.assertEqual(self.ok("fail", "A", "--reason", "실패")["state"], "RETRY")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        self.assertEqual(self.ok("fail", "A", "--reason", "실패")["state"], "BLOCKED")

    def test_preview_gate_blocks_only_its_task(self):
        self.add("UI", paths="ui/", preview=True)
        self.add("API", paths="api/")
        self.assertEqual([t["id"] for t in self.ok("next", "--pool", "implement")["dispatch"]], ["API"])
        self.ok("start", "UI", "--pool", "preview", "--agent", "designer")
        page = self.tmp / "p.html"
        page.write_text("<h1>v1</h1>", encoding="utf-8")
        self.assertEqual(self.ok("preview", "UI", "--file", str(page))["version"], 1)
        self.assertEqual([t["id"] for t in self.ok("next", "--pool", "approval")["dispatch"]], ["UI"])
        self.assertEqual(self.ok("revise", "UI", "--feedback", "버튼을 크게")["state"], "PENDING")
        self.ok("start", "UI", "--pool", "preview", "--agent", "designer")
        page.write_text("<h1>v2</h1>", encoding="utf-8")
        v2 = self.ok("preview", "UI", "--file", str(page))
        self.assertEqual(v2["version"], 2)
        dash = Path(self.ok("dashboard")["dashboard"])
        self.assertIn("버튼을 크게", dash.read_text(encoding="utf-8"))
        self.assertEqual(self.ok("approve", "UI")["state"], "READY")
        t = self.state("UI")
        self.assertEqual(t["preview"]["approved"], 2)
        self.assertEqual(Path(t["preview"]["versions"][0]["file"]).read_text(encoding="utf-8"), "<h1>v1</h1>")
        self.assertEqual(self.repo.git("status", "--porcelain"), "")  # 미리보기가 작업 폴더를 더럽히지 않는다


class TestIntegration(AutoCase):
    def test_verified_task_is_merged_via_temp_worktree(self):
        main = self.head("main")
        wt = self.ready_to_merge("A")
        sha = self.repo.git("rev-parse", "HEAD", cwd=wt)
        code, status, out = self.integrate("A")
        self.assertEqual((code, status), (0, "MERGED"), out)
        self.assertEqual(self.state("A")["state"], "COMPLETED")
        self.assertEqual(self.repo.git("merge-base", "--is-ancestor", sha, "integration"), "")
        self.assertEqual(self.head("main"), main)
        self.assertEqual(self.repo.git("status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(list((self.repo.path / ".claude/worktrees").glob("_integrate-*")), [])
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))  # 다시 실행해도 같은 결과(멱등)
        self.assertEqual(self.repo.git("rev-list", "--count", "--merges", "integration"), "1")

    def test_missing_evidence_or_report_is_rejected(self):
        before = self.head()
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        wt = self.worktree("A", {"src/a.py": "A = 1\n"})
        self.ok("submit", "A", cwd=wt)
        self.assertEqual(self.integrate("A")[:2], (2, "REJECTED"))  # verifier 증거 없음
        sha = self.repo.git("rev-parse", "HEAD", cwd=wt)
        self.ok("evidence", "A", "--verifier", "v1", "--verdict", "PASS", "--sha", sha)
        report = Path(self.repo.git("rev-parse", "--path-format=absolute", "--git-path", "claude-verify-report.json", cwd=wt))
        saved = report.read_text(encoding="utf-8")
        report.write_text("{손상", encoding="utf-8")
        self.assertEqual(self.integrate("A")[:2], (2, "REJECTED"))
        report.unlink()
        self.assertEqual(self.integrate("A")[:2], (2, "REJECTED"))
        self.assertEqual(self.head(), before)
        report.write_text(saved, encoding="utf-8")
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))

    def test_commit_after_verification_is_rejected_and_sent_back(self):
        before = self.head()
        wt = self.ready_to_merge("A")
        self.commit(wt, {"src/a.py": "A = 'unverified'\n"}, "later")
        self.assertEqual(self.integrate("A")[:2], (2, "REJECTED"))
        self.assertEqual((self.head(), self.state("A")["state"]), (before, "RETRY"))

    def test_changed_criteria_invalidates_evidence(self):
        self.ready_to_merge("A")
        self.repo.config(timeout_s=200)  # 검증 설정이 바뀌면 이전 증거는 무효
        self.repo.git("commit", "-qam", "검증 설정 변경")
        self.assertEqual(self.integrate("A")[:2], (2, "REJECTED"))
        self.assertEqual(self.state("A")["state"], "VERIFYING")

    def test_conflict_goes_to_retry_and_other_tasks_continue(self):
        self.ready_to_merge("A", {"src/m.py": GOOD + "# from a\n"}, paths="src/m.py")
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))
        # B는 A가 통합되기 전의 기준에서 같은 파일을 고친 작업
        self.add("B", paths="src/m.py")
        self.add("C", paths="src/c.py")
        self.ok("start", "B", "--pool", "implement", "--agent", "i")
        wb = self.tmp / "wt-B"
        self.repo.git("worktree", "add", "-q", "-b", "task-B", str(wb), "main")
        self.commit(wb, {"src/m.py": GOOD + "# from b\n"}, "B")
        self.assertEqual(self.repo.run("verify.py", args=("--target", str(wb))).returncode, 0)
        self.ok("submit", "B", cwd=wb)
        self.ok("evidence", "B", "--verifier", "v1", "--verdict", "PASS", "--sha", self.repo.git("rev-parse", "HEAD", cwd=wb))
        before = self.head()
        self.assertEqual(self.integrate("B")[:2], (2, "CONFLICT"))
        self.assertEqual((self.head(), self.state("B")["state"]), (before, "RETRY"))
        self.assertEqual(self.repo.git("status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(self.ok("sync", "B", cwd=wb)["result"], "CONFLICT")  # implementer가 풀 충돌이 worktree에 생긴다
        self.assertEqual([t["id"] for t in self.ok("next", "--pool", "implement")["dispatch"]], ["B", "C"])

    def test_failing_combination_leaves_integration_untouched(self):
        self.ready_to_merge("A", {"src/flag.py": "ON = True\n"}, paths="src/flag.py")
        self.ready_to_merge("B", {"tests/test_b.py": "import os, unittest\n\nclass B(unittest.TestCase):\n"
                                  "    def test(self):\n        self.assertFalse(os.path.exists('src/flag.py'))\n"},
                            paths="tests/test_b.py")
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))
        before = self.head()
        self.assertEqual(self.integrate("B")[:2], (2, "FAIL"))
        self.assertEqual((self.head(), self.state("B")["state"]), (before, "RETRY"))
        self.assertEqual(self.ok("metrics")["integration_test_failures"], 1)

    def test_protected_branch_and_dirty_checkout_are_blocked(self):
        self.ready_to_merge("A")
        self.repo.write("src/m.py", GOOD + "# dirty\n")
        self.assertEqual(self.integrate("A")[:2], (2, "BLOCKED"))
        self.repo.git("checkout", "-q", "--", "src/m.py")
        self.repo.git("switch", "-q", "main")
        before = self.head("main")
        self.assertEqual(self.integrate("A")[:2], (2, "BLOCKED"))
        self.assertEqual(self.head("main"), before)
        self.assertIn("보호 브랜치", self.refused("init"))
        # 통합 브랜치를 정한 뒤에 보호 목록에 넣어도 병합하지 않는다
        self.repo.git("switch", "-q", "integration")
        cfg = self.repo.path / ".claude/hooks/auto.config.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["protected_branches"].append("integration")
        cfg.write_text(json.dumps(data), encoding="utf-8")
        self.repo.git("commit", "-qam", "protect")
        before = self.head()
        self.assertEqual(self.integrate("A")[:2], (2, "BLOCKED"))
        self.assertEqual(self.head(), before)

    def test_concurrent_integration_is_refused(self):
        self.ready_to_merge("A")
        lock = Path(self.repo.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "claude-auto/integrate.lock"
        lock.write_text("other", encoding="utf-8")
        before = self.head()
        self.assertEqual(self.integrate("A")[:2], (2, "BUSY"))
        self.assertEqual((self.head(), self.state("A")["state"]), (before, "READY_TO_MERGE"))
        lock.unlink()
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))

    def test_crash_before_publish_leaves_branch_untouched_and_recovers(self):
        self.ready_to_merge("A")
        before = self.head()
        code, _, _ = self.integrate("A", env={"CLAUDE_AUTO_FAULT": "after_temp_merge"})
        self.assertEqual(code, 9)  # 프로세스가 통합 도중 죽었다
        self.assertEqual((self.head(), self.state("A")["state"]), (before, "MERGING"))
        lock = Path(self.repo.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "claude-auto/integrate.lock"
        os.utime(str(lock), (1, 1))  # 죽은 프로세스의 잠금은 오래되면 치워진다
        actions = self.ok("recover", "--reset-running")["actions"]
        self.assertTrue(any("READY_TO_MERGE" in a for a in actions), actions)
        self.assertEqual(list((self.repo.path / ".claude/worktrees").glob("_integrate-*")), [])
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))

    def test_crash_after_publish_is_recovered_as_completed(self):
        wt = self.ready_to_merge("A")
        code, _, _ = self.integrate("A", env={"CLAUDE_AUTO_FAULT": "after_publish"})
        self.assertEqual(code, 9)
        self.assertEqual(self.state("A")["state"], "MERGING")  # 반영은 됐는데 상태 기록 전에 죽었다
        lock = Path(self.repo.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "claude-auto/integrate.lock"
        os.utime(str(lock), (1, 1))
        self.ok("recover")
        self.assertEqual(self.state("A")["state"], "COMPLETED")
        self.assertEqual(self.repo.git("rev-list", "--count", "--merges", "integration"), "1")
        self.assertEqual(self.integrate("A")[:2], (0, "MERGED"))
        self.assertEqual(self.repo.git("rev-list", "--count", "--merges", "integration"), "1")  # 두 번 병합하지 않는다
        self.assertTrue(wt.is_dir())

    def test_recover_resets_interrupted_work_without_losing_it(self):
        self.add("A")
        self.add("R", paths="r/", profile="research")
        self.ok("start", "A", "--pool", "implement", "--agent", "i")
        self.ok("start", "R", "--pool", "research", "--agent", "r")
        self.ok("recover", "--reset-running")
        a = self.state("A")
        self.assertEqual((a["state"], a["retries"], self.state("R")["state"]), ("RETRY", 0, "PENDING"))
        self.assertEqual([t["id"] for t in self.ok("next", "--pool", "implement")["dispatch"]], ["A"])

    def test_cleanup_removes_only_safe_worktrees(self):
        wa = self.ready_to_merge("A", {"src/a.py": "A = 1\n"}, paths="src/a.py")
        wb = self.ready_to_merge("B", {"src/b.py": "B = 1\n"}, paths="src/b.py")
        wc = self.ready_to_merge("C", {"src/c.py": "C = 1\n"}, paths="src/c.py")
        for tid in ("A", "B"):
            self.assertEqual(self.integrate(tid)[:2], (0, "MERGED"))
        (wb / "leftover.txt").write_text("x", encoding="utf-8")
        result = self.ok("cleanup")
        self.assertEqual(result["A"], {"cleaned": True})
        self.assertFalse(result["B"]["cleaned"])
        self.assertNotIn("C", result)  # 통합 안 된 작업은 대상이 아니다
        self.assertFalse(wa.exists())
        self.assertTrue(wb.is_dir() and wc.is_dir())
        self.assertEqual(self.repo.git("branch", "--list", "task-A"), "")
        self.assertNotEqual(self.repo.git("branch", "--list", "task-C"), "")
