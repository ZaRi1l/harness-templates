#!/usr/bin/env python3
"""autonomous-parallel-dev 작업 스케줄러 (표준 라이브러리만 사용, Python 3.8+).

작업 상태를 <git common dir>/claude-auto/state.json 에 둔다. 모든 변경은 잠금 + 원자적 교체로 기록하고,
상태 전이·의존성·담당 경로·실행 슬롯·검증 증거 규칙은 이 프로그램이 강제한다. 에이전트는 상태 파일을
직접 고치지 않고 아래 명령만 쓴다. 어느 worktree에서 실행해도 같은 저장소의 상태를 본다.

  init                         통합 브랜치(메인 체크아웃의 현재 브랜치)를 기록한다
  add / load FILE              작업 추가(하나 / JSON 목록 일괄). 의존성 순환·중복 ID를 거부한다
  next --pool P                지금 시작할 수 있는 작업(research|design|implement|verify|merge)
  start ID --pool P --agent A  작업을 배정한다(중복 배정·경로 충돌·슬롯 초과·미완료 의존을 거부한다)
  done ID [--notes PATH]       조사·설계 단계 완료
  preview ID --file F          (designer) 미리보기 버전 등록 → AWAITING_APPROVAL
  approve ID / revise ID --feedback "..."   사용자의 승인 / 수정 요청을 기록한다
  dashboard                    미리보기와 전체 작업 현황을 한 HTML로 만든다
  submit ID                    (작업 worktree에서) 구현 제출 → VERIFYING. 담당 경로 밖 변경을 거부한다
  evidence ID --verifier V --verdict PASS|FAIL --sha SHA   독립 검증 증거 기록
  fail / block / unblock / cancel ID                        재작업·보류·해제·취소
  paths ID --set P1,P2 --reason R   구현 시작 전에 담당 경로를 안전하게 바꾼다(진행 중 충돌 검사)
  sync ID                      (작업 worktree에서) 통합 브랜치와 이전 작업 브랜치를 병합해 온다
  status [--json] / metrics    현황 / 소요 시간·실패율 등
  recover [--reset-running]    저장된 상태를 실제 Git 상태와 대조해 고친다(세션 재시작 때)
  cleanup [ID...]              통합이 끝난 작업의 worktree·브랜치를 조건을 확인하고 지운다
  limit POOL N / notes-dir ID  슬롯 수 조절 / 작업별 조사·설계 노트 폴더

종료 코드: 0 성공, 2 거부(사유를 stderr에 쓴다).
"""

from __future__ import annotations

import argparse
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

