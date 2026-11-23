from helpers import BAD, GOOD, RepoTestCase, stderr


class TestVerify(RepoTestCase):
    def test_no_relevant_changes_skips(self):
        r = self.assertStatus(self.repo.verify(), "SKIP", 0)
        self.assertIsNone(r["scope"])

    def test_ignored_paths_skip(self):
        self.repo.write("docs/a.txt", "x")
        self.repo.write("NOTES.md", "x")
        self.assertStatus(self.repo.verify(), "SKIP", 0)

    def test_pass_then_same_tree_is_cached(self):
        self.repo.write("src/m.py", GOOD + "# changed\n")
        self.assertEqual(self.assertStatus(self.repo.verify(), "PASS", 0)["scope"], "full")
        # 첫 실행이 만든 __pycache__/*.pyc가 캐시를 깨면 안 된다
        r = self.assertStatus(self.repo.verify(), "SKIP", 0)
        self.assertTrue(r.get("cached"))
        self.assertEqual(r["scope"], "full")

    def test_failing_tests_fail_and_stay_failed(self):
        self.repo.write("src/m.py", BAD)
        self.assertStatus(self.repo.verify(), "FAIL", 2)
        self.assertTrue(self.assertStatus(self.repo.verify(), "FAIL", 2).get("cached"))

    def test_loop_guard_lowers_exit_code_only(self):
        self.repo.write("src/m.py", BAD)
        proc = self.repo.hook("verify.py", "Stop", stop_hook_active=True)
        r = self.assertStatus(proc, "FAIL", 1)
        self.assertTrue(r["loop_guard_applied"])

    def test_missing_module_is_blocked(self):
        self.repo.write("src/m.py", "import nosuchmod\n" + GOOD)
        self.assertStatus(self.repo.verify(), "BLOCKED", 2)

    def test_committed_change_on_branch_is_tested(self):
        self.repo.git("switch", "-qc", "feat")
        self.repo.write("src/m.py", GOOD + "# changed\n")
        self.repo.git("commit", "-qam", "change")
        r = self.assertStatus(self.repo.verify(), "PASS", 0)
        self.assertEqual(r["committed_changes"], ["src/m.py"])

    def test_scoped_run_is_not_cached(self):
        self.repo.write("src/m.py", GOOD + "# changed\n")
        self.assertEqual(self.assertStatus(self.repo.verify("--only", "tests.test_m"), "PASS", 0)["scope"], "scoped")
        r = self.assertStatus(self.repo.verify(), "PASS", 0)
        self.assertFalse(r.get("cached"))

    def test_success_pattern_missing_fails(self):
        self.repo.config(success_pattern="NEVER_PRINTED")
        self.assertStatus(self.repo.verify("--force"), "FAIL", 2)

    def test_timeout_fails(self):
        self.repo.write("tests/test_slow.py",
                        "import time, unittest\nclass S(unittest.TestCase):\n    def test(self): time.sleep(10)\n")
        self.repo.config(timeout_s=1)
        r = self.assertStatus(self.repo.verify("--force"), "FAIL", 2)
        self.assertIn("exceeded", r["reason"])

    def test_preflight_failure_is_blocked(self):
        self.repo.config(preflight=[{"name": "tool", "command": ["{python}", "-c", "import sys; sys.exit(3)"]}])
        self.assertStatus(self.repo.verify("--force"), "BLOCKED", 2)

    def test_unknown_config_key_is_blocked(self):
        self.repo.config(tset_command=["x"])
        r = self.assertStatus(self.repo.verify("--force"), "BLOCKED", 2)
        self.assertIn("tset_command", r["reason"])

    def test_target_outside_git_is_blocked(self):
        outside = self.tmp / "not-git"
        outside.mkdir()
        proc = self.repo.run("verify.py", args=("--target", str(outside)))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BLOCKED", stderr(proc))

    def commit(self, text: str, message: str) -> None:
        self.repo.write("src/m.py", text)
        self.repo.git("commit", "-qam", message)

    def state(self) -> dict:
        import json
        from pathlib import Path
        path = self.repo.git("rev-parse", "--path-format=absolute", "--git-path", "claude-verify-state.json")
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_commit_on_main_after_pass_is_tested(self):
        # 회귀: main에서 직접 일하고 응답 전에 커밋하면 Stop Hook이 항상 "관련 변경 없음" SKIP이었다
        self.assertStatus(self.repo.verify("--force"), "PASS", 0)
        self.assertEqual(self.state()["last_pass_head"], self.repo.git("rev-parse", "HEAD"))
        self.assertStatus(self.repo.hook("verify.py", "Stop"), "SKIP", 0)
        self.commit(GOOD + "# changed\n", "change")
        r = self.assertStatus(self.repo.hook("verify.py", "Stop"), "PASS", 0)
        self.assertEqual((r["committed_changes"], r["scope"]), (["src/m.py"], "full"))

    def test_failing_commit_on_main_fails_and_keeps_last_pass(self):
        self.assertStatus(self.repo.verify("--force"), "PASS", 0)
        good_head = self.repo.git("rev-parse", "HEAD")
        self.commit(BAD, "broken")
        self.assertStatus(self.repo.hook("verify.py", "Stop"), "FAIL", 2)
        self.assertEqual(self.state()["last_pass_head"], good_head)
        self.commit(GOOD + "# fixed\n", "fix")
        self.assertStatus(self.repo.hook("verify.py", "Stop"), "PASS", 0)
        self.assertEqual(self.state()["last_pass_head"], self.repo.git("rev-parse", "HEAD"))

    def test_vanished_last_pass_head_is_ignored(self):
        self.commit(GOOD + "# temp\n", "temp")
        self.assertStatus(self.repo.verify("--force"), "PASS", 0)
        self.repo.git("reset", "-q", "--hard", "HEAD~1")
        self.repo.git("reflog", "expire", "--expire=now", "--all")
        self.repo.git("gc", "-q", "--prune=now")
        self.assertStatus(self.repo.hook("verify.py", "Stop"), "SKIP", 0)

    def test_same_size_same_mtime_edit_is_not_hidden_by_stale_pyc(self):
        # 회귀: 크기·수정 시각이 같은 편집은 이전 .pyc가 재사용되어 깨진 코드로도 PASS가 났다
        import os
        same_size_bad = GOOD.replace("a + b", "a - b")
        self.assertEqual(len(same_size_bad), len(GOOD))
        path = self.repo.path / "src/m.py"
        self.repo.write("src/m.py", GOOD + "# x\n")
        self.assertStatus(self.repo.verify(), "PASS", 0)
        st = path.stat()
        self.repo.write("src/m.py", same_size_bad + "# x\n")
        os.utime(path, (st.st_atime, st.st_mtime))
        self.assertStatus(self.repo.verify(), "FAIL", 2)

    def test_non_ascii_path_committed_on_branch_is_tested(self):
        # 회귀: git이 한글 경로를 "src/\355..." 로 감싸 출력해 관련 변경으로 인식하지 못했다
        self.repo.git("switch", "-qc", "feat")
        self.repo.write("src/한글.py", "X = 1\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-qm", "korean")
        r = self.assertStatus(self.repo.verify(), "PASS", 0)
        self.assertEqual(r["committed_changes"], ["src/한글.py"])

    def test_moving_file_out_of_relevant_path_is_tested(self):
        # 회귀: 이름 변경은 새 경로만 보여 src/에서 빠져나간 파일을 놓쳤다
        self.repo.write("src/extra.py", "X = 1\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-qm", "extra")
        self.repo.git("switch", "-qc", "feat")
        (self.repo.path / "docs").mkdir()
        self.repo.git("mv", "src/extra.py", "docs/extra.txt")
        self.assertIn("src/extra.py", self.assertStatus(self.repo.verify(), "PASS", 0)["uncommitted_changes"])
        self.repo.git("commit", "-qm", "move")
        self.assertIn("src/extra.py", self.assertStatus(self.repo.verify("--force"), "PASS", 0)["committed_changes"])

    def test_subagent_in_main_checkout_warns(self):
        self.repo.write("src/m.py", GOOD + "# changed\n")
        proc = self.repo.hook("verify.py", "SubagentStop", agent_type="implementer")
        r = self.assertStatus(proc, "PASS", 0)
        self.assertFalse(r["linked_worktree"])
        self.assertTrue(r["warnings"])

    def test_subagent_in_linked_worktree(self):
        wt = self.tmp / "wt"
        self.repo.git("worktree", "add", "-q", "-b", "feat", str(wt))
        (wt / "src/m.py").write_text(GOOD + "# changed\n", encoding="utf-8")
        self.repo.git("commit", "-qam", "change", cwd=wt)
        proc = self.repo.hook("verify.py", "SubagentStop", cwd=wt, agent_type="implementer")
        r = self.assertStatus(proc, "PASS", 0, cwd=wt)
        self.assertTrue(r["linked_worktree"])
        self.assertEqual((r["branch"], r["warnings"]), ("feat", []))
