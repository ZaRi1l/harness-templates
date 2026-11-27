"""명시적 로컬 마무리: 실제 임시 저장소에서만 브랜치를 움직인다."""

from test_asterbraid import ModsCase


class TestFinalize(ModsCase):
    def completed(self):
        self.ready_to_merge("A")
        code, status, out = self.integrate("A")
        self.assertEqual(code, 0, f"integrate failed: {status} - {out}")

    def finalize(self, *args):
        return self.script("finalize.py", "--target", "main", *args)

    def test_explicit_apply_fast_forwards_without_push(self):
        self.completed()
        # 존재하지 않는 remote를 등록해도 로컬 작업은 성공한다.
        self.repo.git("remote", "add", "origin", str(self.tmp / "absent.git"))
        before = self.head("main")
        p = self.finalize("--apply")
        self.assertEqual(p.returncode, 0, p.stdout.decode("utf-8", errors="replace") + p.stderr.decode("utf-8", errors="replace"))
        self.assertNotEqual(before, self.head("main"))
        self.assertEqual(self.head("main"), self.head())
        self.assertEqual(self.repo.git("branch", "--show-current"), "integration")
        self.assertEqual(self.repo.git("for-each-ref", "refs/remotes"), "")

    def test_default_is_preview_and_does_not_change_refs(self):
        self.completed()
        before = self.head("main")
        p = self.finalize()
        self.assertEqual(p.returncode, 0, p.stdout.decode("utf-8", errors="replace") + p.stderr.decode("utf-8", errors="replace"))
        self.assertIn("PREVIEW", p.stdout.decode("utf-8", errors="replace"))
        self.assertEqual(self.head("main"), before)

    def test_divergent_target_is_refused(self):
        self.completed()
        self.repo.git("switch", "main")
        self.commit(self.repo.path, {"diverged.txt": "main only"}, "diverge")
        self.repo.git("switch", "integration")
        before = self.head("main")
        self.assertEqual(self.finalize("--apply").returncode, 2)
        self.assertEqual(self.head("main"), before)

    def test_dirty_or_untracked_checkout_is_refused(self):
        self.completed()
        before = self.head("main")
        self.repo.write("unexpected.txt", "untracked")
        self.assertEqual(self.finalize("--apply").returncode, 2)
        self.assertEqual(self.head("main"), before)

    def test_pending_or_unverified_tasks_are_refused(self):
        self.add("A")
        before = self.head("main")
        self.assertEqual(self.finalize("--apply").returncode, 2)
        self.assertEqual(self.head("main"), before)

    def test_missing_verifier_evidence_after_completion_is_refused(self):
        self.completed()
        import json
        from pathlib import Path
        path = Path(self.repo.git("rev-parse", "--git-common-dir"))
        if not path.is_absolute():
            path = self.repo.path / path
        path = path / "claude-auto/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["tasks"]["A"]["evidence"] = []
        path.write_text(json.dumps(state), encoding="utf-8")
        before = self.head("main")
        self.assertEqual(self.finalize("--apply").returncode, 2)
        self.assertEqual(self.head("main"), before)

    def test_target_held_in_other_worktree_is_refused(self):
        self.completed()
        self.repo.git("worktree", "add", str(self.tmp / "main-wt"), "main")
        before = self.head("main")
        self.assertEqual(self.finalize("--apply").returncode, 2)
        self.assertEqual(self.head("main"), before)

    def test_failed_full_test_preserves_branches(self):
        self.completed()
        self.commit(self.repo.path, {"tests/test_fail.py": "raise RuntimeError('failure')\n"}, "break tests")
        before = self.head("main")
        self.assertEqual(self.finalize("--apply").returncode, 2)
        self.assertEqual(self.head("main"), before)
        self.assertEqual(self.repo.git("branch", "--show-current"), "integration")

    def test_yes_flag_applies_fast_forward(self):
        self.completed()
        before = self.head("main")
        p = self.finalize("--yes")
        self.assertEqual(p.returncode, 0, p.stdout.decode("utf-8", errors="replace") + p.stderr.decode("utf-8", errors="replace"))
        self.assertNotEqual(before, self.head("main"))
        self.assertEqual(self.head("main"), self.head())

    def test_missing_target_refused(self):
        p = self.script("finalize.py", "--apply")
        self.assertEqual(p.returncode, 2)
