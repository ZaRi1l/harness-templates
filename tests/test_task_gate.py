from helpers import BAD, GOOD, RepoTestCase, stderr

FILES = "담당 파일: src/m.py, tests/\n완료 기준: 테스트 통과"


class TestTaskGate(RepoTestCase):
    template = "team-shared-dev"

    def create(self, task_id: str, description: str):
        return self.repo.hook("task_gate.py", "TaskCreated", task_id=task_id, task_subject=f"작업 {task_id}",
                              task_description=description)

    def complete(self, task_id: str):
        return self.repo.hook("task_gate.py", "TaskCompleted", task_id=task_id, task_subject=f"작업 {task_id}")

    def test_missing_fields_rejected_with_utf8_message(self):
        proc = self.create("t1", "그냥 해줘")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("담당 파일:", stderr(proc))

    def test_overlapping_files_rejected(self):
        self.assertEqual(self.create("t1", "- " + FILES.replace("\n", "\n- ")).returncode, 0)
        proc = self.create("t2", "담당 파일: src/**\n완료 기준: x")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("t1", stderr(proc))
        self.assertEqual(self.create("t3", "담당 파일: docs/a.md\n완료 기준: x").returncode, 0)
        self.assertEqual(self.create("t4", "담당 파일: 없음\n완료 기준: 리뷰").returncode, 0)
        self.assertEqual(sorted(self.repo.locks()), ["t1", "t3"])

    def test_completion_blocked_while_tests_fail(self):
        self.create("t1", FILES)
        self.repo.write("src/m.py", BAD)
        self.assertEqual(self.complete("t1").returncode, 2)
        self.assertIn("t1", self.repo.locks())

    def test_completion_after_fix_releases_lock(self):
        self.create("t1", FILES)
        self.repo.write("src/m.py", GOOD + "# fixed\n")
        self.assertEqual(self.complete("t1").returncode, 0)
        self.assertEqual(self.repo.locks(), {})
        self.assertEqual(self.create("t2", "담당 파일: src/**\n완료 기준: x").returncode, 0)

    def test_owned_task_without_changes_still_runs_tests(self):
        self.create("t1", FILES)
        proc = self.complete("t1")
        self.assertEqual(proc.returncode, 0, stderr(proc))
        r = self.repo.report()
        self.assertEqual((r["status"], r["scope"]), ("PASS", "full"))

    def test_owned_task_cannot_complete_on_untested_skip(self):
        # 회귀: 실패하는 코드가 이미 커밋되어 "관련 변경 없음" SKIP이 나와도 완료되면 안 된다
        self.repo.write("src/m.py", BAD)
        self.repo.git("commit", "-qam", "broken")
        self.create("t1", FILES)
        self.assertEqual(self.complete("t1").returncode, 2)
        self.assertEqual(self.repo.report()["status"], "FAIL")
        self.assertIn("t1", self.repo.locks())

    def test_read_only_task_completes_without_tests(self):
        self.create("t1", "담당 파일: 없음\n완료 기준: 리뷰")
        self.assertEqual(self.complete("t1").returncode, 0)
        self.assertEqual(self.repo.report()["status"], "SKIP")
