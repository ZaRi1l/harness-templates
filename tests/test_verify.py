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
