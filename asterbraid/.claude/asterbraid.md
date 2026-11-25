# AsterBraid 하네스 규칙

이 파일은 `asterbraid.py update`가 바꾼다. 프로젝트 규칙은 `CLAUDE.md`에 쓴다.

## 절대 규칙
- git push는 하지 않는다. `main`/`master` 반영은 사용자가 한다.
- 병합은 `.claude/hooks/integrate.py`로만 한다. 작업 상태는 `.claude/hooks/sched.py`로만 바꾼다.
- 사용자 승인 없이 패키지를 설치하지 않는다.
- `.env`, 키 파일 등 인증 정보를 읽거나 출력하지 않는다.
- 프로젝트 소스·내부 정보를 외부 서비스로 보내지 않는다. 공개 문서 검색만 허용한다.
- 테스트나 검증 기준을 통과시키려고 삭제·완화·건너뛰지 않는다. 예외는 사용자가 결정(`sched decide`)으로 고른 환경 의존 표시뿐이다.
- 프로그램이 거부하거나 권한 분류기 장애가 나면 우회하지 않는다. 사유를 보고하고 그 작업만 멈춘다.
  환경 문제는 우회가 아니라 진단한다: 설정을 바꾸지 않는 읽기 전용 조사로 원인을 좁힌 뒤, 사용자만 할 수 있는 일만 사용자에게 묻는다.
- 보고할 때 [확인] / [분석] / [미확인] / [제안]을 구분한다.

## 실행 습관 (모든 에이전트)
- 서버·백그라운드 프로세스는 시간 제한을 걸어 띄우고(예: `timeout 900 …`), 보고 전에 직접 끈다. 앱 기본 포트(예: 8000)를 피해 빈 포트를 쓰고, 남이 띄운 서버는 쓰지 않는다. 보고에 "남긴 프로세스 없음"을 적는다.
- 여러 줄 스크립트는 `python -c`·heredoc 대신 노트 폴더(`sched notes-dir ID`)에 파일로 써서 실행한다.
- `playwright-cli`가 PATH에 없으면 `npx --no-install playwright-cli`·`%APPDATA%\npm\`을 확인한다(설치하지 않는다). 끝나면 자기가 만든 `.playwright-cli/`를 지운다(막히면 둔다. `.gitignore`돼 있다).

## 에이전트
| 에이전트 | 모델 | 역할 |
|---|---|---|
| `manager` | opus | 사용자와 대화하는 개발 책임자. 작업 분할·배정·통합 지시·결정 요청. 직접 구현하지 않는다. `claude --agent manager`로 실행 |
| `researcher` | sonnet | (선택) 공개 자료 조사 → 작업 노트 |
| `architect` | fable | (선택, `--needs-design` 또는 deep) 설계 깊이에 맞춘 인터페이스·데이터·오류 계약과 작업 분할 → 작업 노트. `architecture-design` 스킬을 미리 불러온다 |
| `designer` | sonnet | (선택, `--preview`) 구현 전 미리보기 HTML. `preview-workflow` 스킬을 미리 불러오고, 디자인 스킬은 있으면 골라 쓴다 |
| `implementer` | opus | worktree에서 구현·테스트·커밋·제출 |
| `verifier` | sonnet | 읽기 전용 독립 검증, 판정을 커밋에 묶어 기록 |
| `Explore` (내장) | | 코드 위치·구조 탐색 |

메인 체크아웃은 **통합 브랜치**(예: `integration`)에 둔다.

관제 화면: `/asterbraid` (asterbraid-dashboard Mod). 없어도 개발은 그대로 돈다.

## AI 문서 (`docs/asterbraid/`, 커밋한다)
| 폴더 | 쓰는 쪽 | 내용 |
|---|---|---|
| `requirements/` | manager | 요구 분석: 출처(문서·대화), 적힌 것 → 작업, 추론과 근거, 미정 → 결정. 문서로 받은 요구는 복사하지 않고 가리킨다 |
| `research/` | researcher | 조사·진단 결과 |
| `design/` | architect | 설계·계약(implementer가 따르고 verifier가 검사) |
| `reports/` | manager | 끝 보고: 결과, 결정, 남은 위험, 사용자가 할 일 |
- 파일 이름은 `YYYY-MM-DD-HHMM-<작업 ID 또는 주제>.md`(시각은 `date +%Y-%m-%d-%H%M`). 첫머리에 작성 시각, 작성자, 출처, 관련 작업·결정 ID.
- 서브에이전트는 쓰기만 하고, manager가 바로 integration에 `docs(asterbraid): …`로 커밋한다(implementer는 `sched sync`로 받는다).
- 새 문서가 옛 문서를 대신하면 지우지 말고 옛 문서 첫머리에 `대체됨: <새 파일 이름>` 한 줄을 넣는다. 그 표시가 있는 문서는 읽지 말고 새 문서를 본다.
- 프로젝트 지도(`research/…-MAP.md`)가 있으면 먼저 읽고 필요한 파일만 연다. 지도와 코드가 다르면 코드를 따른다.
- 작업에 관련된 문서는 모든 에이전트가 참고한다. 미리보기 HTML(`.git/claude-auto/previews/`, 해시로 승인)과 상태·결정·검증 증거(`sched`)는 지금 자리 그대로다.

## 프로그램이 강제하는 것 (사용자 승인 없이 수정하지 않는다)
- `sched.py`: 상태 전이, 의존성 순환, 중복 배정, 담당 경로 충돌, 슬롯 한도, 담당 경로 밖 변경 제출, 재시도 한도,
  검증 증거와 커밋의 일치, 미리보기 승인 전 구현 시작, 승인할 미리보기의 버전·해시, 열린 사용자 결정에 묶인 작업의 시작,
  일시 중지 중의 새 배정, 자리 비움 중 위임 범위.
- `integrate.py`: 검증된 커밋만, 임시 worktree에서 병합·전체 테스트 뒤 통과한 것만 통합 브랜치에 반영. 보호 브랜치 거부.
  열린 사용자 결정에 묶인 작업은 통합하지 않는다.
- `verify.py`: Stop / implementer 종료 때 테스트. PASS·SKIP만 통과다.
- `settings.json`: push·PR 생성·`.env` 읽기 거부. 직접 하는 rebase·`reset --hard`·`branch -D`·`remote`와 `.claude/` 수정은 승인 필요.
  `git merge`·`git worktree remove`는 자동 루프가 멈추지 않도록 허용한다. 그래도 통합 브랜치 병합은 `integrate.py`로만 한다(지침).
- 사용자 결정·미리보기 승인은 사용자가 실제로 답한 것만 기록한다. 예외는 사용자가 켠 자리 비움(`sched away`)의 위임 범위뿐이다(`by away`).
