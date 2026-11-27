"""sched 규칙: 상태 전이·의존성·담당 경로·슬롯·검증 증거·결정·배정 후보. 상태(dict)를 받아 판단만 한다(파일을 쓰지 않는다)."""

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
import verify  # noqa: E402,F401
from sched_store import *  # noqa: E402,F401,F403
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
DESIGN_DEPTHS = ("light", "standard", "deep")  # asterbraid 추가: 설계 필요성을 위험도와 따로 정한다
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")  # 경로(previews/<ID>)와 marker에 그대로 쓴다
DECISION_KINDS = ("ui", "ux", "architecture", "data", "breaking", "dependency", "requirements", "direction",
                  "environment", "limit", "other")
# asterbraid 추가(4-5): 자리 비움 중 추천안으로 정해도 되는 결정 종류. 의존성·호환 깨짐·환경·데이터·한도는 사람이 정한다
ADOPTABLE_KINDS = ("ui", "ux", "direction", "architecture", "requirements")
DECISION_POLICIES = ("direct", "away", "all")
REVIEW_MODES = ("off", "deep", "ask")
HOLDS = ("start", "merge")  # 결정이 막는 단계: start(시작·통합 모두) / merge(통합만, 구현·테스트는 진행)
TMP_DIR = ".asterbraid-tmp"  # notes-dir: 체크아웃마다 두는 임시 폴더(git이 무시)
NOTE_KINDS = ("strategy", "priority", "decision", "tech", "alternative", "split", "risk", "resolved", "milestone",
              "blocker", "plan")
SNAPSHOT_SCHEMA = 1
NOTES_KEPT = 200


# ---------- 규칙 ----------

def decision_policy(st: dict) -> str:
    """옛 상태의 away는 그대로 읽고, 전체 위임만 별도 명시 상태로 둔다."""
    return "all" if st.get("decision_delegation") else "away" if st.get("away") else "direct"


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
    return (prof["research"] and not t["researched"],
            ((prof["design"] or bool(t.get("design_depth"))) and not t["designed"]) or review_pending(t),
            t["preview"]["required"] and t["preview"]["approved"] is None)


def review_pending(t: dict) -> bool:
    """asterbraid 추가(5-19): 설계는 끝났지만 설계 검토 PASS(또는 사용자가 정한 건너뛰기)를 아직 못 받았다."""
    return (bool(t.get("review_needed")) and t["designed"]
            and (t.get("design_review") or {}).get("verdict") not in ("PASS", "SKIP"))


def review_mode(st: dict, cfg: dict) -> str:
    mode = st.get("design_review") or cfg["design_review"]
    if mode not in REVIEW_MODES:
        raise Refused(f"design_review는 {REVIEW_MODES} 중 하나다: {mode}")
    return mode


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


def open_decisions(st: dict, t: dict, stage: str = "merge") -> list:
    """이 작업에 걸린 열린 결정. stage="start"면 통합만 막는 결정(hold=merge)은 뺀다."""
    return sorted(d["id"] for d in st.get("decisions", {}).values() if d["state"] == "OPEN" and t["id"] in d["tasks"]
                  and (stage == "merge" or d.get("hold", "start") == "start"))


