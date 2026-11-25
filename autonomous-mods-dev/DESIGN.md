# 설계 기록

`autonomous-parallel-dev`의 결정(JSON 상태 + 잠금, `.git/claude-auto/`, 상태 변경은 `sched.py`로만, 통합은 프로그램,
임시 worktree 통합, 커밋에 묶인 증거)은 그대로 둔다. 아래는 이 템플릿에서 더한 것이다.

## 조사 (2026-11-24)
| 대상 | 버전 | 확인한 것 |
|---|---|---|
| Claude Code | 2.1.331 | Mods API 타입(`claude-code.d.ts`), `claude plugin validate`·`test`, plugin-authoring 스킬 |
| 공식 문서 plugins/loading | 2026-11-24 열람 | 프로젝트 `.claude/skills/<name>/` 플러그인은 `<name>@skills-dir`로 자동 로드, 신뢰 승인·저장소 루트 필요, `.claude/plugins/`는 읽지 않음 |
| scasella/claude-flightdeck | `626cc0b` (MIT) | 매니페스트 `userConfig`, `register.tsx`/`core.ts` 분리, `agent.spawn`·`turn.complete` 추적, 레이아웃 선택, `.catch` 대체 그리기, CI(validate·test·tsc). **코드는 복사하지 않고 구조만 참고했다** |
| 디자인 스킬 | claude-plugins-official `5cec671`, ui-ux-pro-max `f2f7266`, visual-explainer `9d5f880`, impeccable `b49177d` | 스킬 이름이 설치 방법에 따라 다르다(`plugin:skill` / `skill`). 라이선스 MIT·Apache-2.0. 저장소에 포함하지 않는다 |

