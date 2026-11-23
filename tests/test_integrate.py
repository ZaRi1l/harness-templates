import json
from pathlib import Path

from helpers import GOOD, RepoTestCase, stderr


class TestIntegrate(RepoTestCase):
    template = "continuous-parallel-dev"

    def setUp(self):
        super().setUp()
        self.repo.git("switch", "-qc", "integration")

    def branch(self, name: str, files: dict, verify: bool = True) -> Path:
        wt = self.tmp / f"wt-{name}"
        self.repo.git("worktree", "add", "-q", "-b", name, str(wt), "integration")
        for rel, text in files.items():
            (wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (wt / rel).write_text(text, encoding="utf-8")
        self.repo.git("add", "-A", cwd=wt)
        self.repo.git("commit", "-qm", name, cwd=wt)
        if verify:
            proc = self.repo.run("verify.py", args=("--target", str(wt)))
            self.assertEqual(proc.returncode, 0, stderr(proc))
        return wt

    def integrate(self, branch: str):
        proc = self.repo.run("integrate.py", args=(branch,))
        log = Path(self.repo.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "claude-integration-log.jsonl"
        last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1]) if log.exists() else {}
        return proc.returncode, last.get("status"), proc.stdout.decode("utf-8")

    def head(self, ref: str = "HEAD") -> str:
        return self.repo.git("rev-parse", ref)

    def test_verified_branch_is_merged_and_main_untouched(self):
        main = self.head("main")
        self.branch("feat", {"src/m.py": GOOD + "# feat\n"})
        code, status, out = self.integrate("feat")
        self.assertEqual((code, status), (0, "MERGED"), out)
        self.assertEqual(self.repo.git("log", "-1", "--format=%P").count(" "), 1)  # --no-ff 병합 커밋
        self.assertEqual(self.head("main"), main)
        self.assertEqual(self.repo.report()["commit"], self.head())

    def test_refuses_protected_branch(self):
        self.branch("feat", {"src/m.py": GOOD + "# feat\n"})
        self.repo.git("switch", "-q", "main")
        before = self.head()
        self.assertEqual(self.integrate("feat")[:2], (2, "BLOCKED"))
        self.assertEqual(self.head(), before)

    def test_unverified_branch_is_rejected(self):
        before = self.head()
        self.branch("feat", {"src/m.py": GOOD + "# feat\n"}, verify=False)
        self.assertEqual(self.integrate("feat")[:2], (2, "REJECTED"))
        self.assertEqual(self.head(), before)

    def test_commit_after_verification_is_rejected(self):
        wt = self.branch("feat", {"src/m.py": GOOD + "# feat\n"})
        (wt / "src/m.py").write_text(GOOD + "# later\n", encoding="utf-8")
        self.repo.git("commit", "-qam", "later", cwd=wt)
        code, status, out = self.integrate("feat")
        self.assertEqual((code, status), (2, "REJECTED"))
        self.assertIn("HEAD", out)

    def test_dirty_main_checkout_is_blocked(self):
        self.branch("feat", {"src/m.py": GOOD + "# feat\n"})
        self.repo.write("src/m.py", GOOD + "# dirty\n")
        self.assertEqual(self.integrate("feat")[:2], (2, "BLOCKED"))

    def test_conflict_is_aborted_cleanly(self):
        self.branch("a", {"src/m.py": GOOD + "# from a\n"})
        self.branch("b", {"src/m.py": GOOD + "# from b\n"})
        self.assertEqual(self.integrate("a")[:2], (0, "MERGED"))
        before = self.head()
        code, status, out = self.integrate("b")
        self.assertEqual((code, status), (2, "CONFLICT"))
        self.assertIn("src/m.py", out)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.repo.git("status", "--porcelain", "--untracked-files=no"), "")

    def test_failing_combination_is_rolled_back(self):
        # 각각은 통과하지만 합치면 실패하는 두 작업(의미 충돌)
        self.branch("a", {"src/flag.py": "ON = True\n"})
        self.branch("b", {"tests/test_b.py": "import os, unittest\n\nclass B(unittest.TestCase):\n"
                                             "    def test(self):\n"
                                             "        self.assertFalse(os.path.exists('src/flag.py'))\n"})
        self.assertEqual(self.integrate("a")[:2], (0, "MERGED"))
        before = self.head()
        self.assertEqual(self.integrate("b")[:2], (2, "FAIL"))
        self.assertEqual(self.head(), before)
        self.assertEqual(self.repo.git("status", "--porcelain", "--untracked-files=no"), "")
