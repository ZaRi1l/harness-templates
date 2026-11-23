# harness-templates

Claude Code 하네스 템플릿 모음. 모든 템플릿이 같은 두 가지를 기본으로 갖는다.

- **안전장치**: `git push`·PR 생성 거부, merge·rebase·브랜치 삭제는 승인 필요, `.env`·키 파일 읽기 거부
- **검증 게이트**: `verify.py` Hook이 관련 파일이 바뀌었을 때 테스트를 돌려 PASS / SKIP / FAIL / BLOCKED로 판정한다.
  FAIL·BLOCKED면 Claude가 멈추지 않고 고치거나 보고한다. 테스트 명령은 `verify.config.json`에서 정한다.

## 템플릿
| 템플릿 | 구조 | 규모 | 비용 | 안전성 | 맞는 일 |
|---|---|---|---|---|---|
| [`solo-guarded-dev`](solo-guarded-dev/) | 1인 세션 + Stop Hook | 1인 | 낮음 | 기본 | 대부분의 프로젝트, UI 비중이 큰 일 |
| [`verified-parallel-dev`](verified-parallel-dev/) | manager + worktree implementer(최대 3) + verifier | 병렬 | 중간 | 높음 | 경로가 나뉜 큰 작업, 변환기·마이그레이션 |
| [`team-shared-dev`](team-shared-dev/) | Agent Teams, 작업 폴더 공유, 작업 Hook | 팀 3~5 | 높음 | 중간 | 파일 경계가 뚜렷한 새 기능 |
| [`team-hybrid-dev`](team-hybrid-dev/) | 조사·검증은 팀원, 구현은 worktree | 팀 + 병렬 | 가장 높음 | 가장 높음 | 조사가 먼저 필요하고 틀리면 안 되는 큰 작업 |

고르는 법: 먼저 `solo-guarded-dev`로 시작하고, 작업을 독립 경로로 나눌 수 있을 때 `verified-parallel-dev`로 올린다.
팀 템플릿 두 개는 Claude Code의 **실험 기능**(Agent Teams)에 의존한다.

## 공통 적용 방법
1. 고른 템플릿의 `CLAUDE.md`와 `.claude/`를 프로젝트 루트(git 저장소)에 복사한다. 기존 파일이 있으면 합친다.
2. `CLAUDE.md`의 TODO를 채우고 `.claude/hooks/verify.config.json`을 프로젝트 테스트 명령에 맞춘다.
3. `.gitignore`에 `.claude/settings.local.json`(worktree를 쓰면 `.claude/worktrees/`도)을 넣는다.
4. 한 번 수동 실행: `python .claude/hooks/verify.py --target . --force`

필요한 것: git, Hook 실행용 `uv`(없으면 Hook의 `command`를 `python`으로 바꾼다). `verify.py`는 표준 라이브러리만 쓴다(Python 3.8+).
`verify.config.json`의 키 설명과 pytest·Node·Gradle 예시는 [`verified-parallel-dev/README.md`](verified-parallel-dev/README.md)에 있다.

네 템플릿의 `verify.py`는 같은 파일이다. 고치면 모두 같이 고친다.
