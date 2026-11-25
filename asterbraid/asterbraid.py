#!/usr/bin/env python3
"""AsterBraid 설치·업데이트·점검 (표준 라이브러리만, 사용자가 직접 실행한다).

  uv run --no-project python asterbraid/asterbraid.py install <프로젝트 폴더> [--apply]
  uv run --no-project python asterbraid/asterbraid.py update  <프로젝트 폴더> [--apply]
  uv run --no-project python asterbraid/asterbraid.py check   <프로젝트 폴더>

--apply 없이는 바꿀 내용만 보여 준다. 하네스 파일(agents·hooks·skills·.claude/asterbraid.md)은 복사하고,
프로젝트 파일은 합친다: settings.json(기존 값 유지, 하네스 권한·Hook만 추가), .gitignore(빠진 줄만),
CLAUDE.md(없으면 템플릿, 있으면 끝에 `@.claude/asterbraid.md` 한 줄). 같은 이름의 다른 파일은 덮지 않는다.
설치한 파일의 해시를 .claude/asterbraid.lock.json에 남겨, update는 사용자가 고치지 않은 하네스 파일만 바꾼다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
TEMPLATE = Path(__file__).resolve().parent
LOCK = ".claude/asterbraid.lock.json"
IMPORT = "@.claude/asterbraid.md"
PROJECT_OWNED = {".claude/settings.json", ".claude/hooks/verify.config.json", ".claude/hooks/auto.config.json"}
SKIP_PARTS = {"__pycache__", "node_modules", "worktrees"}
GITIGNORE = [".claude/settings.local.json", ".claude/worktrees/", ".playwright-cli/"]


class Refused(Exception):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def harness_files() -> dict:
    """템플릿의 하네스 파일: 상대 경로 → 원본."""
    out = {}
    for p in sorted((TEMPLATE / ".claude").rglob("*")):
        rel = p.relative_to(TEMPLATE).as_posix()
        if p.is_file() and rel not in PROJECT_OWNED and not SKIP_PARTS & set(p.parts) \
                and "/.claude-plugin/types/" not in rel and rel != LOCK:
            out[rel] = p
    return out


def merge_settings(cur: dict, tpl: dict) -> list:
    """tpl의 하네스 항목을 cur에 더한다(기존 값은 바꾸지 않는다). 바뀐 내용 목록을 돌려준다."""
    changes = []
    if "$schema" in tpl:
        cur.setdefault("$schema", tpl["$schema"])
    for key in ("env", "enabledPlugins", "worktree"):
        for k, v in tpl.get(key, {}).items():
            have = cur.setdefault(key, {})
            if k not in have:
                have[k] = v
                changes.append(f"{key}.{k}")
            elif have[k] != v:
                changes.append(f"{key}.{k}: 기존 값 {have[k]!r} 유지(하네스 값 {v!r})")
    for kind, rules in tpl.get("permissions", {}).items():
        have = cur.setdefault("permissions", {}).setdefault(kind, [])
        added = [r for r in rules if r not in have]
        have += added
        if added:
            changes.append(f"permissions.{kind} +{len(added)}")
    for event, groups in tpl.get("hooks", {}).items():
        have = cur.setdefault("hooks", {}).setdefault(event, [])
        for g in groups:
            if g not in have:
                have.append(g)
                changes.append(f"hooks.{event}")
    return changes


def plan(target: Path, mode: str) -> tuple[list, dict]:
    """(할 일 목록, 새 lock). 할 일: (종류, 경로, 설명). 종류: copy·remove·write·keep."""
    lock_path = target / LOCK
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else None
    if mode == "install" and lock:
        raise Refused("이미 설치돼 있다. 새 버전 반영은 update를 쓴다")
    if mode == "update" and not lock:
        raise Refused("설치 기록이 없다. 먼저 install을 쓴다")
    old = (lock or {}).get("files", {})
    acts, files = [], {}
    for rel, src in harness_files().items():
        dst, new = target / rel, sha(src)
        if not dst.exists():
            acts.append(("copy", rel, "추가"))
            files[rel] = new
            continue
        cur = sha(dst)
        if cur == new:
            files[rel] = new
        elif rel in old and cur == old[rel]:
            acts.append(("copy", rel, "새 버전"))
            files[rel] = new
        else:
            acts.append(("keep", rel, "이미 있고 내용이 달라 덮지 않았다(직접 비교해 합친다)"))
            if rel in old:
                files[rel] = old[rel]
    for rel, h in old.items():
        if rel not in files and (target / rel).is_file():
            if sha(target / rel) == h:
                acts.append(("remove", rel, "템플릿에서 빠졌다"))
            else:
                acts.append(("keep", rel, "템플릿에서 빠졌지만 직접 고친 파일이라 남겼다"))
    for rel in sorted(PROJECT_OWNED - {".claude/settings.json"}):
        if not (target / rel).exists():
            acts.append(("copy", rel, "추가(프로젝트 설정, 이후 update는 건드리지 않는다)"))
    # settings.json
    tpl = json.loads((TEMPLATE / ".claude/settings.json").read_text(encoding="utf-8"))
    sp = target / ".claude/settings.json"
    if sp.is_file():
        try:
            cur = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError as e:
            raise Refused(f".claude/settings.json을 읽지 못했다({e}). 고친 뒤 다시 실행한다")
        changes = merge_settings(cur, tpl)
        if any(": 기존 값" not in c for c in changes):
            acts.append(("write", ".claude/settings.json", "합침: " + ", ".join(changes),
                         json.dumps(cur, ensure_ascii=False, indent=4) + "\n"))
        elif changes:
            acts.append(("keep", ".claude/settings.json", ", ".join(changes)))
    else:
        acts.append(("copy", ".claude/settings.json", "추가"))
    # .gitignore
    gp = target / ".gitignore"
    text = gp.read_text(encoding="utf-8") if gp.is_file() else ""
    missing = [x for x in GITIGNORE if x not in {l.strip() for l in text.splitlines()}]
    if missing:
        sep = "" if not text or text.endswith("\n") else "\n"
        acts.append(("write", ".gitignore", "줄 추가: " + ", ".join(missing), text + sep + "\n".join(missing) + "\n"))
    # CLAUDE.md
    cp = target / "CLAUDE.md"
    if not cp.is_file():
        acts.append(("copy", "CLAUDE.md", "추가(TODO는 manager가 채운다)"))
    else:
        text = cp.read_text(encoding="utf-8")
        if IMPORT not in {l.strip() for l in text.splitlines()}:
            sep = "" if text.endswith("\n") else "\n"
            acts.append(("write", "CLAUDE.md", f"끝에 `{IMPORT}` 한 줄(기존 내용은 그대로)",
                         text + sep + "\n<!-- AsterBraid 하네스 규칙 -->\n" + IMPORT + "\n"))
    rev = subprocess.run(["git", "-C", str(TEMPLATE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return acts, {"template": rev.stdout.strip() or None, "files": files}


def apply(target: Path, acts: list, lock: dict) -> None:
    for act in acts:
        kind, rel = act[0], act[1]
        dst = target / rel
        if kind == "copy":
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((TEMPLATE / rel).read_bytes())
        elif kind == "write":
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(act[3], encoding="utf-8", newline="")
        elif kind == "remove":
            dst.unlink()
    (target / LOCK).write_text(json.dumps(lock, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def check(target: Path) -> int:
    bad = []
    lock_path = target / LOCK
    if not lock_path.is_file():
        bad.append("설치 기록(.claude/asterbraid.lock.json)이 없다: install")
    for rel, src in harness_files().items():
        dst = target / rel
        if not dst.is_file():
            bad.append(f"없다: {rel}")
        elif sha(dst) != sha(src):
            bad.append(f"템플릿과 다르다: {rel}")
    try:
        s = json.loads((target / ".claude/settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    text = json.dumps(s.get("hooks", {}))
    for need, label in (("sched.py", "SessionStart Hook(sched status)"), ("verify.py", "Stop·SubagentStop Hook(verify)")):
        if need not in text:
            bad.append(f"settings.json에 {label}이 없다")
    if "Bash(git push *)" not in s.get("permissions", {}).get("deny", []):
        bad.append("settings.json에 git push 거부가 없다")
    if not any(k.startswith("asterbraid-dashboard@") for k in s.get("enabledPlugins", {})):
        bad.append("settings.json에 관제 화면 Mod가 켜져 있지 않다")
    gi = (target / ".gitignore").read_text(encoding="utf-8") if (target / ".gitignore").is_file() else ""
    bad += [f".gitignore에 {x}가 없다" for x in GITIGNORE if x not in gi.splitlines()]
    cm = (target / "CLAUDE.md").read_text(encoding="utf-8") if (target / "CLAUDE.md").is_file() else ""
    if IMPORT not in cm:
        bad.append(f"CLAUDE.md에 `{IMPORT}`가 없다")
    for line in bad:
        print(f"✗ {line}")
    print("✓ 하네스 설치 상태 정상" if not bad else f"문제 {len(bad)}개. update --apply로 고치거나 직접 합친다")
    return 1 if bad else 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["install", "update", "check"])
    ap.add_argument("target")
    ap.add_argument("--apply", action="store_true", help="실제로 적용한다(없으면 미리보기)")
    a = ap.parse_args()
    target = Path(a.target).resolve()
    try:
        if not target.is_dir():
            raise Refused(f"폴더가 없다: {target}")
        if target == TEMPLATE or TEMPLATE in target.parents or target in TEMPLATE.parents:
            raise Refused("템플릿 저장소 자체에는 설치하지 않는다")
        if a.mode == "check":
            return check(target)
        acts, lock = plan(target, a.mode)
    except Refused as e:
        print(f"[asterbraid] 거부: {e}", file=sys.stderr)
        return 2
    shown = [x for x in acts if x[0] != "copy" or x[2] != "추가"]
    added = len(acts) - len(shown)
    if added:
        print(f"추가: 하네스 파일 {added}개")
    for act in shown:
        print(f"{ {'copy': '복사', 'write': '수정', 'remove': '삭제', 'keep': '확인 필요'}[act[0]] }: {act[1]} — {act[2]}")
    if not acts:
        print("바꿀 것이 없다")
    if not a.apply:
        print("\n미리보기다. 적용하려면 같은 명령에 --apply를 붙인다")
        return 0
    apply(target, acts, lock)
    print("\n적용했다. 다음: Claude 세션을 다시 열고(새 Hook·에이전트·Mod를 읽는다) `claude --agent manager`로 시작한다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
