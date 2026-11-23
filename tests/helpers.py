"""Hook 테스트 도구: 템플릿의 .claude/를 넣은 임시 git 저장소를 만들고 Hook을 실제처럼 실행한다."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ["solo-guarded-dev", "verified-parallel-dev", "team-shared-dev", "team-hybrid-dev"]

GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"
SAMPLE_TEST = (
    "import sys, unittest\n"
    "sys.path.insert(0, 'src')\n"
    "import m\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(m.add(1, 2), 3)\n"
)


class Repo:
    def __init__(self, base: Path, template: str):
        self.path = base / "한글 폴더"  # 비ASCII·공백 경로에서도 동작해야 한다
        self.path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        self.git("config", "core.autocrlf", "false")
        shutil.copytree(ROOT / template / ".claude", self.path / ".claude")
        self.write("src/m.py", GOOD)
        self.write("tests/__init__.py", "")
        self.write("tests/test_m.py", SAMPLE_TEST)
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", "-C", str(cwd or self.path), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    def write(self, rel: str, text: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def config(self, **changes) -> None:
        p = self.path / ".claude/hooks/verify.config.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data.update(changes)
        p.write_text(json.dumps(data), encoding="utf-8")

    def run(self, script: str, payload: dict | None = None, args: tuple = ()) -> subprocess.CompletedProcess:
        """Hook처럼 실행한다: 입력은 UTF-8 JSON, 인코딩 관련 환경 변수 없음, 출력은 바이트."""
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("VERIFY_") and k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
        return subprocess.run([sys.executable, str(self.path / ".claude/hooks" / script), *args],
                              input=data, capture_output=True, cwd=self.path, env=env)

    def verify(self, *args: str) -> subprocess.CompletedProcess:
        return self.run("verify.py", args=("--target", str(self.path), *args))

    def hook(self, script: str, event: str, cwd: Path | None = None, **fields) -> subprocess.CompletedProcess:
        return self.run(script, {"hook_event_name": event, "cwd": str(cwd or self.path), **fields})

    def report(self, cwd: Path | None = None) -> dict:
        path = self.git("rev-parse", "--path-format=absolute", "--git-path", "claude-verify-report.json", cwd=cwd)
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def locks(self) -> dict:
        path = Path(self.git("rev-parse", "--path-format=absolute", "--git-path", "claude-task-locks.json"))
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def rmtree(path: Path) -> None:
    """git이 만든 읽기 전용 파일도 지운다(Windows). Python 3.8에는 ignore_cleanup_errors가 없다."""
    def force(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=force)


def stderr(proc: subprocess.CompletedProcess) -> str:
    return proc.stderr.decode("utf-8")


class RepoTestCase(unittest.TestCase):
    template = "verified-parallel-dev"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree, self.tmp)
        self.repo = Repo(self.tmp, self.template)

    def assertStatus(self, proc, status: str, code: int, cwd: Path | None = None) -> dict:
        report = self.repo.report(cwd)
        self.assertEqual((report["status"], proc.returncode), (status, code), stderr(proc))
        return report