sys.dont_write_bytecode = True  # Hook 폴더에 __pycache__를 남기지 않는다
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
}
RISKS = ("low", "medium", "high")
TRANSITIONS = {
    "PENDING": {"RESEARCHING", "DESIGNING", "PREVIEWING", "READY", "BLOCKED", "CANCELLED"},
    "RESEARCHING": {"PENDING", "READY", "BLOCKED", "CANCELLED"},
    "DESIGNING": {"PENDING", "READY", "BLOCKED", "CANCELLED"},
    "PREVIEWING": {"AWAITING_APPROVAL", "PENDING", "BLOCKED", "CANCELLED"},
    "AWAITING_APPROVAL": {"READY", "PENDING", "BLOCKED", "CANCELLED"},
    "READY": {"IMPLEMENTING", "BLOCKED", "CANCELLED"},
    "IMPLEMENTING": {"VERIFYING", "RETRY", "BLOCKED", "CANCELLED"},
    "VERIFYING": {"READY_TO_MERGE", "RETRY", "BLOCKED", "CANCELLED"},
    "READY_TO_MERGE": {"MERGING", "VERIFYING", "RETRY", "BLOCKED", "CANCELLED"},
    "MERGING": {"COMPLETED", "READY_TO_MERGE", "RETRY", "BLOCKED"},
    "RETRY": {"IMPLEMENTING", "BLOCKED", "CANCELLED"},
    "BLOCKED": {"PENDING", "READY", "RETRY", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}
SLOT_STATE = {"research": "RESEARCHING", "design": "DESIGNING", "preview": "PREVIEWING",
              "implement": "IMPLEMENTING"}
# 아직 통합되지 않은 변경을 가진 작업은 담당 경로를 계속 잡고 있다
PATH_HOLDERS = {"IMPLEMENTING", "RETRY", "VERIFYING", "READY_TO_MERGE", "MERGING"}


class Refused(Exception):
    pass


class Busy(Exception):
    pass


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


GIT_TIMEOUT_S = 100  # 상태 잠금을 오래된 것으로 보는 120초보다 짧게. git이 멈춰도 잠금을 쥔 채 기다리지 않는다
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


@contextlib.contextmanager
def store(root: Path, write: bool = True):
    """상태를 잠그고 읽는다. 블록이 예외 없이 끝났을 때만 원자적으로 기록한다."""
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    with FileLock(d / "state.lock", timeout=30, stale=120):
        path = d / "state.json"
        st = json.loads(path.read_text(encoding="utf-8")) if path.exists() else \
            {"version": 1, "integration_branch": None, "limits": {}, "tasks": {}}
        yield st
        if write:
            tmp = d / f"state.json.{os.getpid()}.tmp"
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(st, fh, ensure_ascii=False, indent=1)
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


# ---------- 규칙 ----------

def norm(path: str) -> str:
    path = path.strip().strip("`'\"").replace("\\", "/")
    for suffix in ("/**", "/*"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    path = path.rstrip("/")
    while path.startswith("./"):
        path = path[2:]
    return "" if path == "." else path


def validate_path(raw: str) -> str:
    if not isinstance(raw, str):
        raise Refused("경로는 문자열이어야 한다")
    s = raw.strip().strip("`'\"")
    if not s:
        raise Refused("경로가 비어 있다")
    if any(c in s for c in "*?["):
        raise Refused(f"와일드카드·glob 패턴은 지원하지 않는다. 리터럴 파일 또는 폴더 경로만 지정한다: {raw}")
    if s.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", s) or s.startswith(("//", "\\\\")):
        raise Refused(f"절대 경로는 허용되지 않는다(저장소 루트 기준 상대 경로만): {raw}")
    n = s.replace("\\", "/")
    if "//" in n:
        raise Refused(f"경로에 '//'가 포함되어 있다: {raw}")
    while n.startswith("./"):
        n = n[2:]
    n = n.rstrip("/")
    if not n or n == ".":
        raise Refused(f"경로가 유효하지 않다: {raw}")
    segments = n.split("/")
    for seg in segments:
        if seg == "..":
            raise Refused(f"상위 경로('..') 탐색은 허용되지 않는다: {raw}")
        if seg == ".":
            raise Refused(f"경로에 불필요한 '.' 세그먼트가 포함되어 있다: {raw}")
        if not seg:
            raise Refused(f"경로에 빈 세그먼트가 포함되어 있다: {raw}")
    return n


def validate_paths(paths: list | str) -> list[str]:
    if isinstance(paths, str):
        raw_list = paths.split(",")
    elif isinstance(paths, list):
        raw_list = paths
    else:
        raise Refused("담당 경로는 문자열 또는 목록이어야 한다")
    if not raw_list:
        raise Refused("담당 경로가 비어 있다")
    out = []
    seen = set()
    for item in raw_list:
        p = validate_path(item)
        if p not in seen:
            seen.add(p)
            out.append(p)
    if not out:
        raise Refused("담당 경로가 비어 있다")
    return out


def can_edit_paths(t: dict) -> tuple[bool, str]:
    if t["state"] not in ("PENDING", "READY", "RETRY", "BLOCKED"):
        return False, f"{t['id']}는 {t['state']} 상태라 담당 경로를 바꿀 수 없다(구현 시작 전 상태에서만 허용)"
    if (t.get("branch") or t.get("head") or t.get("worktree") or t.get("old_branches")
            or any(h.get("to") in ("IMPLEMENTING", "VERIFYING", "READY_TO_MERGE", "MERGING", "COMPLETED")
                   for h in t.get("history", []))):
        return False, f"{t['id']}는 이미 구현이 시작되었거나 브랜치/작업트리가 있어 담당 경로를 바꿀 수 없다"
    return True, ""


def overlaps(a: str, b: str) -> bool:
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def under(file: str, paths: list) -> bool:
    return any(not p or file == p or file.startswith(p + "/") for p in paths)


def get(st: dict, task_id: str) -> dict:
    if task_id not in st["tasks"]:
        raise Refused(f"작업이 없다: {task_id}")
    return st["tasks"][task_id]


def transition(t: dict, new: str, reason: str = "", force: bool = False, **fields) -> None:
    if not force and new not in TRANSITIONS[t["state"]]:
        raise Refused(f"{t['id']}: {t['state']} → {new} 전이는 허용되지 않는다")
    t["history"].append({"at": now(), "from": t["state"], "to": new, "reason": reason, **fields})
    t["state"], t["updated"], t["reason"] = new, now(), reason
    if new == "COMPLETED":
        t["completed"] = now()


def needs(cfg: dict, t: dict) -> tuple[bool, bool, bool]:
    """아직 남은 사전 단계: (조사, 설계, 미리보기 승인). 이 순서로 진행한다."""
    prof = cfg["profiles"][t["profile"]]
    return (prof["research"] and not t["researched"], prof["design"] and not t["designed"],
            t["preview"]["required"] and t["preview"]["approved"] is None)


def approved_preview(t: dict) -> dict | None:
    return next((v for v in t["preview"]["versions"] if v["n"] == t["preview"]["approved"]), None)


def required_verifiers(cfg: dict, t: dict) -> int:
    return max(cfg["profiles"][t["profile"]]["verifiers"], cfg["verifiers_by_risk"][t["risk"]])


def criteria_version(t: dict) -> str:
    """검증 기준(verify.py, 설정, 작업의 완료 기준)이 바뀌면 이전 증거는 쓰지 않는다."""
    h = hashlib.sha256()
    hooks = main_root() / ".claude" / "hooks"  # 어느 worktree에서 불러도 메인 체크아웃의 기준을 쓴다
    for f in (hooks / "verify.py", hooks / "verify.config.json"):
        h.update(f.read_bytes())
    h.update(t["criteria"].encode("utf-8"))
    h.update(((approved_preview(t) or {}).get("sha256") or "").encode())  # 승인된 미리보기가 바뀌어도 무효
    return h.hexdigest()[:16]


def valid_evidence(t: dict) -> list:
    version, seen, out = criteria_version(t), set(), []
    for e in t["evidence"]:
        if (e["verdict"] == "PASS" and e["sha"] == t.get("head") and e["criteria"] == version
                and e["verifier"] not in seen):
            seen.add(e["verifier"])
            out.append(e)
    return out


def limit(st: dict, cfg: dict, pool: str) -> int:
    return int(st["limits"].get(pool, cfg["slots"][pool]))


def used(st: dict, pool: str) -> int:
    return sum(1 for t in st["tasks"].values() if t["state"] == SLOT_STATE.get(pool))


def holds_paths(t: dict) -> bool:
    return t["state"] in PATH_HOLDERS or (t["state"] == "BLOCKED" and bool(t.get("branch")))


def path_conflict(st: dict, t: dict, extra: list = ()) -> str | None:
    others = [o for o in st["tasks"].values() if o["id"] != t["id"] and holds_paths(o)] + list(extra)
    for o in others:
        hit = [p for p in t["paths"] for q in o["paths"] if overlaps(p, q)]
        if hit:
            return f"담당 경로 {sorted(set(hit))}이(가) 진행 중인 {o['id']}와 겹친다"
    return None


def waiting_deps(st: dict, t: dict) -> list:
    return [d for d in t["deps"] if st["tasks"][d]["state"] != "COMPLETED"]


def check_cycles(tasks: dict) -> None:
    visiting, done = set(), set()

    def visit(node: str, trail: list) -> None:
        if node in done:
            return
        if node in visiting:
            raise Refused("의존성 순환: " + " → ".join(trail + [node]))
        visiting.add(node)
        for dep in tasks[node]["deps"]:
            visit(dep, trail + [node])
        visiting.discard(node)
        done.add(node)

    for name in tasks:
        visit(name, [])


def new_task(cfg: dict, spec: dict) -> dict:
    for key in ("id", "title", "criteria", "paths"):
        if not spec.get(key):
            raise Refused(f"작업에 '{key}'가 필요하다: {spec.get('id', '?')}")
    risk = spec.get("risk", "medium")
    if risk not in RISKS:
        raise Refused(f"risk는 {RISKS} 중 하나다: {risk}")
    profile = spec.get("profile", "adaptive")
    if profile == "adaptive":
        profile = "deep" if risk == "high" else "research" if spec.get("needs_research") else \
            "fast" if risk == "low" else "standard"
    if profile not in cfg["profiles"]:
        raise Refused(f"모르는 프로파일: {profile}")
    paths = validate_paths(spec["paths"])
    t = {"id": str(spec["id"]), "title": spec["title"], "criteria": spec["criteria"], "paths": paths,
         "forbid": [norm(p) for p in spec.get("forbid", [])], "deps": [str(d) for d in spec.get("deps", [])],
         "priority": int(spec.get("priority", 0)), "risk": risk, "profile": profile, "state": "PENDING",
         "researched": False, "designed": False, "agent": None, "branch": None, "worktree": None,
         "base": None, "head": None, "old_branches": [], "evidence": [], "notes": [], "retries": 0,
         "reason": "", "history": [], "created": now(), "updated": now(), "completed": None, "cleaned": False,
         "preview": {"required": bool(spec.get("preview")), "versions": [], "approved": None, "feedback": []}}
    if not any(needs(cfg, t)):
        transition(t, "READY", "조사·설계 단계가 필요 없는 프로파일")
    return t


def insert(st: dict, cfg: dict, specs: list) -> list:
    tasks = dict(st["tasks"])
    for spec in specs:
        t = new_task(cfg, spec)
        if t["id"] in tasks:
            raise Refused(f"작업 ID가 이미 있다: {t['id']}")
        tasks[t["id"]] = t
    for spec in specs:
        for dep in tasks[str(spec["id"])]["deps"]:
            if dep not in tasks:
                raise Refused(f"{spec['id']}의 의존 작업이 없다: {dep}")
    check_cycles(tasks)
    st["tasks"] = tasks
    return [str(s["id"]) for s in specs]


def fail(cfg: dict, t: dict, reason: str, count: bool = True, **fields) -> str:
    if count:
        t["retries"] += 1
    if t["retries"] > cfg["max_retries"]:
        transition(t, "BLOCKED", f"재시도 한도({cfg['max_retries']}) 초과: {reason}", force=True, **fields)
    else:
        transition(t, "RETRY", reason, force=t["state"] not in ("IMPLEMENTING", "VERIFYING", "READY_TO_MERGE",
                                                                 "MERGING"), **fields)
    return t["state"]


def is_ancestor(root: Path, commit: str, of: str) -> bool:
    return run_git(root, "merge-base", "--is-ancestor", commit, of)[0] == 0


def branch_head(root: Path, branch: str) -> str | None:
    code, out = run_git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
    return out if code == 0 and out else None


def prune_temp(root: Path) -> list:
    """중단된 통합이 남긴 임시 worktree를 치운다."""
    removed = []
    base = root / ".claude" / "worktrees"
    for d in sorted(base.glob("_integrate-*")) if base.is_dir() else []:
        remove_worktree(root, d)
        removed.append(d.name)
    return removed


def remove_worktree(root: Path, path: Path) -> None:
    """임시 worktree를 지운다. Windows에서 파일이 잠깐 잡혀 있을 수 있어 몇 번 다시 시도한다."""
    for _ in range(20):
        run_git(root, "worktree", "remove", "--force", str(path))
        shutil.rmtree(str(path), ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.1)
    run_git(root, "worktree", "prune")


# ---------- 명령 ----------

def cmd_init(a, root, cfg):
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in cfg["protected_branches"] or branch == "HEAD":
        raise Refused(f"메인 체크아웃이 '{branch}'다. 보호 브랜치는 통합 브랜치가 될 수 없다. "
                      "`git switch -c integration`으로 통합 브랜치를 만든 뒤 다시 실행한다")
    with store(root) as st:
        st["integration_branch"] = branch
    return {"integration_branch": branch}


def cmd_add(a, root, cfg):
    spec = {"id": a.id, "title": a.title, "criteria": a.criteria, "paths": a.paths.split(","),
            "forbid": a.forbid.split(",") if a.forbid else [], "deps": a.deps.split(",") if a.deps else [],
            "priority": a.priority, "risk": a.risk, "profile": a.profile, "needs_research": a.needs_research,
            "preview": a.preview}
    with store(root) as st:
        ids = insert(st, cfg, [spec])
        return {"added": ids, "state": st["tasks"][ids[0]]["state"], "profile": st["tasks"][ids[0]]["profile"]}


def cmd_load(a, root, cfg):
    specs = json.loads(Path(a.file).read_text(encoding="utf-8"))
    with store(root) as st:
        return {"added": insert(st, cfg, specs)}


def dispatchable(st: dict, cfg: dict, pool: str) -> tuple[list, list]:
    picks, waiting = [], []
    tasks = sorted(st["tasks"].values(), key=lambda t: (-t["priority"], t["created"], t["id"]))
    if pool == "merge":
        return [t for t in tasks if t["state"] == "READY_TO_MERGE"], []
    if pool == "approval":
        return [t for t in tasks if t["state"] == "AWAITING_APPROVAL"], []
    if pool == "verify":
        need = [t for t in tasks if t["state"] == "VERIFYING"]
        return need[: limit(st, cfg, "verify")], [{"id": t["id"], "why": "검증 슬롯 없음"}
                                                 for t in need[limit(st, cfg, "verify"):]]
    free = limit(st, cfg, pool) - used(st, pool)
    for t in tasks:
        need_r, need_d, need_p = needs(cfg, t)
        if pool == "research":
            ok, why = t["state"] == "PENDING" and need_r, None
        elif pool == "design":
            ok, why = t["state"] == "PENDING" and need_d and not need_r, None
        elif pool == "preview":
            ok, why = t["state"] == "PENDING" and need_p and not need_r and not need_d, None
        else:
            ok = t["state"] in ("READY", "RETRY")
            deps = waiting_deps(st, t) if ok else []
            why = f"선행 작업 미완료: {deps}" if deps else path_conflict(st, t, picks) if ok else None
        if not ok:
            continue
        if why:
            waiting.append({"id": t["id"], "why": why})
        elif len(picks) >= free:
            waiting.append({"id": t["id"], "why": f"{pool} 슬롯 없음"})
        else:
            picks.append(t)
    return picks, waiting


def brief(t: dict) -> dict:
    return {k: t[k] for k in ("id", "title", "state", "profile", "risk", "paths", "forbid", "deps", "criteria",
                              "branch", "worktree", "head", "retries", "reason", "notes", "preview")}


def cmd_next(a, root, cfg):
    with store(root, write=False) as st:
        picks, waiting = dispatchable(st, cfg, a.pool)
        out = {"pool": a.pool, "dispatch": [brief(t) for t in picks], "waiting": waiting}
        if a.pool in SLOT_STATE:
            out["free_slots"] = limit(st, cfg, a.pool) - used(st, a.pool)
        if a.pool == "verify":
            for item, t in zip(out["dispatch"], picks):
                item["verifiers_needed"] = required_verifiers(cfg, t) - len(valid_evidence(t))
        return out


def cmd_start(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if not st["integration_branch"]:
            raise Refused("먼저 `sched.py init`으로 통합 브랜치를 기록한다")
        if used(st, a.pool) >= limit(st, cfg, a.pool):
            raise Refused(f"{a.pool} 슬롯이 다 찼다({limit(st, cfg, a.pool)})")
        need_r, need_d, need_p = needs(cfg, t)
        if a.pool == "research":
            if not need_r:
                raise Refused(f"{t['id']}는 조사 단계가 필요 없거나 이미 끝났다")
            transition(t, "RESEARCHING", agent=a.agent)
        elif a.pool == "design":
            if need_r or not need_d:
                raise Refused(f"{t['id']}는 지금 설계 단계가 아니다(조사 먼저이거나 설계가 필요 없다)")
            transition(t, "DESIGNING", agent=a.agent)
        elif a.pool == "preview":
            if need_r or need_d or not need_p:
                raise Refused(f"{t['id']}는 지금 미리보기 단계가 아니다")
            transition(t, "PREVIEWING", agent=a.agent)
        else:
            if t["state"] not in ("READY", "RETRY"):
                raise Refused(f"{t['id']}는 {t['state']} 상태라 구현을 시작할 수 없다(이미 배정됐거나 준비되지 않았다)")
            deps = waiting_deps(st, t)
            if deps:
                raise Refused(f"선행 작업이 아직 통합되지 않았다: {deps}")
            conflict = path_conflict(st, t)
            if conflict:
                raise Refused(conflict)
            if t["state"] == "READY":
                t["base"] = git(root, "rev-parse", st["integration_branch"])
            transition(t, "IMPLEMENTING", agent=a.agent)
        t["agent"] = a.agent
        return brief(t)


def cmd_done(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] == "RESEARCHING":
            t["researched"] = True
        elif t["state"] == "DESIGNING":
            t["designed"] = True
        else:
            raise Refused(f"{t['id']}는 조사·설계 중이 아니다({t['state']})")
        if a.notes:
            t["notes"].append(a.notes)
        transition(t, "PENDING" if any(needs(cfg, t)) else "READY", "단계 완료")
        return brief(t)


def preview_dir(root: Path) -> Path:
    return state_dir(root) / "previews"


def cmd_preview(a, root, cfg):
    src = Path(a.file)
    if not src.is_file():
        raise Refused(f"미리보기 파일이 없다: {src}")
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] != "PREVIEWING":
            raise Refused(f"{t['id']}는 {t['state']} 상태라 미리보기를 등록할 수 없다")
        n = len(t["preview"]["versions"]) + 1
        dest = preview_dir(root) / t["id"] / f"v{n}{src.suffix or '.html'}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(src), str(dest))  # 이전 버전을 덮어쓰지 않는다
        t["preview"]["versions"].append({"n": n, "file": str(dest), "at": now(), "summary": a.summary,
                                         "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()})
        transition(t, "AWAITING_APPROVAL", f"미리보기 v{n} 검토 대기")
        return {"id": t["id"], "version": n, "file": str(dest), "state": t["state"]}


def cmd_approve(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] != "AWAITING_APPROVAL":
            raise Refused(f"{t['id']}는 승인 대기 중이 아니다({t['state']})")
        versions = [v["n"] for v in t["preview"]["versions"]]
        n = a.version or versions[-1]
        if n not in versions:
            raise Refused(f"미리보기 v{n}이 없다. 있는 버전: {versions}")
        t["preview"]["approved"] = n
        transition(t, "READY", f"미리보기 v{n} 승인")
        return brief(t)


def cmd_revise(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] != "AWAITING_APPROVAL":
            raise Refused(f"{t['id']}는 승인 대기 중이 아니다({t['state']})")
        t["preview"]["feedback"].append({"at": now(), "on": len(t["preview"]["versions"]), "text": a.feedback})
        transition(t, "PENDING", f"미리보기 수정 요청: {a.feedback}")
        return brief(t)


def cmd_dashboard(a, root, cfg):
    import html
    with store(root, write=False) as st:
        tasks = sorted(st["tasks"].values(), key=lambda t: t["id"])
    e = html.escape
    script = "uv run --no-project --offline python .claude/hooks/sched.py"
    cards = []
    for t in tasks:
        if not t["preview"]["required"]:
            continue
        links = " · ".join(f'<a href="{e(t["id"])}/{e(Path(v["file"]).name)}">v{v["n"]}</a>'
                           + (" ✅" if v["n"] == t["preview"]["approved"] else "") for v in t["preview"]["versions"])
        feedback = "".join(f"<li>v{f['on']}: {e(f['text'])}</li>" for f in t["preview"]["feedback"])
        todo = (f"<pre>{script} approve {e(t['id'])}\n{script} revise {e(t['id'])} --feedback &quot;...&quot;</pre>"
                if t["state"] == "AWAITING_APPROVAL" else "")
        cards.append(f"<section><h3>{e(t['id'])} · {e(t['title'])} <span class='s'>{t['state']}</span></h3>"
                     f"<p>{links or '미리보기 없음'}</p><ul>{feedback}</ul>{todo}</section>")
    rows = "".join(f"<tr><td>{e(t['id'])}</td><td>{t['state']}</td><td>{e(t['title'])}</td>"
                   f"<td>{e(', '.join(t['deps']))}</td><td>{t['retries']}</td></tr>" for t in tasks)
    waiting = sum(1 for t in tasks if t["state"] == "AWAITING_APPROVAL")
    page = ("<!doctype html><meta charset='utf-8'><title>Development Preview</title><style>"
            "body{font-family:sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}"
            "section{border:1px solid #ccc;border-radius:8px;padding:.5rem 1rem;margin:1rem 0}"
            ".s{font-size:.8rem;background:#eee;border-radius:4px;padding:.1rem .4rem}"
            "pre{background:#f6f6f6;padding:.5rem;overflow:auto}"
            "td,th{border-bottom:1px solid #ddd;padding:.3rem .6rem;text-align:left}</style>"
            f"<h1>Development Preview</h1><p>생성 {now()} · 검토 대기 <b>{waiting}</b>건. "
            "승인·수정 요청은 아래 명령을 터미널에서 실행하거나 manager에게 말한다. "
            f"새로 고치려면 <code>{script} dashboard</code>.</p>"
            f"<h2>미리보기</h2>{''.join(cards) or '<p>미리보기가 필요한 작업이 없다.</p>'}"
            "<h2>전체 작업</h2><table><tr><th>ID</th><th>상태</th><th>작업</th><th>의존</th><th>재시도</th></tr>"
            f"{rows}</table>")
    out = preview_dir(root) / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return {"dashboard": str(out), "awaiting_approval": waiting}


def cmd_submit(a, root, cfg):
    wt = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    if verify.same_path(wt, root):
        raise Refused("메인 체크아웃에서는 제출할 수 없다. 작업 worktree에서 실행한다")
    with store(root) as st:
        t = get(st, a.id)
        integ = st["integration_branch"]
        if t["state"] != "IMPLEMENTING":
            raise Refused(f"{t['id']}는 {t['state']} 상태라 제출할 수 없다")
        branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
        if branch in cfg["protected_branches"] or branch in (integ, "HEAD"):
            raise Refused(f"작업 브랜치가 아니다: {branch}")
        if git(wt, "status", "--porcelain", "--untracked-files=no"):
            raise Refused("커밋 안 된 변경이 있다. 커밋한 뒤 제출한다")
        head = git(wt, "rev-parse", "HEAD")
        base = git(wt, "merge-base", integ, "HEAD")
        if head == base:
            raise Refused("통합 브랜치 이후 커밋이 없다")
        files = [f for f in git(wt, "diff", "--name-only", "-z", "--no-renames", f"{base}..HEAD").split("\0") if f]
        outside = sorted(f for f in files if not under(f, t["paths"]) or (t["forbid"] and under(f, t["forbid"])))
        if outside:
            raise Refused(f"담당 경로 밖(또는 수정 금지 경로)의 변경이 있다: {outside}. "
                          "되돌리거나, 범위를 넓혀야 하면 manager에게 보고한다")
        if t["branch"] and t["branch"] != branch:
            t["old_branches"].append(t["branch"])
        t.update(branch=branch, worktree=str(wt), head=head)
        transition(t, "VERIFYING", "제출", head=head)
        return brief(t)


def cmd_evidence(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] not in ("VERIFYING", "READY_TO_MERGE"):
            raise Refused(f"{t['id']}는 {t['state']} 상태라 검증 증거를 받을 수 없다")
        actual = branch_head(root, t["branch"])
        sha = git(root, "rev-parse", "--verify", f"{a.sha}^{{commit}}")
        if sha != t["head"] or actual != t["head"]:
            raise Refused(f"검증한 커밋 {sha[:10]}이 제출된 커밋 {str(t['head'])[:10]}(브랜치 현재 "
                          f"{str(actual)[:10]})과 다르다. 제출 뒤 브랜치가 바뀌었으면 다시 submit해야 한다")
        t["evidence"].append({"at": now(), "verifier": a.verifier, "verdict": a.verdict, "sha": sha,
                              "scope": a.scope, "notes": a.notes, "criteria": criteria_version(t)})
        if a.verdict == "FAIL":
            fail(cfg, t, f"verifier {a.verifier} FAIL: {a.notes}", kind="verify_fail")
        elif t["state"] == "VERIFYING" and len(valid_evidence(t)) >= required_verifiers(cfg, t):
            transition(t, "READY_TO_MERGE", "독립 검증 충족")
        return {"id": t["id"], "state": t["state"], "valid_pass": len(valid_evidence(t)),
                "required": required_verifiers(cfg, t)}


def cmd_fail(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] not in ("IMPLEMENTING", "VERIFYING", "READY_TO_MERGE"):
            raise Refused(f"{t['id']}는 {t['state']} 상태라 재작업으로 돌릴 수 없다")
        return {"id": t["id"], "state": fail(cfg, t, a.reason, kind="manual"), "retries": t["retries"]}


def cmd_block(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        transition(t, "BLOCKED", a.reason)
        return brief(t)


def cmd_unblock(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] != "BLOCKED":
            raise Refused(f"{t['id']}는 BLOCKED가 아니다")
        target = "RETRY" if t["branch"] else "PENDING" if any(needs(cfg, t)) else "READY"
        if a.reset_retries:
            t["retries"] = 0
        transition(t, target, a.reason)
        return brief(t)


def cmd_cancel(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        transition(t, "CANCELLED", a.reason)
        return brief(t)


def cmd_paths(a, root, cfg):
    """구현 시작 전에 작업의 담당 경로를 바꾼다. 진행 중인 다른 작업과의 경로 충돌을 검사한다."""
    reason = (a.reason or "").strip()
    if not reason:
        raise Refused("경로 변경 사유(--reason)가 필요하다")
    new_paths = validate_paths(a.set)
    with store(root) as st:
        t = get(st, a.id)
        ok, why = can_edit_paths(t)
        if not ok:
            raise Refused(why)
        conflict = path_conflict(st, dict(t, paths=new_paths))
        if conflict:
            raise Refused(conflict)
        old_paths = t["paths"]
        t["paths"] = new_paths
        t["updated"] = now()
        t["history"].append({
            "at": now(),
            "from": t["state"],
            "to": t["state"],
            "action": "paths",
            "old_paths": old_paths,
            "paths": new_paths,
            "reason": reason,
        })
        return brief(t)


def cmd_sync(a, root, cfg):
    wt = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    if verify.same_path(wt, root):
        raise Refused("작업 worktree에서 실행한다")
    with store(root, write=False) as st:
        t = get(st, a.id)
        sources = [st["integration_branch"]]
    current = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    if t["branch"] and t["branch"] != current and branch_head(root, t["branch"]):
        sources.append(t["branch"])
    for src in sources:
        code, out = run_git(wt, "merge", "--no-edit", src)
        if code != 0:
            files = git(wt, "diff", "--name-only", "--diff-filter=U").splitlines()
            return {"result": "CONFLICT", "merging": src, "files": files,
                    "next": "충돌을 풀고 `git add` 후 `git commit`한다. 그다음 테스트하고 다시 submit한다"}
    return {"result": "CLEAN", "merged": sources}


def cmd_status(a, root, cfg):
    with store(root, write=False) as st:
        tasks = list(st["tasks"].values())
        counts = {}
        for t in tasks:
            counts[t["state"]] = counts.get(t["state"], 0) + 1
        slots = {p: {"used": used(st, p), "limit": limit(st, cfg, p)} for p in SLOT_STATE}
        ready, _ = dispatchable(st, cfg, "implement")
        out = {"integration_branch": st["integration_branch"], "counts": counts, "slots": slots,
               "ready_now": [t["id"] for t in ready]}
        if a.json:
            out["tasks"] = tasks
            return out
        if not a.brief or tasks:
            print(f"통합 브랜치: {st['integration_branch']}  슬롯: " +
                  ", ".join(f"{p} {v['used']}/{v['limit']}" for p, v in slots.items()))
            for t in sorted(tasks, key=lambda t: t["id"]):
                extra = f" ← {t['deps']}" if t["deps"] and t["state"] in ("PENDING", "READY") else ""
                why = f"  ({t['reason'][:70]})" if t["state"] in ("BLOCKED", "RETRY") and t["reason"] else ""
                print(f"  {t['id']:<10} {t['state']:<15} {t['profile']:<9} {t['risk']:<6} "
                      f"재시도 {t['retries']}  {t['title'][:40]}{extra}{why}")
            print(f"지금 시작할 수 있는 구현 작업: {out['ready_now']}")
        return None


def cmd_recover(a, root, cfg):
    actions = [f"임시 통합 worktree 제거: {n}" for n in prune_temp(root)]
    lock = state_dir(root) / "integrate.lock"
    integrating = lock.exists() and time.time() - lock.stat().st_mtime <= cfg["integration_lock_stale_s"]
    with store(root) as st:
        integ = st["integration_branch"]
        for t in st["tasks"].values():
            before = t["state"]
            head_now = branch_head(root, t["branch"]) if t["branch"] else None
            merged = bool(t["head"] and integ and is_ancestor(root, t["head"], integ))
            if before == "MERGING" and not integrating:
                if merged:
                    transition(t, "COMPLETED", "복구: 통합 브랜치에 이미 들어 있다", merged_sha=git(root, "rev-parse", integ))
                else:
                    transition(t, "READY_TO_MERGE", "복구: 통합이 반영되지 않고 중단됐다")
            elif before in ("VERIFYING", "READY_TO_MERGE"):
                if merged:
                    transition(t, "COMPLETED", "복구: 통합 브랜치에 이미 들어 있다", force=True)
                elif head_now is None:
                    transition(t, "BLOCKED", f"복구: 작업 브랜치 {t['branch']}가 없다", force=True)
                elif head_now != t["head"]:
                    fail(cfg, t, "복구: 제출 뒤 브랜치가 바뀌었다. 다시 submit해야 한다", count=False)
            elif before == "COMPLETED" and t["head"] and not merged:
                transition(t, "BLOCKED", "복구: 완료로 기록됐지만 통합 브랜치에 커밋이 없다", force=True)
            elif a.reset_running and before == "IMPLEMENTING":
                fail(cfg, t, "복구: 세션이 끊겨 실행 중이던 구현이 중단됐다", count=False)
            elif a.reset_running and before in ("RESEARCHING", "DESIGNING", "PREVIEWING"):
                transition(t, "PENDING", "복구: 세션이 끊겨 조사·설계·미리보기가 중단됐다")
            if t["state"] != before:
                actions.append(f"{t['id']}: {before} → {t['state']} ({t['reason']})")
    return {"actions": actions}


def checked_out(root: Path) -> dict:
    """브랜치 → 그 브랜치를 체크아웃한 worktree 경로(e2e6 E6-3: 옛 브랜치가 옛 worktree에 남아 branch -d가 실패했다)."""
    out, cur = {}, None
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            cur = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            out[line[len("branch refs/heads/"):]] = cur
    return out


def same_path(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return Path(a).resolve() == Path(b).resolve()


def cmd_cleanup(a, root, cfg):
    results = {}
    with store(root) as st:
        integ = st["integration_branch"]
        busy = {t["worktree"] for t in st["tasks"].values() if t["state"] not in ("COMPLETED", "CANCELLED")}
        holders = checked_out(root)
        for t in st["tasks"].values():
            if (a.ids and t["id"] not in a.ids) or t["state"] != "COMPLETED" or t["cleaned"]:
                continue
            wt, problems = t["worktree"], []
            # 옛 브랜치를 아직 체크아웃한 옛 worktree(본 체크아웃·지금 worktree는 빼고)
            old_wts = [holders[b] for b in t["old_branches"]
                       if b in holders and not same_path(holders[b], str(root)) and not (wt and same_path(holders[b], wt))]
            if not is_ancestor(root, t["head"], integ):
                problems.append("작업 커밋이 통합 브랜치에 없다")
            if t["branch"] in cfg["protected_branches"] or t["branch"] == integ:
                problems.append("보호 대상 브랜치다")
            if wt in busy or any(w in busy for w in old_wts):
                problems.append("다른 진행 중 작업이 같은 worktree를 쓴다")
            for w in [wt] + old_wts:
                if w and Path(w).is_dir() and git(Path(w), "status", "--porcelain"):
                    problems.append(f"worktree에 커밋 안 된 변경이나 추적 안 된 파일이 있다: {w}")
            if branch_head(root, t["branch"]) not in (None, t["head"]):
                problems.append("브랜치에 통합되지 않은 새 커밋이 있다")
            if problems:
                results[t["id"]] = {"cleaned": False, "why": problems}
                continue
            try:  # 한 작업이 실패해도 나머지는 정리하고, 실패한 작업은 사유와 함께 cleaned=false로 남긴다(다음에 다시)
                for w in [wt] + old_wts:
                    if w and Path(w).is_dir():
                        git(root, "worktree", "remove", w)
                for b in [t["branch"]] + t["old_branches"]:
                    if branch_head(root, b) and is_ancestor(root, b, integ):
                        git(root, "branch", "-d", b)
            except Refused as e:
                results[t["id"]] = {"cleaned": False, "why": [str(e)]}
                continue
            t["cleaned"] = True
            results[t["id"]] = {"cleaned": True}
    return results


def cmd_metrics(a, root, cfg):
    with store(root, write=False) as st:
        tasks = list(st["tasks"].values())
        impl_limit = limit(st, cfg, "implement")
    per_state, lead, spans = {}, [], []
    for t in tasks:
        marks = [(datetime.fromisoformat(t["created"]), "PENDING")] + \
                [(datetime.fromisoformat(h["at"]), h["to"]) for h in t["history"]]
        marks.append((datetime.now().astimezone(), None))
        for (start, state), (end, _) in zip(marks, marks[1:]):
            if state in ("COMPLETED", "CANCELLED"):
                break
            per_state[state] = per_state.get(state, 0.0) + (end - start).total_seconds()
            if state == "IMPLEMENTING":
                spans.append((start, end))
        if t["completed"]:
            lead.append((datetime.fromisoformat(t["completed"]) - datetime.fromisoformat(t["created"])).total_seconds())
    points = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
    peak = running = 0
    for _, delta in points:
        running += delta
        peak = max(peak, running)
    wall = (max(e for _, e in spans) - min(s for s, _ in spans)).total_seconds() if spans else 0
    kinds = [h.get("kind") for t in tasks for h in t["history"]]
    done = sum(1 for t in tasks if t["state"] == "COMPLETED")
    evid = [e for t in tasks for e in t["evidence"]]
    return {
        "tasks": len(tasks), "completed": done,
        "completion_rate": round(done / len(tasks), 2) if tasks else None,
        "seconds_by_state": {k: round(v) for k, v in sorted(per_state.items())},
        "avg_lead_time_s": round(sum(lead) / len(lead)) if lead else None,
        "implement_peak_parallel": peak, "implement_slot_limit": impl_limit,
        "implement_slot_utilization": round(sum((e - s).total_seconds() for s, e in spans) / (wall * impl_limit), 2)
        if wall else None,
        "retries_total": sum(t["retries"] for t in tasks),
        "verify_fail_rate": round(sum(1 for e in evid if e["verdict"] == "FAIL") / len(evid), 2) if evid else None,
        "merge_conflicts": kinds.count("conflict"), "integration_test_failures": kinds.count("integration_test"),
        "queue": {s: sum(1 for t in tasks if t["state"] == s) for s in ("AWAITING_APPROVAL", "READY", "VERIFYING", "READY_TO_MERGE")},
        "tokens": "unavailable (Claude Code가 Hook·스크립트에 토큰 사용량을 주지 않는다. /cost로 확인)",
    }


def cmd_limit(a, root, cfg):
    if a.pool not in cfg["slots"] or a.n < 1:
        raise Refused(f"pool은 {sorted(cfg['slots'])} 중 하나, N은 1 이상이다")
    with store(root) as st:
        st["limits"][a.pool] = a.n
        return {"limits": st["limits"]}


def cmd_notes_dir(a, root, cfg):
    with store(root, write=False) as st:
        get(st, a.id)
    d = state_dir(root) / "notes" / a.id
    d.mkdir(parents=True, exist_ok=True)
    return {"notes_dir": str(d)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, *args):
        p = sub.add_parser(name)
        for flags, kw in args:
            p.add_argument(*flags, **kw)
        p.set_defaults(fn=fn)

    ID = (("id",), {})
    REASON = (("--reason",), {"default": ""})
    cmd("init", cmd_init)
    cmd("add", cmd_add, (("--id",), {"required": True}), (("--title",), {"required": True}),
        (("--criteria",), {"required": True}), (("--paths",), {"required": True}), (("--forbid",), {"default": ""}),
        (("--deps",), {"default": ""}), (("--priority",), {"type": int, "default": 0}),
        (("--risk",), {"default": "medium"}), (("--profile",), {"default": "adaptive"}),
        (("--needs-research",), {"action": "store_true"}), (("--preview",), {"action": "store_true"}))
    cmd("load", cmd_load, (("file",), {}))
    cmd("next", cmd_next, (("--pool",), {"required": True, "choices": ["research", "design", "preview", "approval", "implement", "verify", "merge"]}))
    cmd("start", cmd_start, ID, (("--pool",), {"required": True, "choices": list(SLOT_STATE)}),
        (("--agent",), {"required": True}))
    cmd("done", cmd_done, ID, (("--notes",), {"default": ""}))
    cmd("preview", cmd_preview, ID, (("--file",), {"required": True}), (("--summary",), {"default": ""}))
    cmd("approve", cmd_approve, ID, (("--version",), {"type": int, "default": 0}))
    cmd("revise", cmd_revise, ID, (("--feedback",), {"required": True}))
    cmd("dashboard", cmd_dashboard)
    cmd("submit", cmd_submit, ID)
    cmd("evidence", cmd_evidence, ID, (("--verifier",), {"required": True}),
        (("--verdict",), {"required": True, "choices": ["PASS", "FAIL"]}), (("--sha",), {"required": True}),
        (("--scope",), {"default": ""}), (("--notes",), {"default": ""}))
    cmd("fail", cmd_fail, ID, (("--reason",), {"required": True}))
    cmd("block", cmd_block, ID, (("--reason",), {"required": True}))
    cmd("unblock", cmd_unblock, ID, REASON, (("--reset-retries",), {"action": "store_true"}))
    cmd("cancel", cmd_cancel, ID, REASON)
    cmd("paths", cmd_paths, ID, (("--set",), {"required": True}), (("--reason",), {"required": True}))
    cmd("sync", cmd_sync, ID)
    cmd("status", cmd_status, (("--json",), {"action": "store_true"}), (("--brief",), {"action": "store_true"}))
    cmd("recover", cmd_recover, (("--reset-running",), {"action": "store_true"}))
    cmd("cleanup", cmd_cleanup, (("ids",), {"nargs": "*"}))
    cmd("metrics", cmd_metrics)
    cmd("limit", cmd_limit, (("pool",), {}), (("n",), {"type": int}))
    cmd("notes-dir", cmd_notes_dir, ID)
    for stream in (sys.stdout, sys.stderr):  # -h·인자 오류도 UTF-8로(Windows 콘솔 인코딩이면 한글이 깨진다)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    a = ap.parse_args()
    try:
        out = a.fn(a, main_root(), config())
    except Refused as e:
        print(f"[sched] 거부: {e}", file=sys.stderr)
        return 2
    except Busy as e:
        print(f"[sched] 거부: 상태 잠금을 얻지 못했다({e}). 잠시 뒤 다시 실행한다", file=sys.stderr)
        return 2
    if out is not None:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
