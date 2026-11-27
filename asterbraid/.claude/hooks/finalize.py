#!/usr/bin/env python3
"""명시적 로컬 마무리(Fast-Forward): 검증·통합된 결과를 사용자가 승인한 로컬 대상 브랜치에만 반영한다 (Python 3.8+).

실행:
  python .claude/hooks/finalize.py --target <대상 브랜치>           (미리보기만)
  python .claude/hooks/finalize.py --target <대상 브랜치> --apply   (실제 로컬 fast-forward 적용)
  python .claude/hooks/finalize.py --target <대상 브랜치> --yes     (--apply와 동일)

안전 규칙:
  - 사용자의 명시적인 로컬 실행 전용 (--target 필수, --apply 또는 --yes 없이는 미리보기만 수행)
  - 자동 루프나 all 모드에서 자동 호출되지 않음
  - 기록된 통합 브랜치와 깨끗한 체크아웃 확인
  - 모든 작업이 COMPLETED 또는 CANCELLED인지 확인 (PENDING/BLOCKED/진행 중 작업이 있으면 거부)
  - COMPLETED 작업의 독립 verifier 증거 및 통합 브랜치 전체 테스트 통과 확인
  - 대상 브랜치가 존재하고 통합 브랜치의 조상인지 확인 (갈라진 브랜치는 거부)
  - 대상 브랜치가 다른 worktree에 체크아웃되어 있지 않은지 확인
  - merge --ff-only로만 반영 (force, reset --hard, remote push 금지)
  - 실패 시 원래 브랜치 및 작업 디렉터리 복구 보장
  - 커밋 레이스 컨디션(SHA 변경) 감지
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sched as S  # noqa: E402
import verify  # noqa: E402


def test_report(worktree: Path) -> dict:
    try:
        return json.loads(verify.git_path(worktree, verify.REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {}


def dirty(root: Path) -> bool:
    code, out = S.run_git(root, "status", "--porcelain")
    return bool(out.strip())


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="마무리할 로컬 대상 브랜치 이름 (예: main)")
    parser.add_argument("--apply", action="store_true", help="로컬 fast-forward 병합을 실제로 실행한다")
    parser.add_argument("--yes", action="store_true", help="--apply와 동일 (사용자 승인 플래그)")
    args = parser.parse_args()

    execute_apply = args.apply or args.yes

    try:
        root = S.main_root()
        cfg = S.config()
    except S.Refused as e:
        print(f"[finalize] BLOCKED: {e}")
        return 2

    lock = S.FileLock(S.state_dir(root) / "integrate.lock", timeout=0, stale=cfg.get("integration_lock_stale_s", 7200))
    try:
        S.state_dir(root).mkdir(parents=True, exist_ok=True)
        lock.acquire()
    except S.Busy:
        print("[finalize] BUSY: 다른 통합 또는 마무리 작업이 진행 중입니다.")
        return 2

    try:
        with S.store(root, write=False) as st:
            integ = st.get("integration_branch")
            if not integ:
                print("[finalize] BLOCKED: 기록된 통합 브랜치가 없습니다.")
                return 2

            cur_branch = S.git(root, "rev-parse", "--abbrev-ref", "HEAD")
            if cur_branch != integ:
                print(f"[finalize] BLOCKED: 메인 체크아웃이 통합 브랜치 '{integ}'에 있지 않습니다 (현재: {cur_branch}).")
                return 2

            if args.target == integ:
                print(f"[finalize] BLOCKED: 대상 브랜치 '{args.target}'는 통합 브랜치와 같을 수 없습니다.")
                return 2

            code, out = S.run_git(root, "rev-parse", "--verify", f"refs/heads/{args.target}")
            if code != 0:
                print(f"[finalize] REJECTED: 대상 브랜치 '{args.target}'이 로컬에 존재하지 않습니다.")
                return 2
            target_sha = out.strip()

            if dirty(root):
                print("[finalize] BLOCKED: 메인 체크아웃에 커밋 안 된 변경 또는 미추적 파일이 있습니다.")
                return 2

            wt_map = S.checked_out(root)
            if args.target in wt_map:
                print(f"[finalize] REJECTED: 대상 브랜치 '{args.target}'이 다른 worktree에 체크아웃되어 있습니다: {wt_map[args.target]}")
                return 2

            tasks = st.get("tasks", {})
            if not tasks:
                print("[finalize] REJECTED: 등록된 작업이 없습니다.")
                return 2

            non_terminal = [t for t in tasks.values() if t.get("state") not in ("COMPLETED", "CANCELLED")]
            if non_terminal:
                reasons = ", ".join(f"{t['id']}({t.get('state')})" for t in non_terminal)
                print(f"[finalize] REJECTED: 완료되지 않은 작업이 있습니다: {reasons}. 모든 작업이 COMPLETED 또는 CANCELLED여야 합니다.")
                return 2

            completed = [t for t in tasks.values() if t.get("state") == "COMPLETED"]
            if not completed:
                print("[finalize] REJECTED: 완료된 작업이 없습니다.")
                return 2

            for t in completed:
                if t.get("head"):
                    have = len(S.valid_evidence(t))
                    need = S.required_verifiers(cfg, t)
                    if have < need:
                        print(f"[finalize] REJECTED: 작업 {t['id']}의 유효한 verifier 증거가 부족합니다 ({have}/{need}).")
                        return 2

            open_decisions = [d["id"] for d in st.get("decisions", {}).values() if d.get("state") == "OPEN"]
            if open_decisions:
                print(f"[finalize] REJECTED: 열린 사용자 결정({open_decisions})이 남아 있습니다.")
                return 2

            integ_sha = S.git(root, "rev-parse", integ)
            if not S.is_ancestor(root, target_sha, integ_sha):
                print(f"[finalize] REJECTED: 대상 브랜치 '{args.target}'({target_sha[:10]})이 통합 브랜치 '{integ}'({integ_sha[:10]})의 조상이 아닙니다 (갈라진 브랜치).")
                return 2

            test = subprocess.run([sys.executable, str(Path(verify.__file__)), "--target", str(root), "--force"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
            report = test_report(root)
            passed = report.get("status") == "PASS" or (report.get("status") == "SKIP" and report.get("cached"))
            if test.returncode != 0 or not passed or report.get("commit") != integ_sha:
                reason = (report.get("reason") or test.stderr or test.stdout or "")[-1200:]
                print(f"[finalize] FAIL: 통합 브랜치가 전체 테스트를 통과하지 못했습니다 (status={report.get('status')}).\n{reason}")
                return 2

            if target_sha == integ_sha:
                if not execute_apply:
                    print(f"[finalize] PREVIEW: 대상 브랜치 '{args.target}'는 이미 통합 브랜치 '{integ}'와 같은 커밋({integ_sha[:10]})입니다 (변경 없음).")
                else:
                    print(f"[finalize] SUCCESS: 대상 브랜치 '{args.target}'는 이미 최신 상태입니다 ({integ_sha[:10]}).")
                return 0

            count = S.git(root, "rev-list", "--count", f"{target_sha}..{integ_sha}")
            summary = S.git(root, "log", "--oneline", f"{target_sha}..{integ_sha}")

            if not execute_apply:
                print(f"[finalize] PREVIEW: 대상 브랜치 '{args.target}'({target_sha[:10]}) -> 통합 브랜치 '{integ}'({integ_sha[:10]})")
                print(f"반영할 커밋 수: {count}개")
                print(f"커밋 요약:\n{summary}")
                print(f"전체 테스트: PASS ({report.get('command')})")
                print("[finalize] PREVIEW: 실제 반영하려면 --apply 또는 --yes 플래그를 붙여 다시 실행하십시오.")
                return 0

            # Apply 모드: SHA 불일치 레이스 컨디션 검사
            if S.git(root, "rev-parse", integ) != integ_sha:
                print(f"[finalize] STALE: 테스트 도중 통합 브랜치 '{integ}'의 커밋이 변경되었습니다.")
                return 2
            if S.git(root, "rev-parse", args.target) != target_sha:
                print(f"[finalize] STALE: 테스트 도중 대상 브랜치 '{args.target}'의 커밋이 변경되었습니다.")
                return 2
            if dirty(root):
                print("[finalize] BLOCKED: 테스트 도중 체크아웃에 변경 사항이 생겼습니다.")
                return 2

            orig_branch = S.git(root, "rev-parse", "--abbrev-ref", "HEAD")
            success = False
            try:
                code, text = S.run_git(root, "switch", args.target)
                if code != 0:
                    print(f"[finalize] BLOCKED: 대상 브랜치 '{args.target}'(으)로 전환하지 못했습니다: {text}")
                    return 2
                code, text = S.run_git(root, "merge", "--ff-only", integ_sha)
                if code != 0:
                    print(f"[finalize] BLOCKED: fast-forward 병합 실패: {text}")
                    return 2
                success = True
            finally:
                try:
                    cur = S.git(root, "rev-parse", "--abbrev-ref", "HEAD")
                    if cur != orig_branch:
                        r_code, r_text = S.run_git(root, "switch", orig_branch)
                        if r_code != 0:
                            print(f"[finalize] WARNING: 원래 브랜치 '{orig_branch}'(으)로 복귀 실패: {r_text}", file=sys.stderr)
                except Exception as e:
                    print(f"[finalize] WARNING: 브랜치 복귀 확인 중 오류: {e}", file=sys.stderr)

            if success:
                entry = {
                    "time": S.now(),
                    "status": "FINALIZED",
                    "target": args.target,
                    "from": target_sha,
                    "to": integ_sha,
                    "commits": int(count),
                    "tests": report.get("command"),
                }
                try:
                    with (S.state_dir(root) / "finalization-log.jsonl").open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    pass
                try:
                    with S.store(root, write=False) as st_snap:
                        S.write_snapshot(root, st_snap)
                except Exception:
                    pass
                print(f"[finalize] SUCCESS: 대상 브랜치 '{args.target}' ({target_sha[:7]} -> {integ_sha[:7]}) fast-forward 완료 ({count}개 커밋)")
                return 0

            return 2
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
