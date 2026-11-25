# <프로젝트 이름> — 프로젝트 공통 규칙

<!-- TODO: 프로젝트 설명 한두 문장. `<...>`와 TODO를 채우거나 지운다. -->

## 현재 단계
- <!-- TODO: 예) 구현 단계다. 범위: ... -->

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
- <!-- TODO: 읽기 전용·수정 금지 경로 -->

## 에이전트
| 에이전트 | 모델 | 역할 |
|---|---|---|
| `manager` | opus | 사용자와 대화하는 개발 책임자. 작업 분할·배정·통합 지시·결정 요청. 직접 구현하지 않는다. `claude --agent manager`로 실행 |
| `researcher` | sonnet | (선택) 공개 자료 조사 → 작업 노트 |
| `architect` | opus | (선택, `--needs-design` 또는 deep) 설계 깊이에 맞춘 인터페이스·데이터·오류 계약과 작업 분할 → 작업 노트. `architecture-design` 스킬을 미리 불러온다 |
| `designer` | sonnet | (선택, `--preview`) 구현 전 미리보기 HTML. `preview-workflow` 스킬을 미리 불러오고, 디자인 스킬은 있으면 골라 쓴다 |
| `implementer` | inherit | worktree에서 구현·테스트·커밋·제출 |
| `verifier` | sonnet | 읽기 전용 독립 검증, 판정을 커밋에 묶어 기록 |
| `Explore` (내장) | | 코드 위치·구조 탐색 |

메인 체크아웃은 **통합 브랜치**(예: `integration`)에 둔다.

관제 화면: `/autonomous` (autonomous-dashboard Mod). 없어도 개발은 그대로 돈다.

## 프로그램이 강제하는 것 (사용자 승인 없이 수정하지 않는다)
- `sched.py`: 상태 전이, 의존성 순환, 중복 배정, 담당 경로 충돌, 슬롯 한도, 담당 경로 밖 변경 제출, 재시도 한도,
  검증 증거와 커밋의 일치, 미리보기 승인 전 구현 시작, 승인할 미리보기의 버전·해시, 열린 사용자 결정에 묶인 작업의 시작,
  일시 중지 중의 새 배정.
- `integrate.py`: 검증된 커밋만, 임시 worktree에서 병합·전체 테스트 뒤 통과한 것만 통합 브랜치에 반영. 보호 브랜치 거부.
  열린 사용자 결정에 묶인 작업은 통합하지 않는다.
- `verify.py`: Stop / implementer 종료 때 테스트. PASS·SKIP만 통과다.
- `settings.json`: push·PR 생성·`.env` 읽기 거부. 직접 하는 rebase·`reset --hard`·`branch -D`·`remote`와 `.claude/` 수정은 승인 필요.
  `git merge`·`git worktree remove`는 자동 루프가 멈추지 않도록 허용한다. 그래도 통합 브랜치 병합은 `integrate.py`로만 한다(지침).
- 사용자 결정·미리보기 승인은 사용자가 실제로 답한 것만 기록한다. 답이 없으면 기록하지 않는다.

## 테스트 실행
- 전체 테스트: <!-- TODO: 예) `.venv/Scripts/python.exe -m unittest` --> (`verify.config.json`의 `test_command`와 같게)

## 커밋 규칙
- [Conventional Commits](https://www.conventionalcommits.org/) 형식. <!-- TODO: 프로젝트 규칙 -->

## 도메인 검토 기준 (verifier가 사용)
<!-- TODO: 테스트가 통과해도 틀릴 수 있는 이 프로젝트 고유의 검토 항목 -->
