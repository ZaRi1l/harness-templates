#!/usr/bin/env python3
"""asterbraid 작업 스케줄러 (표준 라이브러리만 사용, Python 3.8+).

작업 상태를 <git common dir>/claude-auto/state.json 에 둔다. 모든 변경은 잠금 + 원자적 교체로 기록하고,
상태 전이·의존성·담당 경로·실행 슬롯·검증 증거 규칙은 이 프로그램이 강제한다. 에이전트는 상태 파일을
직접 고치지 않고 아래 명령만 쓴다. 어느 worktree에서 실행해도 같은 저장소의 상태를 본다.
상태를 기록할 때마다 같은 잠금 안에서 읽기 전용 스냅샷(dashboard.json)도 기록한다. Mod는 이것만 읽는다.

  init                         통합 브랜치(메인 체크아웃의 현재 브랜치)를 기록한다
  add / load FILE              작업 추가(하나 / JSON 목록 일괄). 의존성 순환·중복 ID를 거부한다
  next --pool P                지금 시작할 수 있는 작업(research|design|review|preview|approval|implement|verify|merge).
                               attention(재작업·열린 결정), map·conventions(프로젝트 지도·코드 관례 경로)도 함께 돌려준다
  start ID --pool P --agent A  작업을 배정한다(중복 배정·경로 충돌·슬롯 초과·미완료 의존·결정 대기·일시 중지를 거부한다).
                               에이전트 지시문 첫 줄에 넣을 marker와 attempt 번호를 돌려준다. 메인 체크아웃에서만 한다
  start ID --pool verify --agent V [--replace OLD]   제출된 커밋에 verifier 배정(필요한 수까지). 이 이름의 증거만 센다
  done ID [--notes PATH] [--close]   조사·설계 단계 완료(--close: 구현 없이 작업 완료, 노트 파일 필요)
  design-review ID --verifier V --verdict PASS|FAIL [--notes] / --skip --decision D   deep 설계 검토(FAIL → 다시 설계)
  review-mode off|deep|ask     설계 검토 설정(deep: deep 설계는 검토 PASS 뒤 구현, ask: 사용자에게 묻고 --skip 가능)
  deps ID --set A,B            구현 시작 전에 의존을 바꾼다(순환 검사, 승인·노트는 그대로)
  preview ID --file F          (designer) 미리보기 버전 등록 → AWAITING_APPROVAL
  approve ID [--version N --sha S] / revise ID [--version N] --feedback "..."
                               사용자의 승인 / 수정 요청을 기록한다(버전·해시가 사용자가 본 것과 같아야 한다)
  dashboard                    미리보기와 전체 작업 현황을 한 HTML로 만든다
  decide add|answer|cancel     사용자 의사결정 기록. 열린 결정에 묶인 작업만 시작·통합을 멈춘다
                               add [--hold merge]: 통합만 막는다 / [--ask-user]: 자리 비움 중에도 adopt 안 함
  decide adopt ID              (자리 비움 중) 위임된 종류의 결정을 추천안으로 정한다(by away)
  away [--previews] / back     사용자 자리 비움 켜기(--previews: 미리보기 최신 버전 approve --by away 허용) / 복귀
  away-review KEY --keep|--redo   자리 비운 동안 정한 것 확인: 그대로 둠 / 같은 선택지로 결정을 다시 연다
  pause [--reason] / resume    새 배정만 멈춘다 / 다시 시작한다(실행 중인 에이전트는 그대로)
  priority ID N / note --kind K --text T   우선순위 변경 / manager 설명 기록
  snapshot                     스냅샷(dashboard.json)을 다시 만든다
  submit ID                    (작업 worktree에서) 구현 제출 → VERIFYING. 담당 경로 밖 변경을 거부한다
  evidence ID --verifier V --verdict PASS|FAIL --sha SHA   독립 검증 증거 기록
  fail / block / unblock / cancel ID                        재작업·보류·해제·취소(승인된 미리보기가 있으면 cancel --force)
  sync ID                      (작업 worktree에서) 통합 브랜치와 이전 작업 브랜치를 병합해 온다
  status [--json] / metrics    현황 / 소요 시간·실패율 등
  recover [--reset-running]    저장된 상태를 실제 Git 상태와 대조해 고친다(세션 재시작 때)
  cleanup [ID...] [--dry-run]  통합이 끝난 작업의 worktree·브랜치를 조건을 확인하고 지운다(--dry-run: 목록만)
  limit POOL N / notes-dir ID  슬롯 수 조절 / 작업별 임시 폴더(부른 체크아웃 안 .asterbraid-tmp/ID, git이 무시)

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
from sched_store import *  # noqa: E402,F401,F403  (내부를 나눴다. 이름은 sched에서 그대로 보인다)
from sched_rules import *  # noqa: E402,F401,F403
from sched_view import *  # noqa: E402,F401,F403



@contextlib.contextmanager
def store(root: Path, write: bool = True, recovery_read: bool = False):
    """상태를 잠그고 읽는다. 블록이 예외 없이 끝났을 때만 원자적으로 기록하고, 이어서 스냅샷을 기록한다."""
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    with FileLock(d / "state.lock", timeout=30, stale=120):
        path = d / "state.json"
        st = json.loads(path.read_text(encoding="utf-8")) if path.exists() else \
            {"version": 1, "integration_branch": None, "limits": {}, "tasks": {}}
        for key, default in (("paused", None), ("decisions", {}), ("notes_log", []), ("away", None), ("away_log", [])):
            st.setdefault(key, default)
        if st.get("engine", 0) > ENGINE and not (recovery_read and not write):
            raise Refused(f"이 sched.py(엔진 {ENGINE})는 상태를 쓴 sched(엔진 {st['engine']})보다 옛것이다(하네스 업데이트 뒤의 옛 worktree). "
                          "worktree면 `sched sync`로 통합 브랜치의 새 하네스를 받은 뒤 다시 한다")
        yield st
        if write:
            st["engine"] = ENGINE
            atomic_write(path, st)
            write_snapshot(root, st)


def write_snapshot(root: Path, st: dict) -> Path:
    """읽기 전용 스냅샷을 기록한다. 실패해도 상태 기록은 이미 끝났으니 오류 파일만 남긴다."""
    d = state_dir(root)
    out = d / "dashboard.json"
    try:
        atomic_write(out, snapshot(root, config(), st), indent=None)
        with contextlib.suppress(OSError):
            (d / "dashboard.error.json").unlink()
    except Exception as e:  # noqa: BLE001  스냅샷 실패가 작업 상태 기록을 되돌리면 안 된다
        with contextlib.suppress(Exception):
            atomic_write(d / "dashboard.error.json", {"at": now(), "error": f"{type(e).__name__}: {e}"})
    return out


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
            "needs_design": a.needs_design, "preview": a.preview}
    with store(root) as st:
        ids = insert(st, cfg, [spec])
        return {"added": ids, "state": st["tasks"][ids[0]]["state"], "profile": st["tasks"][ids[0]]["profile"]}


def cmd_load(a, root, cfg):
    specs = json.loads(Path(a.file).read_text(encoding="utf-8"))
    with store(root) as st:
        return {"added": insert(st, cfg, specs)}


def cmd_next(a, root, cfg):
    with store(root, write=False) as st:
        picks, waiting = dispatchable(st, cfg, a.pool)
        out = {"pool": a.pool, "dispatch": [{**brief(t), "design_depth": t.get("design_depth")} for t in picks],
               "waiting": waiting, "attention": attention(st), "map": project_map(root),
               "conventions": project_map(root, "CONVENTIONS")}
        if out["map"] and not out["conventions"]:  # e2e6 E6-8: 관례 문서가 생기기 전 지도만 만든 프로젝트(업데이트 뒤)
            out["attention"].append({"id": "CONVENTIONS", "state": "MISSING", "why": "지도(MAP)는 있는데 코드 관례(CONVENTIONS) 문서가 없다. "
                                     "researcher 조사 작업 하나로 관례 문서만 한 번 만든다"})
        if a.pool in SLOT_STATE:
            out["free_slots"] = limit(st, cfg, a.pool) - used(st, a.pool)
        if a.pool == "verify":
            for item, t in zip(out["dispatch"], picks):
                busy = [v for v in assigned_verifiers(t) if v not in {e["verifier"] for e in valid_evidence(t)}]
                item["verifiers_needed"] = max(0, required_verifiers(cfg, t) - len(valid_evidence(t)) - len(busy))
                item["verifiers_assigned"] = assigned_verifiers(t)
        if a.pool == "review":
            for item, t in zip(out["dispatch"], picks):
                item["marker"] = marker(t["id"], "review", t.get("design_rounds", 0) + 1)
        return out


def cmd_start(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if not st["integration_branch"]:
            raise Refused("먼저 `sched.py init`으로 통합 브랜치를 기록한다")
        here = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
        if not verify.same_path(here, root):  # asterbraid 추가(e2e5 E5-5): 배정은 manager가 메인 체크아웃에서 한다
            raise Refused("start는 메인 체크아웃에서 manager가 한다. 작업 worktree에서는 배정하지 않는다(재작업이면 manager에게 보고)")
        if a.pool == "verify":
            return start_verify(st, cfg, t, a)
        if st.get("paused"):
            raise Refused(f"새 배정이 일시 중지됐다({st['paused'].get('reason') or '사유 없음'}). `sched resume`으로 다시 시작한다")
        held = hold_reason(st, t)
        if held:
            raise Refused(f"{t['id']}: {held}. 결정이 기록된 뒤 시작한다")
        if used(st, a.pool) >= limit(st, cfg, a.pool):
            raise Refused(f"{a.pool} 슬롯이 다 찼다({limit(st, cfg, a.pool)})")
        need_r, need_d, need_p = needs(cfg, t)
        if a.pool == "research":
            if not need_r:
                raise Refused(f"{t['id']}는 조사 단계가 필요 없거나 이미 끝났다")
            transition(t, "RESEARCHING", agent=a.agent)
        elif a.pool == "design":
            if need_r or not need_d or t["designed"]:
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
        t["attempts"] = t.get("attempts", 0) + 1
        t["history"][-1].update(pool=a.pool, attempt=t["attempts"])
        out = {**brief(t), "attempt": t["attempts"], "marker": marker(t["id"], a.pool, t["attempts"])}
        if a.pool in ("research", "design"):  # e2e6 E6-1: 확정 경로를 지시문에 "산출물: <경로>"로 넣는다(자리표시·"가능하면"이 아니라)
            out["output"] = f"docs/asterbraid/{a.pool}/{datetime.now().strftime('%Y-%m-%d-%H%M')}-{t['id']}.md"
        return out


def start_verify(st: dict, cfg: dict, t: dict, a) -> dict:
    """asterbraid 추가(e2e5 E5-2·E5-6·5-16): verifier를 제출된 커밋에 배정한다. 필요한 수보다 더 띄우지 않는다."""
    if t["state"] not in ("VERIFYING", "READY_TO_MERGE"):
        raise Refused(f"{t['id']}는 {t['state']} 상태다. 검증은 VERIFYING(제출 뒤)일 때 배정한다")
    t.setdefault("verifiers", [])
    if a.replace:
        old = [v for v in t["verifiers"] if v["sha"] == t["head"] and v["agent"] == a.replace and not v.get("replaced")]
        if not old:
            raise Refused(f"바꿀 verifier가 이 커밋에 배정돼 있지 않다: {a.replace}")
        old[0]["replaced"] = a.agent
    names = assigned_verifiers(t)
    if a.agent in names:
        raise Refused(f"{a.agent}는 이미 {t['id']}에 배정됐다")
    if len(names) >= required_verifiers(cfg, t):
        raise Refused(f"{t['id']}에는 이미 verifier {len(names)}명({names})이 배정됐다(필요 {required_verifiers(cfg, t)}). "
                      "멈춘 verifier를 바꾸려면 --replace 이전이름")
    t["verifiers"].append({"agent": a.agent, "sha": t["head"], "at": now()})
    return {**brief(t), "attempt": t["attempts"], "marker": marker(t["id"], "verify", t["attempts"]),
            "verifiers_assigned": assigned_verifiers(t), "required": required_verifiers(cfg, t)}


def cmd_done(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if a.close:  # asterbraid 추가: 조사·설계만 하는 작업을 구현 없이 완료로 닫는다
            if not a.notes and not t["notes"]:
                raise Refused(f"{t['id']}: 결과 노트(--notes) 없이 닫지 않는다")
            if t["branch"]:
                raise Refused(f"{t['id']}: 작업 브랜치가 있다. 구현 작업은 검증·통합으로 끝낸다")
            ok = t["state"] in ("RESEARCHING", "DESIGNING") or (
                t["state"] == "BLOCKED" and (t["researched"] or t["designed"]))
            if not ok:
                raise Refused(f"{t['id']}는 조사·설계 단계가 아니다({t['state']})")
            for n in ([a.notes] if a.notes else []) + t["notes"]:
                f = Path(n) if Path(n).is_absolute() else root / n
                if not f.is_file():
                    raise Refused(f"{t['id']}: 노트 파일이 없다: {n}. 결과를 파일로 쓴 뒤 닫는다(메시지만으로는 남지 않는다)")
            if a.notes:
                t["notes"].append(a.notes)
            transition(t, "COMPLETED", "조사·설계만 하는 작업 완료", force=True)
            return brief(t)
        if t["state"] == "RESEARCHING":
            t["researched"] = True
        elif t["state"] == "DESIGNING":
            t["designed"] = True
            mode = review_mode(st, cfg)
            t["review_needed"] = mode != "off" and t.get("design_depth") == "deep"
            t["design_review"] = None
        else:
            raise Refused(f"{t['id']}는 조사·설계 중이 아니다({t['state']})")
        if a.notes:
            t["notes"].append(a.notes)
        transition(t, "PENDING" if any(needs(cfg, t)) else "READY", "단계 완료")
        out = brief(t)
        if review_pending(t):
            out["next"] = ("설계 검토가 필요하다: `sched next --pool review` → verifier(설계 검토)"
                           + (". 설정이 ask다: 먼저 사용자에게 설계 검토를 받을지 결정으로 묻는다" if mode == "ask" else ""))
        return out


def cmd_design_review(a, root, cfg):
    """asterbraid 추가(5-19): 설계 검토 판정. FAIL이면 설계로 돌아가고, --skip은 사용자가 답한 결정이 있어야 한다."""
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] != "PENDING" or not review_pending(t):
            raise Refused(f"{t['id']}는 설계 검토를 기다리는 작업이 아니다({t['state']})")
        if a.skip:
            d = st["decisions"].get(a.decision or "")
            if not d or d["state"] != "ANSWERED" or t["id"] not in d["tasks"]:
                raise Refused(f"설계 검토를 건너뛰려면 이 작업에 묶인 답한 결정(--decision)이 필요하다: {a.decision or '없음'}")
            t["design_review"] = {"verdict": "SKIP", "by": d.get("by"), "decision": d["id"], "at": now(), "notes": a.notes}
        else:
            if not a.verifier or a.verdict not in ("PASS", "FAIL"):
                raise Refused("--verifier 이름과 --verdict PASS|FAIL을 준다(건너뛰기는 --skip --decision ID)")
            t["design_review"] = {"verdict": a.verdict, "by": a.verifier, "at": now(), "notes": a.notes}
            t.setdefault("design_reviews", []).append(t["design_review"])
            if a.verdict == "FAIL":
                t["design_rounds"] = t.get("design_rounds", 0) + 1
                t["designed"] = False
                if t["design_rounds"] > cfg["max_retries"]:
                    transition(t, "BLOCKED", f"설계 검토 FAIL이 한도({cfg['max_retries']})를 넘었다: {a.notes}")
                    return brief(t)
                t["history"].append({"at": now(), "from": "PENDING", "to": "PENDING", "kind": "design_fail",
                                     "reason": f"설계 검토 FAIL({a.verifier}): {a.notes}"})
                t["reason"] = f"설계 검토 FAIL: {a.notes}"
                return brief(t)
        why = f"설계 검토 {t['design_review']['verdict']}"
        if any(needs(cfg, t)):  # 미리보기가 남았으면 PENDING 그대로
            t["history"].append({"at": now(), "from": "PENDING", "to": "PENDING", "reason": why})
        else:
            transition(t, "READY", why)
        return brief(t)


def cmd_review_mode(a, root, cfg):
    with store(root) as st:
        st["design_review"] = a.mode
        return {"design_review": a.mode}


def cmd_preview(a, root, cfg):
    src = Path(a.file)
    if src.suffix.lower() not in (".html", ".htm"):  # 대시보드·Mod가 OS 기본 프로그램으로 연다. 실행 파일이면 안 된다
        raise Refused(f"미리보기는 .html/.htm 한 파일이어야 한다: {src.name}")
    if not src.is_file():
        raise Refused(f"미리보기 파일이 없다: {src}")
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] != "PREVIEWING":
            raise Refused(f"{t['id']}는 {t['state']} 상태라 미리보기를 등록할 수 없다")
        n = len(t["preview"]["versions"]) + 1
        dest = preview_dir(root) / t["id"] / f"v{n}{src.suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(src), str(dest))  # 이전 버전을 덮어쓰지 않는다
        t["preview"]["versions"].append({"n": n, "file": str(dest), "at": now(), "summary": a.summary,
                                         "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()})
        transition(t, "AWAITING_APPROVAL", f"미리보기 v{n} 검토 대기")
        return {"id": t["id"], "version": n, "file": str(dest), "state": t["state"]}


def cmd_approve(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if a.by == "away":  # asterbraid 추가(4-5): 사용자가 미리보기까지 위임했을 때만 추천안(최신 버전)을 승인한다
            if not st["away"]:
                raise Refused("자리 비움 중이 아니다. by away 승인은 사용자가 자리 비움을 켰을 때만 한다")
            if not st["away"].get("previews"):
                raise Refused("미리보기 자동 승인은 위임받지 않았다(sched away --previews). 사용자 승인을 기다린다")
        if t["state"] != "AWAITING_APPROVAL":
            raise Refused(f"{t['id']}는 승인 대기 중이 아니다({t['state']})")
        versions = [v["n"] for v in t["preview"]["versions"]]
        n = a.version or versions[-1]
        if n not in versions:
            raise Refused(f"미리보기 v{n}이 없다. 있는 버전: {versions}")
        if n != versions[-1]:  # 옛 버전은 수정 요청으로 거절됐다. 오래된 명령·화면으로 승인되지 않게 한다
            raise Refused(f"v{n}이 아니라 v{versions[-1]}이 최신이다. 최신 버전만 승인한다"
                          "(옛 안이 필요하면 designer가 새 버전으로 다시 등록한다)")
        v = next(x for x in t["preview"]["versions"] if x["n"] == n)
        if a.sha:  # 화면에서 본 버전의 해시. 오래된 화면의 승인이 다른 내용에 적용되지 않게 한다
            if len(a.sha) < 8:
                raise Refused("--sha는 8자 이상이어야 한다")
            if not v["sha256"].startswith(a.sha.lower()):
                raise Refused(f"v{n}의 해시가 일치하지 않는다(기록 {v['sha256'][:12]}, 받은 값 {a.sha}). 화면을 새로 고쳐 다시 본다")
        try:
            on_disk = hashlib.sha256(Path(v["file"]).read_bytes()).hexdigest()
        except OSError:
            on_disk = None
        if on_disk != v["sha256"]:
            raise Refused(f"v{n} 파일이 없거나 등록 뒤 바뀌었다: {v['file']}. designer가 새 버전으로 다시 등록해야 한다")
        t["preview"]["approved"] = n
        t["preview"]["approval"] = {"version": n, "sha256": v["sha256"], "at": now(), "by": a.by,
                                    "criteria_sha": hashlib.sha256(t["criteria"].encode("utf-8")).hexdigest()[:16]}
        transition(t, "READY", f"미리보기 v{n} 승인({a.by})")
        if a.by == "away":
            log_away(st, "preview", t["id"], f"{t['title']} 미리보기 v{n} 자동 승인", version=n)
        return brief(t)


def cmd_revise(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] != "AWAITING_APPROVAL":
            raise Refused(f"{t['id']}는 승인 대기 중이 아니다({t['state']})")
        latest = len(t["preview"]["versions"])
        if a.version and a.version != latest:
            raise Refused(f"v{a.version}이 아니라 v{latest}이 최신이다. v{latest}을 보고 다시 판단한다")
        t["preview"]["feedback"].append({"at": now(), "on": latest, "text": a.feedback, "by": a.by})
        transition(t, "PENDING", f"미리보기 수정 요청: {a.feedback}")
        return brief(t)


PREVIEW_UI = HOOKS.parent / "previewUI" / "preview.html"  # 화면 소스. 생성물은 <git common dir>/claude-auto/previews/
DATA_SLOT = '<script id="auto-data" type="application/json">__AUTO_DATA__</script>'


def cmd_dashboard(a, root, cfg):
    """`.claude/previewUI/preview.html`에 데이터만 넣어 기록한다. 화면은 그 파일의 JS가 그린다."""
    if not PREVIEW_UI.is_file():
        raise Refused(f"미리보기 화면 소스가 없다: {PREVIEW_UI}")
    with store(root, write=False) as st:
        tasks = sorted(st["tasks"].values(), key=lambda t: t["id"])
        decisions = sorted(st["decisions"].values(), key=lambda d: d["id"])
    script = "uv run --no-project --offline python .claude/hooks/sched.py"
    previews = []
    for t in tasks:
        if not t["preview"]["required"]:
            continue
        versions = t["preview"]["versions"]
        commands = []
        if t["state"] == "AWAITING_APPROVAL" and versions:
            last = versions[-1]
            commands = [f"{script} approve {t['id']} --version {last['n']} --sha {last['sha256'][:12]}",
                        f'{script} revise {t["id"]} --version {last["n"]} --feedback "..."']
        previews.append({
            "id": t["id"], "title": t["title"], "state": t["state"], "approved": t["preview"]["approved"],
            "versions": [{"n": v["n"], "href": f"{t['id']}/{Path(v['file']).name}", "sha": v["sha256"],
                          "summary": v.get("summary") or ""} for v in versions],
            "feedback": [{"on": f["on"], "text": f["text"]} for f in t["preview"]["feedback"]], "commands": commands})
    data = {
        "generated_at": now(), "script": script,
        "awaiting": sum(1 for t in tasks if t["state"] == "AWAITING_APPROVAL"),
        "decisions_open": sum(1 for d in decisions if d["state"] == "OPEN"),
        "previews": previews, "decisions": decisions,
        "tasks": [{"id": t["id"], "title": t["title"], "state": t["state"], "deps": t["deps"], "retries": t["retries"]}
                  for t in tasks]}
    # `<`를 이스케이프해 데이터가 </script>로 스크립트 블록을 벗어나지 못하게 한다
    blob = json.dumps(data, ensure_ascii=False)
    for raw, safe in (("<", "\\u003c"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        blob = blob.replace(raw, safe)
    template = PREVIEW_UI.read_text(encoding="utf-8")
    if DATA_SLOT not in template:
        raise Refused(f"{PREVIEW_UI}에 데이터 자리({DATA_SLOT})가 없다")
    page = template.replace(DATA_SLOT, DATA_SLOT.replace("__AUTO_DATA__", blob), 1)
    out = preview_dir(root) / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return {"dashboard": str(out), "awaiting_approval": data["awaiting"], "decisions_open": data["decisions_open"]}


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
        if cfg["verify_assignment"] and a.verifier not in assigned_verifiers(t):
            raise Refused(f"{a.verifier}는 이 커밋({sha[:10]})에 배정된 verifier가 아니다(배정: {assigned_verifiers(t)}). "
                          "manager가 `sched start ID --pool verify --agent 이름`으로 배정한 이름으로만 기록한다")
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
        # asterbraid 추가: 승인된 미리보기를 취소·재등록으로 잃지 않게 한다(의존 변경은 `deps`로)
        if t["preview"]["approved"] is not None and not a.force:
            raise Refused(f"{t['id']}에는 승인된 미리보기(v{t['preview']['approved']})가 있다. "
                          "의존만 바꾸려면 `sched deps`를 쓴다. 정말 취소하려면 --force")
        transition(t, "CANCELLED", a.reason)
        return brief(t)


DEPS_LOCKED = {"IMPLEMENTING", "VERIFYING", "READY_TO_MERGE", "MERGING", "COMPLETED", "CANCELLED"}


def cmd_deps(a, root, cfg):
    """asterbraid 추가: 구현을 시작하기 전 작업의 의존을 바꾼다. 미리보기 승인·노트·결정은 그대로다."""
    with store(root) as st:
        t = get(st, a.id)
        if t["state"] in DEPS_LOCKED:
            raise Refused(f"{t['id']}는 {t['state']} 상태라 의존을 바꿀 수 없다(구현을 시작하기 전에만)")
        new = [d.strip() for d in a.set.split(",") if d.strip()]
        for d in new:
            if d not in st["tasks"]:
                raise Refused(f"의존 작업이 없다: {d}")
            if d == t["id"]:
                raise Refused("자기 자신에 의존할 수 없다")
        tasks = {k: (dict(v, deps=new) if k == t["id"] else v) for k, v in st["tasks"].items()}
        check_cycles(tasks)
        old = t["deps"]
        t["deps"] = new
        text = f"의존 변경 {old} → {new}" + (f": {a.reason}" if a.reason else "")
        st["notes_log"] = (st["notes_log"] + [{"at": now(), "kind": "split", "text": text, "task": t["id"]}])[-NOTES_KEPT:]
        return brief(t)


def cmd_sync(a, root, cfg):
    wt = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    if verify.same_path(wt, root):
        raise Refused("작업 worktree에서 실행한다")
    # sync만 최소 정보(작업 브랜치·통합 브랜치)를 읽어 새 엔진을 가져온다. 상태 쓰기는 여전히 거부한다.
    with store(root, write=False, recovery_read=True) as st:
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
        warn = anomalies(st)
        open_dec = sorted(d["id"] for d in st["decisions"].values() if d["state"] == "OPEN")
        out = {"integration_branch": st["integration_branch"], "counts": counts, "slots": slots,
               "ready_now": [t["id"] for t in ready], "paused": st["paused"], "decisions_open": open_dec,
               "anomalies": warn, "warnings": settings_warnings(root)}
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
        if st["paused"]:
            print(f"새 배정 일시 중지: {st['paused'].get('reason') or '사유 없음'} (sched resume)")
        if st["away"]:
            print(f"사용자 자리 비움 중(미리보기 자동 승인 {'예' if st['away'].get('previews') else '아니오'}): "
                  f"{', '.join(ADOPTABLE_KINDS)} 결정은 decide adopt로 추천안 채택")
        if open_dec:
            print(f"사용자 결정 대기: {open_dec}")
        for w in warn:
            print(f"경고: {w['text']}. 원인을 확인한다(에이전트 상태, 같은 오류 반복, 환경)")
        for w in out["warnings"]:
            print(f"경고: {w}")
        return None


def settings_warnings(root: Path) -> list:
    """asterbraid 추가(e2e4 N1): 하네스 Hook이 빠진 채 돌지 않게 알린다. 설정은 사용자가 고친다."""
    try:
        text = (root / ".claude" / "settings.json").read_text(encoding="utf-8")
    except OSError:
        text = ""
    if "verify.py" in text and "sched.py" in text:
        return []
    return ["하네스 Hook이 .claude/settings.json에 없다(검증 Hook·push 거부 없이 돈다). 직접 고치지 말고 "
            "사용자에게 `asterbraid.py check <프로젝트>`와 `update`를 안내한다"]


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
    # asterbraid 추가: --dry-run은 지울 worktree·브랜치만 보여 주고 아무것도 바꾸지 않는다
    with store(root, write=not a.dry_run) as st:
        integ = st["integration_branch"]
        busy = {t["worktree"] for t in st["tasks"].values() if t["state"] not in ("COMPLETED", "CANCELLED")}
        holders = checked_out(root)
        for t in st["tasks"].values():
            if (a.ids and t["id"] not in a.ids) or t["state"] != "COMPLETED" or t["cleaned"]:
                continue
            tmp = root / TMP_DIR / t["id"]  # e2e6 E6-9: 본 체크아웃의 임시 폴더(git이 무시한다)도 함께 지운다
            wt, problems = t["worktree"], []
            if not t["head"] and not t["branch"] and not t["old_branches"] and not wt:
                # asterbraid 추가(e2e4): 조사·설계만 하고 닫은 작업은 지울 것이 없다. 커밋 검사로 멈추지 않는다
                if a.dry_run:
                    results[t["id"]] = {"cleaned": False, "would_remove": {"worktree": None, "branches": []}}
                else:
                    shutil.rmtree(str(tmp), ignore_errors=True)
                    t["cleaned"] = True
                    results[t["id"]] = {"cleaned": True}
                continue
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
            branches = [b for b in [t["branch"]] + t["old_branches"] if branch_head(root, b) and is_ancestor(root, b, integ)]
            if a.dry_run:
                results[t["id"]] = {"cleaned": False, "would_remove": {
                    "worktree": wt if wt and Path(wt).is_dir() else None, "branches": branches,
                    "old_worktrees": [w for w in old_wts if Path(w).is_dir()], "tmp": str(tmp) if tmp.is_dir() else None}}
                continue
            try:  # 한 작업이 실패해도 나머지는 정리하고, 실패한 작업은 사유와 함께 cleaned=false로 남긴다(다음에 다시)
                for w in [wt] + old_wts:
                    if w and Path(w).is_dir():
                        git(root, "worktree", "remove", w)
                for b in branches:
                    git(root, "branch", "-d", b)
            except Refused as e:
                results[t["id"]] = {"cleaned": False, "why": [str(e)]}
                continue
            shutil.rmtree(str(tmp), ignore_errors=True)
            t["cleaned"] = True
            results[t["id"]] = {"cleaned": True}
    return results


def cmd_metrics(a, root, cfg):
    with store(root, write=False) as st:
        return metrics(st, cfg)


def cmd_snapshot(a, root, cfg):
    with store(root, write=False) as st:
        path = write_snapshot(root, st)
    err = state_dir(root) / "dashboard.error.json"
    if err.exists():
        raise Refused(f"스냅샷을 만들지 못했다: {err.read_text(encoding='utf-8')}")
    return {"snapshot": str(path)}


# ---------- 사용자 결정·일시 중지·우선순위·manager 설명 ----------

def split_keyed(items: list, what: str) -> dict:
    out = {}
    for item in items or []:
        key, sep, text = item.partition(":")
        if not sep or not key.strip():
            raise Refused(f"{what}은 'KEY: 내용' 형식이다: {item!r}")
        out[key.strip()] = text.strip()
    return out


def cmd_decide(a, root, cfg):
    a.id = a.id or a.id_opt
    with store(root) as st:
        decisions = st["decisions"]
        if a.action == "add":
            if not SAFE_ID.match(a.id):
                raise Refused(f"결정 ID 형식이 잘못됐다: {a.id!r}")
            if a.id in decisions:
                raise Refused(f"결정 ID가 이미 있다: {a.id}")
            labels = split_keyed(a.option, "--option")
            if len(labels) < 2:
                raise Refused("선택지(--option 'KEY: 설명')를 두 개 이상 준다")
            pros, cons = split_keyed(a.pros, "--pros"), split_keyed(a.cons, "--cons")
            unknown = (set(pros) | set(cons) | ({a.recommend} if a.recommend else set())) - set(labels)
            if unknown:
                raise Refused(f"없는 선택지를 가리킨다: {sorted(unknown)}")
            tasks = [x for x in (a.tasks or "").split(",") if x]
            missing = [x for x in tasks if x not in st["tasks"]]
            if missing:
                raise Refused(f"작업이 없다: {missing}")
            if a.kind not in DECISION_KINDS:
                raise Refused(f"kind는 {DECISION_KINDS} 중 하나다")
            decisions[a.id] = {
                "id": a.id, "title": a.title, "question": a.question, "kind": a.kind, "tasks": tasks,
                "options": [{"key": k, "label": v, "pros": pros.get(k, ""), "cons": cons.get(k, "")}
                            for k, v in labels.items()],
                "recommend": a.recommend or None, "why": a.why, "evidence": [x for x in (a.evidence or "").split(",") if x],
                "state": "OPEN", "created": now(), "choice": None, "note": None, "by": None, "answered_at": None,
                "hold": a.hold, "ask_user": a.ask_user}
            done = [x for x in tasks if st["tasks"][x]["state"] in ("COMPLETED", "MERGING")]
            out = dict(decisions[a.id])
            if done:  # e2e5 5-13: 결정을 늦게 열어 이미 통합된 작업은 이 결정이 막지 못한다
                out["warning"] = f"이미 통합됐거나 통합 중인 작업이 있다: {done}. 답에 따라 후속 작업으로 고친다"
            return out
        d = decisions.get(a.id)
        if d is None:
            raise Refused(f"결정이 없다: {a.id}")
        if d["state"] != "OPEN":
            raise Refused(f"{a.id}는 OPEN이 아니다({d['state']}). 이미 응답·취소된 결정은 바꾸지 않는다. 새 결정을 연다")
        if a.action == "adopt":  # asterbraid 추가(4-5): 자리 비움 중 추천안 채택
            if not st["away"]:
                raise Refused("자리 비움 중이 아니다. 사용자가 답할 때까지 기다린다")
            if d.get("ask_user"):
                raise Refused("사용자가 직접 정한다고 한 결정이다(--ask-user). 사용자가 돌아오면 묻는다")
            if d["kind"] not in ADOPTABLE_KINDS:
                raise Refused(f"{d['kind']} 결정은 위임 범위가 아니다(위임: {', '.join(ADOPTABLE_KINDS)}). 사용자가 돌아오면 묻는다")
            if not d["recommend"]:
                raise Refused("추천안이 없는 결정은 채택할 수 없다. 사용자가 돌아오면 묻는다")
            d.update(state="ANSWERED", choice=d["recommend"], note="자리 비움 중 추천안 채택", by="away", answered_at=now())
            label = next(o["label"] for o in d["options"] if o["key"] == d["recommend"])
            log_away(st, "decision", d["id"], f"{d['title']}: {d['recommend']} ({label})", choice=d["recommend"])
        elif a.action == "answer":
            if a.by == "away":
                raise Refused("자리 비움 중 결정은 decide adopt로 한다(추천안·위임 범위를 확인한다)")
            if a.choice not in [o["key"] for o in d["options"]]:
                raise Refused(f"선택지에 없다: {a.choice}. 선택지: {[o['key'] for o in d['options']]}")
            d.update(state="ANSWERED", choice=a.choice, note=a.note or None, by=a.by, answered_at=now())
        else:
            d.update(state="CANCELLED", reason=a.reason or "", answered_at=now())
        return d


def log_away(st: dict, kind: str, key: str, text: str, **fields) -> None:
    st["away_log"] = (st["away_log"] + [{"at": now(), "kind": kind, "key": key, "text": text, "reviewed": None,
                                         **fields}])[-NOTES_KEPT:]


def cmd_away(a, root, cfg):
    with store(root) as st:
        st["away"] = {"at": now(), "previews": a.previews, "reason": a.reason}
        return {"away": st["away"], "adoptable": list(ADOPTABLE_KINDS)}


def cmd_back(a, root, cfg):
    with store(root) as st:
        st["away"] = None
        return {"away": None, "auto": [x for x in st["away_log"] if x["reviewed"] is None]}


def cmd_away_review(a, root, cfg):
    """자리 비운 동안 정한 것을 사용자가 확인한다: --keep 그대로 둠, --redo 결정을 같은 선택지로 다시 연다."""
    with store(root) as st:
        entries = [x for x in st["away_log"] if x["key"] == a.key]
        if not entries:
            raise Refused(f"자리 비움 기록에 없다: {a.key}")
        entry = next((x for x in reversed(entries) if x["reviewed"] is None), None)
        if entry is None:
            raise Refused(f"{a.key}는 이미 확인했다({entries[-1]['reviewed']})")
        if a.keep:
            entry["reviewed"] = "keep"
            return entry
        if entry["kind"] != "decision":
            raise Refused("미리보기는 다시 정하기를 지원하지 않는다. 바꾸려면 manager에게 수정을 요청한다")
        old = st["decisions"][a.key]
        n = 1
        while f"{a.key}-r{n}" in st["decisions"]:
            n += 1
        new_id = f"{a.key}-r{n}"
        live = [x for x in old["tasks"] if st["tasks"].get(x, {}).get("state") not in ("COMPLETED", "CANCELLED")]
        st["decisions"][new_id] = {
            **{k: old[k] for k in ("title", "question", "kind", "options", "recommend", "evidence")},
            "hold": old.get("hold", "start"), "ask_user": old.get("ask_user", False),
            "id": new_id, "tasks": live, "why": f"자리 비운 동안 {old['choice']}로 정했다. 사용자가 다시 정한다",
            "state": "OPEN", "created": now(), "choice": None, "note": None, "by": None, "answered_at": None,
            "redo_of": a.key}
        entry["reviewed"] = f"redo:{new_id}"
        return {"reopened": new_id, "tasks": live,
                "done_tasks": [x for x in old["tasks"] if x not in live]}  # 이미 끝난 작업은 manager가 후속 작업으로 고친다


def cmd_pause(a, root, cfg):
    with store(root) as st:
        st["paused"] = {"at": now(), "reason": a.reason}
        return {"paused": st["paused"]}


def cmd_resume(a, root, cfg):
    with store(root) as st:
        st["paused"] = None
        return {"paused": None}


def cmd_priority(a, root, cfg):
    with store(root) as st:
        t = get(st, a.id)
        t["priority"] = a.n
        return brief(t)


def cmd_note(a, root, cfg):
    if a.kind not in NOTE_KINDS:
        raise Refused(f"모르는 kind: {a.kind}. {NOTE_KINDS} 중 하나다")
    with store(root) as st:
        if a.task:
            get(st, a.task)
        st["notes_log"] = (st["notes_log"] + [{"at": now(), "kind": a.kind, "text": a.text, "task": a.task or None}])[
            -NOTES_KEPT:]
        return st["notes_log"][-1]


def cmd_limit(a, root, cfg):
    if a.pool not in cfg["slots"] or a.n < 1:
        raise Refused(f"pool은 {sorted(cfg['slots'])} 중 하나, N은 1 이상이다")
    with store(root) as st:
        st["limits"][a.pool] = a.n
        return {"limits": st["limits"]}


def cmd_notes_dir(a, root, cfg):
    """asterbraid 변경(e2e5 5-12): 부른 체크아웃 안의 `.asterbraid-tmp/ID`. worktree 격리 에이전트도 쓸 수 있고,
    git이 무시하게(info/exclude) 해 두어 커밋·제출·정리를 막지 않는다."""
    with store(root, write=False) as st:
        get(st, a.id)
    here = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    exclude = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if TMP_DIR + "/" not in text.splitlines():
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write(("" if text.endswith("\n") or not text else "\n") + TMP_DIR + "/\n")
    d = here / TMP_DIR / a.id
    d.mkdir(parents=True, exist_ok=True)
    return {"notes_dir": str(d), "note": "실험·검사용 임시 파일 자리다(커밋되지 않는다). 남길 결과는 docs/asterbraid/에 쓴다"}


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
        (("--needs-research",), {"action": "store_true"}), (("--preview",), {"action": "store_true"}),
        (("--needs-design",), {"nargs": "?", "const": "standard", "default": None, "choices": DESIGN_DEPTHS}))
    cmd("load", cmd_load, (("file",), {}))
    cmd("next", cmd_next, (("--pool",), {"required": True, "choices": ["research", "design", "review", "preview", "approval",
                                                                       "implement", "verify", "merge"]}))
    cmd("start", cmd_start, ID, (("--pool",), {"required": True, "choices": list(SLOT_STATE) + ["verify"]}),
        (("--agent",), {"required": True}), (("--replace",), {"default": ""}))
    cmd("done", cmd_done, ID, (("--notes",), {"default": ""}), (("--close",), {"action": "store_true"}))
    cmd("design-review", cmd_design_review, ID, (("--verifier",), {"default": ""}),
        (("--verdict",), {"default": "", "choices": ["", "PASS", "FAIL"]}), (("--notes",), {"default": ""}),
        (("--skip",), {"action": "store_true"}), (("--decision",), {"default": ""}))
    cmd("review-mode", cmd_review_mode, (("mode",), {"choices": REVIEW_MODES}))
    cmd("preview", cmd_preview, ID, (("--file",), {"required": True}), (("--summary",), {"default": ""}))
    BY = (("--by",), {"default": "user"})  # 누가 기록했나: user(manager가 사용자 답을 옮김) / cli / tui
    cmd("approve", cmd_approve, ID, (("--version",), {"type": int, "default": 0}), (("--sha",), {"default": ""}), BY)
    cmd("revise", cmd_revise, ID, (("--feedback",), {"required": True}), (("--version",), {"type": int, "default": 0}),
        BY)
    cmd("dashboard", cmd_dashboard)
    cmd("snapshot", cmd_snapshot)
    cmd("decide", cmd_decide, (("action",), {"choices": ["add", "answer", "adopt", "cancel"]}),
        (("id",), {"nargs": "?", "default": ""}), (("--id",), {"dest": "id_opt", "default": ""}),
        (("--title",), {"default": ""}), (("--question",), {"default": ""}), (("--kind",), {"default": "other"}),
        (("--tasks",), {"default": ""}), (("--option",), {"action": "append"}), (("--pros",), {"action": "append"}),
        (("--cons",), {"action": "append"}), (("--recommend",), {"default": ""}), (("--why",), {"default": ""}),
        (("--evidence",), {"default": ""}), (("--choice",), {"default": ""}), (("--note",), {"default": ""}), REASON, BY,
        (("--hold",), {"default": "start", "choices": HOLDS}), (("--ask-user",), {"action": "store_true"}))
    cmd("pause", cmd_pause, REASON)
    cmd("resume", cmd_resume)
    cmd("away", cmd_away, (("--previews",), {"action": "store_true"}), REASON)
    cmd("back", cmd_back)
    cmd("away-review", cmd_away_review, (("key",), {}), (("--keep",), {"action": "store_true"}),
        (("--redo",), {"action": "store_true"}))
    cmd("priority", cmd_priority, ID, (("n",), {"type": int}))
    cmd("note", cmd_note, (("--kind",), {"required": True}), (("--text",), {"required": True}),
        (("--task",), {"default": ""}))
    cmd("submit", cmd_submit, ID)
    cmd("evidence", cmd_evidence, ID, (("--verifier",), {"required": True}),
        (("--verdict",), {"required": True, "choices": ["PASS", "FAIL"]}), (("--sha",), {"required": True}),
        (("--scope",), {"default": ""}), (("--notes",), {"default": ""}))
    cmd("fail", cmd_fail, ID, (("--reason",), {"required": True}))
    cmd("block", cmd_block, ID, (("--reason",), {"required": True}))
    cmd("unblock", cmd_unblock, ID, REASON, (("--reset-retries",), {"action": "store_true"}))
    cmd("cancel", cmd_cancel, ID, REASON, (("--force",), {"action": "store_true"}))
    cmd("deps", cmd_deps, ID, (("--set",), {"required": True}), REASON)
    cmd("sync", cmd_sync, ID)
    cmd("status", cmd_status, (("--json",), {"action": "store_true"}), (("--brief",), {"action": "store_true"}))
    cmd("recover", cmd_recover, (("--reset-running",), {"action": "store_true"}))
    cmd("cleanup", cmd_cleanup, (("ids",), {"nargs": "*"}), (("--dry-run",), {"action": "store_true"}))
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
