"""Literal task ownership and guarded pre-implementation path correction."""

import json

import test_autonomous as base


class TaskPathsChecks:
    def raw_state(self):
        return (self.repo.path / ".git/claude-auto/state.json").read_bytes()

    def test_registration_rejects_globs_and_unsafe_paths_atomically(self):
        for path in ("src/*.py", "src/**", "src/?", "src/[ab]", "../other", "src/../other",
                     "/root", "C:/root", "\\\\server\\share", "src//a", "", "a,", "a/./b"):
            with self.subTest(path=path):
                before = self.raw_state()
                err = self.refused("add", "--id", "A", "--title", "a", "--criteria", "c", "--paths", path)
                if any(c in path for c in "*?["):
                    self.assertIn("glob", err)
                self.assertEqual(self.raw_state(), before)

    def test_load_rejects_invalid_later_spec_without_partial_registration(self):
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps([
            {"id": "A", "title": "a", "criteria": "c", "paths": ["src/a"]},
            {"id": "B", "title": "b", "criteria": "c", "paths": ["src/*.py"]},
        ]), encoding="utf-8")
        before = self.raw_state()
        self.assertIn("glob", self.refused("load", str(plan)))
        self.assertEqual(self.raw_state(), before)

    def test_safe_edit_preserves_identity_metadata_and_history(self):
        self.add("A", paths="old/", profile="research")
        before = self.state("A")
        out = self.ok("paths", "A", "--set", "./src/,tests/test_a.py", "--reason", "correct scope")
        after = self.state("A")
        self.assertEqual(out["paths"], ["src", "tests/test_a.py"])
        for key in before.keys() - {"paths", "history", "updated"}:
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["history"][:-1], before["history"])
        event = after["history"][-1]
        self.assertEqual(event["reason"], "correct scope")
        self.assertEqual(event["old_paths"], ["old"])
        self.assertEqual(event["paths"], after["paths"])
        self.assertEqual((event["from"], event["to"]), ("PENDING", "PENDING"))

    def test_blocked_preimplementation_edit_is_allowed(self):
        self.add("A", paths="old/")
        self.ok("block", "A", "--reason", "waiting")
        self.ok("paths", "A", "--set", "new/", "--reason", "scope")
        self.assertEqual(self.state("A")["state"], "BLOCKED")

    def test_invalid_edit_and_live_conflict_are_atomic(self):
        self.add("A", paths="old/")
        self.add("B", paths="busy/")
        self.ok("start", "B", "--pool", "implement", "--agent", "impl")
        for paths, reason in (("src/*.py", "scope"), ("../x", "scope"), ("busy/file.py", "scope"),
                              ("safe/", "  ")):
            with self.subTest(paths=paths, reason=reason):
                before = self.raw_state()
                self.refused("paths", "A", "--set", paths, "--reason", reason)
                self.assertEqual(self.raw_state(), before)

    def test_active_and_retried_implementation_cannot_widen_scope(self):
        self.add("A", paths="src/a.py")
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        for state in ("IMPLEMENTING", "RETRY", "BLOCKED"):
            if state == "RETRY":
                self.ok("fail", "A", "--reason", "failed")
            elif state == "BLOCKED":
                self.ok("block", "A", "--reason", "blocked")
            before = self.raw_state()
            self.refused("paths", "A", "--set", "src/", "--reason", "widen")
            self.assertEqual(self.raw_state(), before)

    def test_rejected_submit_cannot_be_bypassed_by_scope_edit(self):
        self.add("A", paths="src/a.py")
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        wt = self.worktree("A", {"src/other.py": "X = 1\n"})
        self.assertIn("src/other.py", self.refused("submit", "A", cwd=wt))
        self.refused("paths", "A", "--set", "src/", "--reason", "widen")
        self.assertIn("src/other.py", self.refused("submit", "A", cwd=wt))

    def test_submitted_task_cannot_change_scope(self):
        self.add("A")
        self.ok("start", "A", "--pool", "implement", "--agent", "impl")
        wt = self.worktree("A", {"src/a.py": "A = 1\n"})
        self.ok("submit", "A", cwd=wt)
        before = self.raw_state()
        self.refused("paths", "A", "--set", "src/", "--reason", "widen")
        self.assertEqual(self.raw_state(), before)

    def test_active_preliminary_and_terminal_states_reject_path_edit(self):
        # 1. Active research
        self.add("R", profile="research", paths="res/")
        self.ok("start", "R", "--pool", "research", "--agent", "r")
        self.refused("paths", "R", "--set", "res2/", "--reason", "active research")

        # 2. Active preview / review
        self.add("P", paths="ui/", preview=True)
        self.ok("start", "P", "--pool", "preview", "--agent", "designer")
        self.refused("paths", "P", "--set", "ui2/", "--reason", "active preview")

        # 3. Cancelled
        self.add("C", paths="c/")
        self.ok("cancel", "C", "--reason", "discard")
        self.refused("paths", "C", "--set", "c2/", "--reason", "cancelled")

    def test_paths_command_requires_reason_and_deduplicates_entries(self):
        self.add("A", paths="old/")
        self.refused("paths", "A", "--set", "new/")
        out = self.ok("paths", "A", "--set", "src/,src/,tests/test_a.py", "--reason", "dedup test")
        self.assertEqual(out["paths"], ["src", "tests/test_a.py"])


class TestAutonomousTaskPaths(TaskPathsChecks, base.AutoCase):
    pass


class TestAsterbraidTaskPaths(TaskPathsChecks, base.AutoCase):
    template = "asterbraid"
