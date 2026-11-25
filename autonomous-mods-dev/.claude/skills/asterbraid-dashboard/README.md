# asterbraid-dashboard

`autonomous-mods-dev`의 관제 Mod(Claude Code 플러그인, hooks 모듈). 사용법·명령·설정·보안·한계는 템플릿 [README](../../../README.md)와 [DESIGN](../../../DESIGN.md)에 있다.

| 파일 | 역할 |
|---|---|
| `hooks/register.tsx` | 이벤트 관찰(결과를 바꾸지 않음), 스냅샷 읽기, `/asterbraid`, Pane 그리기, 사용자 행동만 `sched.py`로 |
| `hooks/core.ts` | 순수 함수: 스냅샷 검사, marker, 명령 해석, sched 인수, 문자 폭, 패널 줄, 경고 판정 |
| `types/index.d.ts` | `$.state` 계약과 스냅샷 타입(sched.py `SNAPSHOT_SCHEMA` 1) |
| `tests/dashboard.test.tsx` | `claude plugin test` |

검사: `claude plugin validate .`, `claude plugin test .`, (한 번 로드된 뒤) `npx -p typescript tsc -p .`. Claude Code 2.1.331 기준.
