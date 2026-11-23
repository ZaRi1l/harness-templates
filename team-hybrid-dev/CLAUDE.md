# <프로젝트 이름> — 프로젝트 공통 규칙

<!-- TODO: 프로젝트가 무엇을 만드는지 한두 문장. `<...>`와 TODO 주석을 모두 채우거나 지운다. -->

## 현재 단계
- <!-- TODO: 예) 설계 단계다. 사용자가 지시하기 전에는 구현을 시작하지 않는다. -->

## 절대 규칙
- 사용자 승인 없이 git push, 원격 저장소 변경, 브랜치 병합을 하지 않는다.
- 사용자 승인 없이 패키지를 설치하지 않는다.
- `.env`, 키 파일 등 인증 정보를 읽거나 출력하지 않는다.
- 프로젝트 소스·내부 정보를 외부 서비스로 보내지 않는다. 공개 문서 검색만 허용한다.
- 테스트나 검증 기준을 통과시키려고 삭제·완화·건너뛰지 않는다.
- 처리할 수 없는 경우를 임의로 처리해 통과시키지 않는다. 사유와 함께 보고한다.
- 권한 분류기 장애가 나면 우회하지 않는다. 실패한 작업과 미완료 항목을 보고하고 멈춘다.
- 보고할 때 [확인] / [분석] / [미확인] / [제안]을 구분한다.
- <!-- TODO: 읽기 전용 경로가 있으면 적는다. 예) `../other_repo`는 읽기 전용이다. -->
- <!-- TODO: 수정 금지 경로가 있으면 적는다. 예) `vendor/`는 수정하지 않는다. -->

## 실제 차단은 설정과 Hook이 한다
이 문서와 에이전트 문서의 규칙은 지침이다. 실제 차단은 아래가 담당하며, 사용자 승인 없이 수정하지 않는다.
- `.claude/settings.json`
  - 거부: `.env`·키 파일 읽기, `git push`, `gh pr create/merge`
  - 승인 필요: merge, rebase, `branch -D`, `worktree remove`, `git remote`
  - Agent Teams 켬(조사·검증용), 작업 목록 도구 켬, worktree는 현재 HEAD에서 분기(`worktree.baseRef: head`)
- `.claude/hooks/verify.py` + `verify.config.json`: 메인 세션 종료(Stop)와 implementer 종료(SubagentStop) 때 자동 검증

## 멀티 에이전트 구조
| 에이전트 | 모델 | 역할 |
|---|---|---|
| `manager` | opus | 작업 분할·배정·판정. 직접 구현하지 않는다. `claude --agent manager`로 실행 |
| `researcher` | sonnet | **팀원.** 관점별 조사, 서로 반박, 결과를 `docs/`에 정리 |
| `implementer` | inherit | **worktree 서브에이전트.** 구현·테스트·커밋. 동시에 최대 3명 |
| `verifier` | sonnet | **팀원.** implementer의 worktree를 읽기 전용으로 검증, 관점별로 서로 반박 |

작업 흐름:
0. (필요하면) researcher 팀원들이 조사해 `docs/`에 정리한다.
1. manager가 담당 경로가 겹치지 않게 작업을 나눠 implementer에게 배정한다(반드시 `isolation: "worktree"`).
2. implementer가 `.claude/worktrees/` 아래 자기 worktree에서 구현·테스트·커밋한다.
3. implementer가 끝나면 SubagentStop Hook이 `verify.py`로 그 worktree를 자동 검증한다. FAIL이면 implementer가 계속 고친다.
4. manager가 검증 보고서와 Git 상태를 직접 확인하고 상태별로 처리한다.
5. verifier가 독립 검증한다. PASS인 브랜치만 "병합 후보"가 되고, 병합은 사용자 승인 후에 한다.

## verify.py 판정
| 상태 | 종료 코드 | 의미 |
|---|---|---|
| PASS | 0 | 테스트 통과. `scope: full`(전체 테스트 명령)일 때만 병합 근거가 된다 |
| SKIP | 0 | 관련 변경이 없거나, 같은 트리가 이미 전체 통과했다 |
| FAIL | 2 | 테스트 실패 또는 시간 초과 |
| BLOCKED | 2 | 환경 미비(의존성 등), 설정 오류, 대상 오류. 결과를 믿을 수 없다 |

- 반복 차단 방지로 종료 코드가 1이 되어도 상태는 FAIL/BLOCKED 그대로다. 통과가 아니다.
- 보고서 위치: `git -C <worktree> rev-parse --path-format=absolute --git-path claude-verify-report.json`
  (누적 기록은 같은 폴더의 `claude-verify-history.jsonl`)

## 테스트 실행
- 전체 테스트: <!-- TODO: 예) `.venv/Scripts/python.exe -m unittest`, `npm test`, `./gradlew test` -->
  (`verify.config.json`의 `test_command`와 같아야 한다)
- 수동 검증: `python .claude/hooks/verify.py --target <worktree> --force`

## 커밋 규칙
- [Conventional Commits](https://www.conventionalcommits.org/) 형식. <!-- TODO: 메시지 꼬리표 등 프로젝트 규칙 -->

## 도메인 검토 기준 (verifier가 사용)
<!-- TODO: 테스트가 통과해도 틀릴 수 있는, 이 프로젝트 고유의 의미 검토 항목을 적는다.
예) SQL 변환: NULL과 빈 문자열, 날짜 포맷, 정렬 결정성, 암묵적 형변환
예) 금융 계산: 반올림 규칙, 통화 단위, 시간대
예) API: 하위 호환, 오류 코드, 페이지네이션 경계 -->

## 참고 문서
<!-- TODO: 설계 문서 위치. 예) `docs/architecture.md` -->
