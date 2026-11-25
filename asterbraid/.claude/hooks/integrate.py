#!/usr/bin/env python3
"""검증된 작업을 통합 브랜치에 반영한다 (표준 라이브러리만 사용, Python 3.8+).

실행: python .claude/hooks/integrate.py <작업 ID>        (어느 worktree에서든)

브랜치 이름이 아니라 검증이 끝난 커밋 SHA를 병합한다. 병합과 전체 테스트는 임시 worktree에서 하고,
통과한 결과만 통합 브랜치에 fast-forward로 반영한다. 그래서 충돌·테스트 실패·예외·강제 종료가 나도
통합 브랜치에는 검증되지 않은 병합이 남지 않는다.

병합 조건(하나라도 어긋나면 REJECTED)
  - 작업이 READY_TO_MERGE이고, 작업 브랜치의 현재 커밋이 제출·검증된 커밋과 같다
  - 그 커밋에 대한 유효한 독립 verifier PASS 증거가 필요한 수만큼 있다(검증 기준이 바뀌면 무효)
  - 작업 worktree의 verify.py 보고서가 그 커밋의 전체 테스트 PASS다

결과와 종료 코드
  MERGED 0 / REJECTED 2 / CONFLICT 2(재작업으로 전환) / FAIL 2(통합 테스트 실패, 재작업으로 전환)
  BUSY 2(다른 통합이 진행 중) / BLOCKED 2(환경·대상 문제, 상태는 통합 전으로 되돌린다)
기록: <git common dir>/claude-auto/integration-log.jsonl
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # Hook 폴더에 __pycache__를 남기지 않는다
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sched as S  # noqa: E402
import verify  # noqa: E402

PUBLISH_ATTEMPTS = 3


def fault(point: str) -> None:
    """테스트용: 지정한 지점에서 프로세스를 즉시 죽여 강제 종료를 흉내 낸다."""
    if os.environ.get("CLAUDE_AUTO_FAULT") == point:
        os._exit(9)


def out(root: Path, status: str, task_id: str, message: str, **extra) -> int:
    entry = {"time": S.now(), "status": status, "task": task_id, "message": message, **extra}
    try:
        with (S.state_dir(root) / "integration-log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    try:  # asterbraid 추가: 방금 쓴 통합 기록까지 스냅샷에 반영한다. 실패해도 결과는 그대로다
        with S.store(root, write=False) as st:
            S.write_snapshot(root, st)
    except Exception:  # noqa: BLE001
        pass
    print(f"[integrate] {status}: {task_id} - {message}")
    return 0 if status == "MERGED" else 2


def test_report(worktree: Path) -> dict:
    try:
        return json.loads(verify.git_path(worktree, verify.REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {}


def dirty(root: Path) -> bool:
    return bool(S.git(root, "status", "--porcelain", "--untracked-files=no"))


def prepare(root: Path, cfg: dict, task_id: str):
    """조건을 확인하고 MERGING으로 바꾼다. (status, message) 또는 (None, 정보)를 돌려준다."""
    with S.store(root) as st:
        t = S.get(st, task_id)
        integ = st["integration_branch"]
        if not integ or S.git(root, "rev-parse", "--abbrev-ref", "HEAD") != integ:
            return "BLOCKED", f"메인 체크아웃이 통합 브랜치 '{integ}'에 있지 않다"
        if integ in cfg["protected_branches"]:
            return "BLOCKED", f"통합 브랜치 '{integ}'는 보호 브랜치다"
        if dirty(root):
            return "BLOCKED", "메인 체크아웃에 커밋 안 된 변경이 있다"
        merged = bool(t["head"]) and S.is_ancestor(root, t["head"], integ)
        if t["state"] == "COMPLETED":
            return "MERGED", "이미 통합된 작업이다(변경 없음)"
        if t["state"] == "MERGING":  # 통합 잠금은 우리가 쥐고 있다: 이전 실행이 중단된 흔적이다
            S.transition(t, "COMPLETED" if merged else "READY_TO_MERGE", "중단된 통합 정리")
            if merged:
                return "MERGED", "이전 실행에서 이미 반영됐다"
        if t["state"] != "READY_TO_MERGE":
            return "REJECTED", f"{t['state']} 상태다. READY_TO_MERGE인 작업만 통합한다"
        held = S.hold_reason(st, t)  # asterbraid 추가: 사용자 결정이 걸린 작업은 통합하지 않는다
        if held:
            return "REJECTED", f"{held}. 결정이 기록된 뒤 통합한다"
        sha = t["head"]
        if S.branch_head(root, t["branch"]) != sha:
            S.fail(cfg, t, "검증 뒤 작업 브랜치가 바뀌었다. 다시 submit·검증해야 한다", count=False)
            return "REJECTED", f"작업 브랜치가 검증된 커밋 {sha[:10]}과 다르다. 재작업으로 돌렸다"
        have, need = len(S.valid_evidence(t)), S.required_verifiers(cfg, t)
        if have < need:
            S.transition(t, "VERIFYING", "유효한 독립 검증 증거가 부족하다")
            return "REJECTED", f"유효한 verifier PASS {have}/{need}. 검증 기준이나 커밋이 바뀌면 이전 증거는 무효다"
        report = test_report(Path(t["worktree"])) if t["worktree"] and Path(t["worktree"]).is_dir() else {}
        passed = report.get("status") == "PASS" or (report.get("status") == "SKIP" and report.get("cached"))
        if not passed or report.get("scope") != "full" or report.get("commit") != sha:
            return "REJECTED", ("작업 worktree의 테스트 보고서가 없거나 손상됐거나, 이 커밋의 전체 테스트 PASS가 아니다 "
                                f"(status={report.get('status')}, commit={str(report.get('commit'))[:10]}). "
                                "`verify.py --target <worktree> --force`로 다시 만든다")
        if merged:
            S.transition(t, "MERGING", "이미 포함된 커밋")
            S.transition(t, "COMPLETED", "통합 브랜치에 이미 들어 있다", merged_sha=S.git(root, "rev-parse", integ))
            return "MERGED", "커밋이 이미 통합 브랜치에 들어 있다"
        base = S.git(root, "rev-parse", integ)
        S.transition(t, "MERGING", "통합 시작", integration_base=base)
        return None, {"sha": sha, "base": base, "integ": integ, "title": t["title"]}


def settle(root: Path, cfg: dict, task_id: str, state: str, reason: str, **fields) -> None:
    with S.store(root) as st:
        t = S.get(st, task_id)
        if state == "RETRY":
            S.fail(cfg, t, reason, **fields)
        else:
            S.transition(t, state, reason, **fields)


def attempt(root: Path, cfg: dict, task_id: str):
    status, info = prepare(root, cfg, task_id)
    if status:
        return status, info, {}
    sha, base = info["sha"], info["base"]
    tmp = root / ".claude" / "worktrees" / f"_integrate-{task_id}-{os.getpid()}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        code, text = S.run_git(root, "worktree", "add", "--detach", str(tmp), base)
        if code != 0:
            settle(root, cfg, task_id, "READY_TO_MERGE", "임시 worktree 생성 실패")
            return "BLOCKED", f"임시 통합 worktree를 만들지 못했다: {text}", {}
        code, text = S.run_git(tmp, "merge", "--no-ff", "--no-edit", "-m",
                               f"merge: {task_id} {info['title']} (verified {sha[:10]})", sha)
        if code != 0:
            files = S.run_git(tmp, "diff", "--name-only", "--diff-filter=U")[1].splitlines()
            settle(root, cfg, task_id, "RETRY", f"통합 브랜치와 충돌: {files}. `sched.py sync`로 충돌을 푼다",
                   kind="conflict")
            return "CONFLICT", f"충돌 파일 {files}. 재작업으로 돌렸다", {"files": files}
        fault("after_temp_merge")
        merged = S.git(tmp, "rev-parse", "HEAD")
        test = subprocess.run([sys.executable, str(Path(verify.__file__)), "--target", str(tmp), "--force"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        report = test_report(tmp)
        if test.returncode != 0 or report.get("status") != "PASS" or report.get("commit") != merged:
            reason = (report.get("reason") or test.stderr or "")[-1200:]
            settle(root, cfg, task_id, "RETRY", f"병합 결과의 전체 테스트 실패(status={report.get('status')})",
                   kind="integration_test")
            return "FAIL", f"병합 결과가 전체 테스트를 통과하지 못했다. 통합 브랜치는 그대로다.\n{reason}", {}
        # 반영: 통합 브랜치가 그사이 움직였거나 더러워졌으면 이 결과를 쓰지 않는다
        if S.git(root, "rev-parse", info["integ"]) != base or dirty(root):
            settle(root, cfg, task_id, "READY_TO_MERGE", "테스트 중 통합 브랜치가 바뀌었다")
            return "STALE", "테스트하는 동안 통합 브랜치가 바뀌었다", {}
        code, text = S.run_git(root, "merge", "--ff-only", merged)
        if code != 0:
            settle(root, cfg, task_id, "READY_TO_MERGE", "fast-forward 반영 실패")
            return "BLOCKED", f"통합 브랜치에 반영하지 못했다(변경 없음): {text}", {}
        fault("after_publish")
        settle(root, cfg, task_id, "COMPLETED", "통합 완료", merged_sha=merged, integration_base=base)
        return "MERGED", f"'{info['integ']}' {base[:7]} -> {merged[:7]}, 임시 worktree에서 전체 테스트 통과", \
            {"sha": sha, "merged": merged, "base": base, "tests": report.get("command")}
    finally:
        S.remove_worktree(root, tmp)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 2
    task_id = sys.argv[1]
    try:
        root = S.main_root()
        cfg = S.config()
    except S.Refused as e:
        print(f"[integrate] BLOCKED: {e}")
        return 2
    lock = S.FileLock(S.state_dir(root) / "integrate.lock", timeout=0, stale=cfg["integration_lock_stale_s"])
    try:
        S.state_dir(root).mkdir(parents=True, exist_ok=True)
        lock.acquire()
    except S.Busy:
        return out(root, "BUSY", task_id, "다른 통합이 진행 중이다. 끝난 뒤 다시 실행한다")
    try:
        S.prune_temp(root)
        for _ in range(PUBLISH_ATTEMPTS):
            status, message, extra = attempt(root, cfg, task_id)
            if status != "STALE":
                return out(root, status, task_id, message, **extra)
        return out(root, "BLOCKED", task_id, "통합 브랜치가 계속 바뀌어 반영하지 못했다. 다시 실행한다")
    except S.Refused as e:
        return out(root, "REJECTED", task_id, str(e))
    except Exception as e:  # 내부 오류가 통합 성공처럼 보이면 안 된다. 통합 브랜치는 ff 전이라 그대로다
        with_state = ""
        try:
            with S.store(root) as st:
                t = S.get(st, task_id)
                if t["state"] == "MERGING":
                    S.transition(t, "READY_TO_MERGE", f"통합 중 내부 오류: {type(e).__name__}")
                    with_state = " 작업은 READY_TO_MERGE로 되돌렸다."
        except Exception:
            with_state = " 상태 복구는 `sched.py recover`로 한다."
        return out(root, "BLOCKED", task_id, f"integrate.py 내부 오류: {type(e).__name__}: {e}.{with_state}")
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
