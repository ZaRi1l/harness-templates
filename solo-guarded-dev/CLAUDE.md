# <프로젝트 이름> — 프로젝트 공통 규칙

<!-- TODO: 프로젝트가 무엇을 만드는지 한두 문장. `<...>`와 TODO 주석을 모두 채우거나 지운다. -->

## 현재 단계
- <!-- TODO: 예) 조사 단계다. 사용자가 지시하기 전에는 코드를 수정하지 않는다. / 구현 단계다. 범위: ... -->

## 절대 규칙
- 사용자 승인 없이 git push, 원격 저장소 변경, 브랜치 병합을 하지 않는다.
- 사용자 승인 없이 패키지를 설치하지 않는다.
- `.env`, 키 파일 등 인증 정보를 읽거나 출력하지 않는다.
- 프로젝트 소스·내부 정보를 외부 서비스로 보내지 않는다. 공개 문서 검색만 허용한다.
- 테스트나 검증 기준을 통과시키려고 삭제·완화·건너뛰지 않는다.
- 처리할 수 없는 경우를 임의로 처리해 통과시키지 않는다. 사유와 함께 보고한다.
- 권한 분류기 장애가 나면 우회하지 않는다. 실패한 작업과 미완료 항목을 보고하고 멈춘다.
- 보고할 때 [확인] / [분석] / [미확인] / [제안]을 구분한다.
- <!-- TODO: 읽기 전용·수정 금지 경로가 있으면 적는다. -->

## 실제 차단은 설정과 Hook이 한다
이 문서의 규칙은 지침이다. 실제 차단은 아래가 담당하며, 사용자 승인 없이 수정하지 않는다.
- `.claude/settings.json`
  - 거부: `.env`·키 파일 읽기, `git push`, `gh pr create/merge`
  - 승인 필요: merge, rebase, `reset --hard`, `branch -D`, `git remote`
- `.claude/hooks/verify.py` + `verify.config.json`: 세션이 응답을 마칠 때(Stop) 관련 변경이 있으면 테스트를 돌린다.
  FAIL/BLOCKED면 Claude가 멈추지 않고 이어서 고치거나 보고한다.

## verify.py 판정
| 상태 | 종료 코드 | 의미 |
|---|---|---|
| PASS | 0 | 테스트 통과 |
| SKIP | 0 | 관련 변경이 없거나, 같은 트리가 이미 통과했다 |
| FAIL | 2 | 테스트 실패 또는 시간 초과 |
| BLOCKED | 2 | 환경 미비(의존성 등), 설정 오류. 결과를 믿을 수 없다 |

- 반복 차단 방지로 종료 코드가 1이 되어도 상태는 FAIL/BLOCKED 그대로다. 통과가 아니다.
- 보고서: `.git/claude-verify-report.json` (누적 기록 `.git/claude-verify-history.jsonl`)

## 테스트 실행
- 전체 테스트: <!-- TODO: 예) `.venv/Scripts/python.exe -m unittest`, `npm test` -->
  (`verify.config.json`의 `test_command`와 같아야 한다)
- 수동 검증: `python .claude/hooks/verify.py --target . --force`

## 커밋 규칙
- [Conventional Commits](https://www.conventionalcommits.org/) 형식. <!-- TODO: 프로젝트 규칙 -->

## 참고 문서
<!-- TODO: 설계·조사 문서 위치. 예) `docs/` -->