## 결정
| 주제 | 결정 | 이유 |
|---|---|---|
| 템플릿 분리 | `autonomous-parallel-dev`를 복사해 이 템플릿에만 기능을 더한다 | 기존 엔진을 바꾸지 않고, Mods API(early access) 의존을 이 템플릿에 가둔다 |
| Mod 위치 | `.claude/skills/autonomous-dashboard/` + `enabledPlugins` | 프로젝트에서 자동 로드되는 유일한 위치. `--plugin-dir`·사용자 설정 변경이 필요 없다 |
| 상태 원천 | `sched.py`의 `state.json` 하나. Mod는 별도 DB·스케줄러를 두지 않는다 | 규칙을 두 곳에서 구현하면 어긋난다 |
| Mod가 읽는 것 | 상태를 쓸 때 같은 잠금 안에서 쓰는 `dashboard.json`(schema_version 1) | Mod가 잠금을 잡지 않고, 매 틱 subprocess를 띄우지 않는다. 파일 시각이 바뀔 때만 읽는다 |
| 스냅샷 실패 | 상태 기록은 그대로 성공, `dashboard.error.json`을 남긴다. Mod는 stale로 표시 | 화면 문제로 개발이 멈추면 안 된다 |
| Mod가 쓰는 것 | 사용자가 누르거나 입력한 승인·수정 요청·결정·슬롯·일시 중지·우선순위만, `sched.py` argv로 | 같은 검사를 거친다. 셸이 없고 입력은 인수 하나로만 들어간다 |
| 에이전트 ↔ 작업 | `start`가 돌려준 marker를 지시문 첫 줄에 넣고 Mod는 그것만 믿는다 | 역할 이름·설명으로 추측하면 같은 역할 여러 개·재작업을 구별하지 못한다 |
| 슬롯 vs 실행 | 스케줄러 점유와 관측한 실행을 따로 세고, 차이를 "실행 미확인"으로 보인다 | 배정됐다고 실행 중인 것은 아니다(세션 끊김, 다른 세션) |
| 사용자 결정 | `decide add/answer/cancel`. 열린 결정에 묶인 작업만 `start`·`integrate` 거부. 응답은 한 번만 | BLOCKED(장애)와 결정 대기를 구분한다. 독립 작업은 계속 돈다 |
| 미리보기 승인 | `--version`·`--sha`로 본 것을 승인, 디스크 파일 해시를 다시 확인, 승인 기록에 완료 기준 해시 | 오래된 화면의 승인이 새 버전에 적용되지 않고, 등록 뒤 바뀐 파일을 승인하지 않는다 |
| 대시보드 | 화면 소스 `.claude/previewUI/preview.html` + 데이터 JSON 주입. 생성물은 `previews/index.html` 그대로 | Python 문자열 HTML을 떼어 고치기 쉽게. `<`를 이스케이프하고 `textContent`로만 그린다 |
| 대시보드 승인 버튼 | 두지 않는다. 로컬 서버도 없다 | 정적 HTML 버튼은 상태를 바꿀 수 없고, 서버는 공격면이다. 승인은 Pane·CLI·manager |
| 미리보기 격리 | `sandbox="allow-scripts"` iframe | 미리보기 코드가 부모 페이지·승인 흐름에 닿지 않는다 |
| designer 스킬 | `preview-workflow`만 미리 불러오고, 디자인 스킬은 있으면 `Skill`로 | 외부 스킬이 없는 환경에서도 동작. 이름이 설치 방법마다 달라 미리 불러오기가 깨진다 |
| 시작 준비 | (절차는 `harness-bootstrap` 스킬, 처음 한 번만 불러 manager 프롬프트를 가볍게 둔다) 템플릿 + 설계 문서만 넣으면 manager가 `CLAUDE.md` TODO·`verify.config.json`·`.gitignore`를 채워 확인받고, 커밋·`integration` 생성·`sched init` | 사람이 기억해야 하는 수동 단계를 없앤다. 테스트 명령·검토 기준은 설계 문서에서 나오므로 문서를 읽는 manager가 채우는 것이 정확하다. 테스트 설정 수정은 승인 창으로 사람이 한 번 본다 |
| 직접 git 권한 | `git merge`·`git worktree remove` 허용, rebase·`reset --hard`·`branch -D`·`remote`는 승인 필요 | 실제 사용에서 승인 요청이 자동 루프를 멈췄다(사용자 경험). 프로그램 경유 병합은 원래 규칙과 무관하고, 직접 병합 금지는 지침으로 남는다 |
| 일시 중지 | 새 `start`만 거부. 실행 중 에이전트·검증·통합은 계속 | 에이전트를 강제로 멈추는 API가 스케줄러에 없고, 반쯤 한 작업을 버리지 않는다 |
| 이상 감지 | 긴 단계는 `sched.py`(과거 중앙값 3배·10분·표본 3), 멈춤·연속 실패·유령 슬롯은 Mod. 경고만 한다 | 상주 LLM 감독관보다 싸고 재현된다. 원인 판단은 manager 하나만 한다 |
| manager에게 경고 전달 | 기본은 화면·토스트. `tellManager`를 켜야 메모로 덧붙인다 | 사용자가 모르는 사이 대화 맥락에 끼어들지 않는다 |
| 모델 호출 | Mod는 하지 않는다 | 비용과 비결정성. 설명은 manager가 `sched note`로 남긴다 |
| 화면 상태 | `$.state`(세션). 리로드에 남고 새 세션은 기본값 | 탭·필터는 개인 화면 상태다. 개발 규칙 설정은 `auto.config.json`에만 둔다 |
| 설계 필요성 (2026-11-25) | 작업마다 `design_depth`(light/standard/deep, `--needs-design`). 프로파일·위험도(검증 수)와 별개. deep 프로파일은 자동 deep | 위험이 낮아도 공통 계약을 여러 작업이 쓰면 설계가 필요하다. e2e 두 번 모두 UI가 CORE 계약에 의존 관계 없이 기댔다 |
| 설계 형식 | `architecture-design` 스킬(architect만 미리 불러옴). deep 점검표는 `references/`로 분리 | 깊이마다 필수 항목을 정해 implementer가 추측하지 않게. 긴 점검표는 deep일 때만 읽어 컨텍스트를 아낀다 |
| 에이전트 모델 (2026-11-25) | manager·implementer opus, architect fable, 나머지 sonnet | implementer는 `inherit`이면 세션을 어떻게 열었는지에 따라 모델이 바뀌어 opus로 고정했다(지금까지의 e2e와 같은 품질). architect는 실행 횟수가 적고 어려운 추론이 몰려, 가격 2배인 fable의 효과가 가장 크다. manager는 짧은 판단을 오래 반복해 fable의 긴 응답·비용이 불리하다 |
| 외부 방법론 스킬 | implementer에게 superpowers TDD·디버깅·완료 검증을 "있으면 사용"으로만 안내. 필수로 미리 불러오지 않는다 | 설치 여부·이름이 환경마다 다르다. 그 스킬들의 자체 worktree·서브에이전트·병합 절차는 스케줄러와 겹치므로 쓰지 않는다 |
| 조사만 하는 작업 | `sched done --close`(노트 필수, 브랜치 있으면 거부) | 1차 e2e에서 진단 작업을 닫을 길이 없어 BLOCKED로 쌓였다 |
| 정리 미리보기 | `sched cleanup --dry-run` | 자동 모드 분류기가 대상이 안 보이는 정리를 거부했다(2차 e2e). 목록을 먼저 보이면 사람과 분류기가 판단할 수 있다 |