def hold_reason(st: dict, t: dict, stage: str = "start") -> str | None:
    """사용자 결정이 이 작업에 걸려 있으면 그 사유. 결정과 무관한 작업은 계속 진행한다.
    hold=merge 결정은 시작·구현·검증은 두고 통합만 막는다(asterbraid 추가, e2e5)."""
    ids = open_decisions(st, t, stage)
    return f"사용자 결정 대기: {ids}" if ids else None


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
    if not SAFE_ID.match(str(spec["id"])):
        raise Refused(f"작업 ID는 영문·숫자로 시작하고 영문·숫자·_.-만 쓴다(64자 이하): {spec['id']!r}")
    risk = spec.get("risk", "medium")
    if risk not in RISKS:
        raise Refused(f"risk는 {RISKS} 중 하나다: {risk}")
    profile = spec.get("profile", "adaptive")
    if profile == "adaptive":
        profile = "deep" if risk == "high" else "research" if spec.get("needs_research") else \
            "fast" if risk == "low" else "standard"
    if profile not in cfg["profiles"]:
        raise Refused(f"모르는 프로파일: {profile}")
    depth = spec.get("needs_design") or None
    depth = "standard" if depth is True else depth
    if depth is None and cfg["profiles"][profile]["design"]:
        depth = "deep" if profile == "deep" else "standard"
    if depth is not None and depth not in DESIGN_DEPTHS:
        raise Refused(f"설계 깊이는 {DESIGN_DEPTHS} 중 하나다: {depth}")
    paths = validate_paths(spec["paths"])
    t = {"id": str(spec["id"]), "title": spec["title"], "criteria": spec["criteria"], "paths": paths,
         "forbid": [norm(p) for p in spec.get("forbid", [])], "deps": [str(d) for d in spec.get("deps", [])],
         "priority": int(spec.get("priority", 0)), "risk": risk, "profile": profile, "state": "PENDING",
         "researched": False, "designed": False, "design_depth": depth, "agent": None, "branch": None, "worktree": None,
         "base": None, "head": None, "old_branches": [], "evidence": [], "notes": [], "retries": 0, "attempts": 0,
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


def dispatchable(st: dict, cfg: dict, pool: str) -> tuple[list, list]:
    picks, waiting = [], []
    tasks = sorted(st["tasks"].values(), key=lambda t: (-t["priority"], t["created"], t["id"]))
    if pool == "merge":
        ready = [t for t in tasks if t["state"] == "READY_TO_MERGE"]
        return [t for t in ready if not hold_reason(st, t, "merge")], \
            [{"id": t["id"], "why": hold_reason(st, t, "merge")} for t in ready if hold_reason(st, t, "merge")]
    if pool == "approval":
        return [t for t in tasks if t["state"] == "AWAITING_APPROVAL"], []
    if pool == "review":  # asterbraid 추가(5-19): 설계 검토를 기다리는 작업(슬롯 없음, verifier가 맡는다)
        return [t for t in tasks if t["state"] == "PENDING" and review_pending(t)], []
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
            ok, why = t["state"] == "PENDING" and need_d and not need_r and not t["designed"], None
        elif pool == "preview":
            ok, why = t["state"] == "PENDING" and need_p and not need_r and not need_d, None
        else:
            ok = t["state"] in ("READY", "RETRY")
            deps = waiting_deps(st, t) if ok else []
            why = f"선행 작업 미완료: {deps}" if deps else path_conflict(st, t, picks) if ok else None
        if not ok:
            continue
        why = why or hold_reason(st, t) or (f"일시 중지: {st['paused'].get('reason') or '사유 없음'}"
                                            if st.get("paused") else None)
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


def assigned_verifiers(t: dict) -> list:
    """지금 제출된 커밋에 배정된 verifier 이름(바꿔 띄운 것은 뺀다)."""
    return [v["agent"] for v in t.get("verifiers", []) if v["sha"] == t.get("head") and not v.get("replaced")]


def attention(st: dict) -> list:
    """asterbraid 추가(e2e5 E5-1): 어느 풀을 물어도 놓치면 안 되는 것(재작업, 사용자 결정)을 함께 보인다."""
    out = [{"id": t["id"], "state": t["state"], "why": t["reason"]}
           for t in sorted(st["tasks"].values(), key=lambda t: t["id"]) if t["state"] == "RETRY"]
    for d in sorted(st["decisions"].values(), key=lambda x: x["created"]):
        if d["state"] == "OPEN":
            out.append({"id": d["id"], "state": "DECISION", "why": f"사용자 결정 대기 {d['id']}: {d['title']} "
                                                                   f"(작업 {','.join(d['tasks']) or '없음'})"})
    return out


def project_map(root: Path, kind: str = "MAP") -> str | None:
    """asterbraid 추가(E5-9): 최신 프로젝트 지도(MAP)·코드 관례(CONVENTIONS) 경로. 지시문에 넣는다."""
    maps = sorted((root / "docs" / "asterbraid" / "research").glob(f"*-{kind}.md"))
    return maps[-1].relative_to(root).as_posix() if maps else None


def marker(task_id: str, pool: str, attempt: int) -> str:
    """에이전트 지시문 첫 줄에 넣는다. Mod는 이 줄로만 에이전트와 작업을 연결한다."""
    return f"[auto-task id={task_id} pool={pool} attempt={attempt}]"


def preview_dir(root: Path) -> Path:
    return state_dir(root) / "previews"
