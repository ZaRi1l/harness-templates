"""sched 저장 계층: 오류, 시각, git 실행(시간 제한), 설정, 잠금, 원자적 기록. 다른 sched 모듈에 기대지 않는다."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify  # noqa: E402  (git 실행기 재사용)
HOOKS = Path(__file__).resolve().parent
CONFIG_PATH = HOOKS / "auto.config.json"
DEFAULT_CONFIG = {
    "protected_branches": ["main", "master"],
    "slots": {"research": 2, "design": 1, "preview": 2, "implement": 3, "verify": 2},
    "max_retries": 3,
    "verifiers_by_risk": {"low": 1, "medium": 1, "high": 2},
    "profiles": {
        "fast": {"research": False, "design": False, "verifiers": 1},
        "standard": {"research": False, "design": False, "verifiers": 1},
        "research": {"research": True, "design": False, "verifiers": 1},
        "deep": {"research": True, "design": True, "verifiers": 2},
    },
    "integration_lock_stale_s": 7200,
    "verify_assignment": True,  # asterbraid 추가(e2e5): `start --pool verify`로 배정된 verifier의 증거만 센다
    "design_review": "deep",  # asterbraid 추가(5-19): off | deep(deep 설계는 검토 PASS 뒤 구현) | ask(deep마다 사용자에게 묻는다)
}
GIT_TIMEOUT_S = 100  # 상태 잠금을 오래된 것으로 보는 120초보다 짧게. git이 멈춰도 잠금을 쥔 채 기다리지 않는다


class Refused(Exception):
    pass


class Busy(Exception):
    pass


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


HELD_LOCKS: set = set()  # 이 프로세스가 쥔 잠금. git을 부를 때마다 갱신해 오래된 잠금으로 치워지지 않게 한다


def run_git(cwd: Path, *args: str) -> tuple[int, str]:
    for lock in HELD_LOCKS:
        with contextlib.suppress(OSError):
            os.utime(str(lock))
    try:
        p = subprocess.run([verify.GIT, "-C", str(cwd), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise Refused(f"git {' '.join(args)} 명령이 {GIT_TIMEOUT_S}초 시간 제한을 넘었다. 다른 git 작업·잠긴 파일을 확인하고 다시 실행한다")
    return p.returncode, (p.stdout if p.returncode == 0 else p.stdout + p.stderr).strip()


def git(cwd: Path, *args: str) -> str:
    code, out = run_git(cwd, *args)
    if code != 0:
        raise Refused(f"git {' '.join(args)} 실패: {out}")
    return out


def main_root(cwd: Path | None = None) -> Path:
    """어느 worktree에서 불러도 메인 체크아웃 경로를 돌려준다."""
    common = git(cwd or Path.cwd(), "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent


def state_dir(root: Path) -> Path:
    return Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "claude-auto"


def config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        for key, value in json.loads(CONFIG_PATH.read_text(encoding="utf-8")).items():
            if key.startswith("_"):
                continue
            if key not in cfg:
                raise Refused(f"auto.config.json에 모르는 키: {key}")
            if isinstance(cfg[key], dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


class FileLock:
    """O_EXCL 잠금 파일. stale 초를 넘긴 잠금은 죽은 프로세스의 것으로 보고 치운다."""

    def __init__(self, path: Path, timeout: float, stale: float):
        self.path, self.timeout, self.stale = path, timeout, stale

    def acquire(self) -> None:
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {now()}".encode())
                os.close(fd)
                HELD_LOCKS.add(self.path)
                return
            except (FileExistsError, PermissionError):  # Windows는 지워지는 중인 파일에 PermissionError를 낸다
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        os.unlink(str(self.path))
                        continue
                except OSError:
                    continue
                if time.time() >= deadline:
                    raise Busy(str(self.path))
                time.sleep(0.05)

    def release(self) -> None:
        HELD_LOCKS.discard(self.path)
        with contextlib.suppress(OSError):
            os.unlink(str(self.path))

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def atomic_write(path: Path, data, indent=1) -> None:
    tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(40):  # Windows: 백신 등이 파일을 잠깐 잡고 있으면 교체가 거부된다
            try:
                os.replace(str(tmp), str(path))
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