## 공통 코드 정책
| 파일 | 정책 |
|---|---|
| `verify.py` | 모든 템플릿과 같은 파일(테스트가 검사) |
| `sched.py` | `autonomous-parallel-dev`의 상위 집합. 기존 명령·출력은 유지하고 확장만 한다. 기존 autonomous 테스트를 이 템플릿에 그대로 다시 돌린다 |
| `integrate.py` | 두 곳만 다르다: 결정 대기 거부, 통합 기록 뒤 스냅샷 갱신(`autonomous-mods-dev 추가` 주석) |
| 엔진 버그 수정 | 두 autonomous 템플릿에 함께 적용하고 각자의 테스트로 확인한다 |

## 무엇이 어떻게 보장되나
| 항목 | 방식 |
|---|---|
| 결정 대기 작업의 시작·통합 거부, 중복 응답·없는 선택지 거부 | 프로그램 + 테스트 |
| 일시 중지 중 새 배정 거부, 실행 중 작업 유지 | 프로그램 + 테스트 |
| 미리보기 버전·해시 불일치, 등록 뒤 바뀐 파일, 오래된 버전 수정 요청 거부 | 프로그램 + 테스트 |
| 스냅샷 갱신, 실패해도 상태 기록 유지, 기다리는 이유, 긴 단계 경고 | 프로그램 + 테스트 |
| 대시보드 데이터의 `</script>` 탈출 방지, 화면 소스 분리 | 프로그램 + 테스트, 실제 브라우저(Edge headless)로 렌더링 확인 |
| Mod: marker 연결, 승인 argv·해시, 읽기 전용, 폴더 밖 미리보기 거부, 그리기만으로 기록 안 함, 손상 스냅샷 표시, 경고 판정 | 엔진 테스트 도구(`claude plugin test`) + 타입 검사 |
| manager가 marker·결정·note를 지침대로 쓰는지, 사용자 답을 정확히 옮기는지 | 지침 |
| Pane 배치, 자동 열기, `/plugin` 활성 표시, 브라우저 열기, `tellManager` 메모 | 미확인(실제 세션 E2E 전) |

## 넣지 않은 것
- 대시보드 HTML의 승인 버튼과 로컬 승인 서버, Mod의 복구·정리·통합 실행, 에이전트 강제 종료.
- 자동 병렬도 조절, 에이전트별 비용(API가 주지 않는다), 모델을 부르는 요약.
- 별도 JSON Schema 파일(계약은 `types/index.d.ts`와 `SNAPSHOT_SCHEMA` 버전 검사).
