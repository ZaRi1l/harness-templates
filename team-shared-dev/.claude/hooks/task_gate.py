#!/usr/bin/env python3
"""Agent Teams 작업 Hook (표준 라이브러리만 사용, Python 3.8+).

TaskCreated   작업 설명에 '담당 파일:'과 '완료 기준:' 줄이 없거나, 담당 파일이 아직 열린 다른 작업과
              겹치면 생성을 거부한다(exit 2). 통과하면 담당 파일을 잠근다.
TaskCompleted verify.py를 돌려 PASS/SKIP이 아니면 완료를 막는다(exit 2). 통과하면 잠금을 푼다.

잠금 파일: <git dir>/claude-task-locks.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import verify  # noqa: E402  (같은 폴더의 verify.py: git 경로 처리 재사용)

NO_FILES = {"없음", "none", "-"}


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def field(text: str, name: str) -> str | None:
    for line in text.splitlines():
        line = line.strip().lstrip("-* ").strip()
        if line.startswith(name):
            return line[len(name):].strip()
    return None


def norm(path: str) -> str:
    path = path.strip().strip("`'\"").replace("\\", "/")
    for suffix in ("/**", "/*"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    path = path.rstrip("/")
    while path.startswith("./"):
        path = path[2:]
    return "" if path == "." else path


def overlaps(a: str, b: str) -> bool:
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def lock_path(cwd: str) -> Path:
    root = Path(verify.git(Path(cwd), "rev-parse", "--show-toplevel").strip())
    return verify.git_path(root, "claude-task-locks.json")


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def on_created(hook: dict, path: Path) -> None:
    desc = hook.get("task_description") or ""
    files_text, criteria = field(desc, "담당 파일:"), field(desc, "완료 기준:")
    if files_text is None or not criteria:
        block("작업 설명에 다음 두 줄이 필요하다:\n"
              "  담당 파일: src/a.py, src/b/   (읽기만 하는 작업이면 '담당 파일: 없음')\n"
              "  완료 기준: 무엇이 통과하면 끝인지")
    if files_text.lower() in NO_FILES:
        return
    files = [norm(f) for f in files_text.split(",") if f.strip()]
    if not files:
        block("담당 파일이 비어 있다. 읽기 전용이면 '담당 파일: 없음'으로 쓴다.")
    locks = load(path)
    for other_id, other in locks.items():
        hit = [f for f in files for g in other["files"] if overlaps(f, g)]
        if hit:
            block(f"담당 파일 {sorted(set(hit))}이(가) 열린 작업 {other_id} '{other['subject']}'과 겹친다. "
                  "그 작업이 끝난 뒤 만들거나, 파일을 나눠라.")
    locks[hook.get("task_id", "?")] = {"subject": hook.get("task_subject", ""), "files": files}
    path.write_text(json.dumps(locks, ensure_ascii=False, indent=2), encoding="utf-8")


def on_completed(raw: str, hook: dict, path: Path) -> None:
    res = subprocess.run([sys.executable, str(verify.__file__)], input=raw, text=True, encoding="utf-8")
    if res.returncode != 0:
        block(f"verify.py가 통과하지 않아 작업 '{hook.get('task_subject', '')}'을 완료할 수 없다. "
              "위 보고를 보고 고친다. 다른 작업 때문에 실패했다면 lead에게 알린다.")
    locks = load(path)
    if locks.pop(hook.get("task_id", "?"), None) is not None:
        path.write_text(json.dumps(locks, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")  # Hook 입력은 UTF-8 (Windows 기본 cp949 아님)
    hook = json.loads(raw) if raw.strip() else {}
    path = lock_path(hook.get("cwd") or ".")
    event = hook.get("hook_event_name")
    if event == "TaskCreated":
        on_created(hook, path)
    elif event == "TaskCompleted":
        on_completed(raw, hook, path)


if __name__ == "__main__":
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # Claude는 stderr를 UTF-8로 읽는다
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # 내부 오류가 통과처럼 보이면 안 된다
        block(f"task_gate.py 내부 오류: {type(e).__name__}: {e}")
