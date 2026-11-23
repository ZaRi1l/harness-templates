#!/usr/bin/env python3
"""검증된 작업 브랜치를 통합 브랜치에 병합한다 (표준 라이브러리만 사용, Python 3.8+).

실행: python .claude/hooks/integrate.py <작업 브랜치>      (메인 체크아웃에서)

통합 브랜치 = 메인 체크아웃이 지금 체크아웃한 브랜치(예: integration). 새 worktree가 이 브랜치의
HEAD에서 갈라지므로, 병합할수록 다음 작업은 최신 통합 결과 위에서 시작한다.

순서
  1. 통합 브랜치가 보호 브랜치(PROTECTED)가 아니고, 메인 체크아웃에 커밋 안 된 변경이 없어야 한다.
  2. 작업 브랜치의 worktree 검증 보고서가 그 브랜치 HEAD의 전체 테스트 PASS여야 한다.
  3. git merge --no-ff. 충돌이면 merge --abort로 되돌리고 CONFLICT.
  4. 병합 결과에서 verify.py --force로 전체 테스트. 실패하면 병합 직전 커밋으로 되돌리고 FAIL.

결과와 종료 코드
  MERGED 0 / REJECTED 2(검증 안 됨) / CONFLICT 2 / FAIL 2(병합 후 테스트 실패, 되돌림) / BLOCKED 2(환경·대상 오류)
기록: <git common dir>/claude-integration-log.jsonl
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import verify  # noqa: E402  (git 경로 처리 재사용)

PROTECTED = ("main", "master")  # 여기에는 절대 자동 병합하지 않는다. 필요하면 프로젝트에 맞게 늘린다


def git(root: Path, *args: str, check: bool = True) -> str:
    return verify.git(root, *args, check=check).strip()


def result(root: Path, status: str, branch: str, message: str, **extra) -> int:
    code = 0 if status == "MERGED" else 2
    entry = {"time": verify.now_iso(), "status": status, "branch": branch, "message": message, **extra}
    try:
        log = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "claude-integration-log.jsonl"
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (RuntimeError, OSError):
        pass
    print(f"[integrate] {status}: {branch} - {message}")
    return code


def branch_worktree(root: Path, branch: str) -> Path | None:
    current = None
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{branch}":
            return current
    return None


def verified(worktree: Path, head: str) -> str | None:
    """작업 브랜치 보고서가 이 HEAD의 전체 테스트 통과인지. 문제가 있으면 사유를 돌려준다."""
    try:
        report = json.loads(verify.git_path(worktree, verify.REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return "검증 보고서가 없다"
    passed = report.get("status") == "PASS" or (report.get("status") == "SKIP" and report.get("cached"))
    if not passed or report.get("scope") != "full":
        return f"보고서가 전체 테스트 통과가 아니다(status={report.get('status')}, scope={report.get('scope')})"
    if report.get("commit") != head:
        return f"보고서 커밋 {str(report.get('commit'))[:10]}이 브랜치 HEAD {head[:10]}와 다르다(검증 후 커밋이 더 있다)"
    return None


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 2
    branch = sys.argv[1]
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    except RuntimeError as e:
        print(f"[integrate] BLOCKED: git 저장소가 아니다: {e}")
        return 2
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if not verify.same_path(Path(git(root, "rev-parse", "--absolute-git-dir")), common):
        return result(root, "BLOCKED", branch, "메인 체크아웃에서 실행해야 한다(지금은 linked worktree)")
    target = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if target in PROTECTED or target == "HEAD":
        return result(root, "BLOCKED", branch, f"통합 브랜치가 '{target}'이다. 보호 브랜치에는 병합하지 않는다. "
                                               "메인 체크아웃을 통합 브랜치(예: integration)로 바꾼다")
    if branch == target or branch in PROTECTED:
        return result(root, "BLOCKED", branch, f"'{branch}'는 병합 대상 작업 브랜치가 아니다")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        return result(root, "BLOCKED", branch, "메인 체크아웃에 커밋 안 된 변경이 있다")
    head = git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", check=False)
    if not head:
        return result(root, "BLOCKED", branch, "그런 브랜치가 없다")
    worktree = branch_worktree(root, branch)
    if worktree is None:
        return result(root, "REJECTED", branch, "브랜치의 worktree를 찾을 수 없어 검증 보고서를 확인할 수 없다")
    problem = verified(worktree, head)
    if problem:
        return result(root, "REJECTED", branch, problem + ". implementer 검증부터 통과시킨다", head=head)

    before = git(root, "rev-parse", "HEAD")
    merge = subprocess.run([verify.GIT, "-C", str(root), "merge", "--no-ff", "--no-edit",
                            "-m", f"merge: {branch} (verified)", branch],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if merge.returncode != 0:
        files = git(root, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()
        git(root, "merge", "--abort", check=False)
        if git(root, "rev-parse", "HEAD") != before:
            return result(root, "BLOCKED", branch, "merge --abort 뒤 HEAD가 바뀌었다. 사람이 확인해야 한다")
        return result(root, "CONFLICT", branch, f"충돌 파일 {files}. implementer가 자기 worktree에서 "
                                                f"'{target}'을 병합해 충돌을 풀고 다시 검증한다", files=files, head=head)

    merged = git(root, "rev-parse", "HEAD")
    test = subprocess.run([sys.executable, str(Path(verify.__file__)), "--target", str(root), "--force"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    report = json.loads(verify.git_path(root, verify.REPORT_NAME).read_text(encoding="utf-8"))
    if test.returncode != 0 or report.get("status") != "PASS" or report.get("commit") != merged:
        # 방금 만든 병합 커밋만 되돌린다(첫 부모가 병합 전 HEAD인지 확인)
        if git(root, "rev-parse", "HEAD^1") == before:
            git(root, "reset", "--hard", before)
        reason = (report.get("reason") or "")[:1500]
        return result(root, "FAIL", branch, f"병합 결과가 전체 테스트를 통과하지 못해 되돌렸다"
                                            f"(status={report.get('status')}). 다른 작업과의 의미 충돌일 수 있다.\n{reason}",
                      head=head)
    return result(root, "MERGED", branch, f"'{target}' {before[:7]} -> {merged[:7]}, 전체 테스트 통과",
                  head=head, merged=merged)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except Exception as e:  # 내부 오류가 병합 성공처럼 보이면 안 된다
        print(f"[integrate] BLOCKED: integrate.py 내부 오류: {type(e).__name__}: {e}")
        sys.exit(2)
