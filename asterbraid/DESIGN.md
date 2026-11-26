# 설계 기록 — AsterBraid

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
| 이름 (2026-11-25) | 사람에게 보이는 이름은 **AsterBraid**로 통일: Mod `asterbraid-dashboard`, 명령 `/asterbraid`, 스킬 `asterbraid-bootstrap`, 문서 `docs/asterbraid/`, 템플릿 폴더 `asterbraid/`(대표 템플릿이라 `방식-dev` 규칙에서 뺐다, 옛 이름 autonomous-mods-dev). 엔진 내부 이름(`.git/claude-auto/`, `auto.config.json`, `[auto-task]`, 스냅샷 `schema`)은 그대로 | harness·autonomous·auto가 섞여 있었다. Loom·Heddle은 같은 분야 오픈소스가 많아 명령이 겹칠 수 있어 피했다(2026-11-25 검색). 내부 이름은 autonomous-parallel-dev와 같은 엔진이고 진행 중인 프로젝트가 깨져 바꾸지 않는다 |
| 템플릿 분리 | `autonomous-parallel-dev`를 복사해 이 템플릿에만 기능을 더한다 | 기존 엔진을 바꾸지 않고, Mods API(early access) 의존을 이 템플릿에 가둔다 |
| Mod 위치 | `.claude/skills/asterbraid-dashboard/` + `enabledPlugins` | 프로젝트에서 자동 로드되는 유일한 위치. `--plugin-dir`·사용자 설정 변경이 필요 없다 |
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
| 시작 준비 | (절차는 `asterbraid-bootstrap` 스킬, 처음 한 번만 불러 manager 프롬프트를 가볍게 둔다) 템플릿 + 설계 문서만 넣으면 manager가 `CLAUDE.md` TODO·`verify.config.json`·`.gitignore`를 채워 확인받고, 커밋·`integration` 생성·`sched init` | 사람이 기억해야 하는 수동 단계를 없앤다. 테스트 명령·검토 기준은 설계 문서에서 나오므로 문서를 읽는 manager가 채우는 것이 정확하다. 테스트 설정 수정은 승인 창으로 사람이 한 번 본다 |
| 설치·업데이트 (2026-11-25) | 사용자가 `asterbraid.py install`·`update`·`check`를 실행한다. 하네스 파일은 복사·해시 기록, settings.json은 JSON 단위로 더하기만, 기존 CLAUDE.md는 끝에 `@.claude/asterbraid.md` 한 줄. 하네스 규칙은 `.claude/asterbraid.md`로 분리 | e2e4: manager가 이식 중 settings.json을 합치려다 권한 분류기에 두 번 막혀 Hook·push 거부 없이 끝까지 돌았다(에이전트가 자기 권한을 고치는 일). 사람이 실행하는 프로그램은 막히지 않고, 해시로 사용자가 고친 파일을 지키며 템플릿 새 버전을 반영할 수 있다 |
| 자리 비움 (2026-11-25) | `sched away [--previews]`/`back`. 자리 비움 중에만 ui·ux·direction·architecture·requirements 결정을 추천안으로 `decide adopt`(by away), `--previews`면 최신 미리보기 `approve --by away`. 복귀하면 `away-review`로 그대로 둠/다시 정하기 | 사용자가 밤새 맡긴다. 시한 대신 미리 위임 범위를 정하면 깨어 있을 때와 규칙이 같고, 의존성·호환 깨짐·환경·데이터는 여전히 사람이 정한다 |
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
| AI 문서 위치 (2026-11-25) | `docs/asterbraid/{requirements,research,design,reports}/`에 날짜·시각 이름으로 쓰고 manager가 바로 커밋 | e2e3에서 계약 문서가 `.git` 안 노트나 메인 체크아웃에만 있어 worktree에서 안 보이고 통합에도 없었다. 대화로 정한 요구도 남지 않았다. 미리보기·상태·증거는 해시·프로그램 검사가 걸려 있어 그대로 둔다 |
| 의존 변경 | `sched deps`(구현 전, 순환 검사), 승인된 미리보기 작업의 `cancel`은 `--force` | e2e3에서 의존을 바꾸려고 취소·재등록하다 미리보기 승인 연결이 끊겼다 |
| 조사만 하는 작업 | `sched done --close`(노트 필수, 브랜치 있으면 거부) | 1차 e2e에서 진단 작업을 닫을 길이 없어 BLOCKED로 쌓였다 |
| 정리 미리보기 | `sched cleanup --dry-run` | 자동 모드 분류기가 대상이 안 보이는 정리를 거부했다(2차 e2e). 목록을 먼저 보이면 사람과 분류기가 판단할 수 있다 |
| 검증자 배정 (2026-11-26) | `start ID --pool verify --agent V`로 제출된 커밋에 필요한 수만큼 배정하고, 배정된 이름의 증거만 센다(`verify_assignment`) | e2e5: 한 verifier가 이름 3개로 증거를 남겨 혼자 "검증 2명"을 채울 수 있었고, 같은 작업에 verifier가 3명 떴다 |
| 통합만 막는 결정 (2026-11-26) | `decide add --hold merge`(시작·검증은 하고 통합만 대기), `--ask-user`(자리 비움 adopt 거부) | e2e5: 데이터 결정 하나 때문에 구현까지 밤새 멈췄다. 임시 데이터로 할 수 있는 일은 하고 반영만 사람을 기다린다. 사용자가 정한다고 한 저장 방식이 architecture로 분류돼 자동 채택됐다 |
| 설계 검토 (2026-11-26) | deep 설계는 `next --pool review` → verifier가 `design-review` PASS해야 구현. `review-mode off|deep|ask`(상태에 저장, Mod에서 전환). 건너뛰기는 답한 결정이 있어야 한다 | 큰 작업은 설계가 틀리면 구현 전체를 다시 한다. 상주 부매니저 대신 검토 단계 하나만 더한다 |
| 노트 폴더 위치 (2026-11-26) | 부른 체크아웃 안 `.asterbraid-tmp/<ID>`(`.git/info/exclude`로 무시) | e2e5: `.git/claude-auto/notes/`는 worktree 밖이라 격리된 implementer의 Write가 거부돼 작업이 멈췄고, manager는 이를 사용자 결정(environment)으로 올렸다 |
| 배정은 메인 체크아웃에서만 | worktree에서 `sched start`를 거부 | e2e5: RETRY 재개 때 implementer가 직접 `start`해 agent 이름이 worktree id로 남았다 |
| 기다리기 (2026-11-26) | manager는 할 일이 없으면 차례를 끝낸다. 모든 `next`에 `attention`(RETRY·열린 결정). Mod 머리말에 멈춘 이유 | e2e5: manager가 한 차례 안에서 96분 동안 `next --pool merge`를 491번 불러, 차례가 끝나야 도착하는 FAIL 보고를 65분 몰랐다(manager 캐시 읽기의 64%). 상주 감시 에이전트는 같은 구조라 도움이 안 되고 토큰만 든다 |
| 가끔 쓰는 절차 | `asterbraid-procedures` 스킬(막힘·중간 인계·끝). manager.md는 원래 크기 안 | 지침은 띄울 때마다 읽혀 토큰이 된다. 핵심 규칙이 빠지지 않는지는 테스트(`TestAgentRules`)가 지킨다 |
| git 시간 제한 | sched의 git 호출 100초, 잠금을 쥔 동안 git마다 잠금 시각 갱신(두 autonomous 템플릿) | 잠금을 쥔 채 git이 멈추면 120초 뒤 다른 프로세스가 잠금을 치우고 함께 쓴다(외부 검수 지적). PID 생존 확인은 Windows에서 `os.kill(pid, 0)`이 프로세스를 끝내 쓰지 않는다 |
| 설치 백업 (2026-11-26) | 바꾸기 전 원본을 `.claude/asterbraid.backup/<시각>/`에, 실패하면 되돌리고 `rollback` 제공(최근 3개) | 중간 실패로 새 지침과 옛 sched.py가 섞이면 이상하게 돈다. 하네스를 모르는 사용자도 되돌릴 수 있게 |

## 공통 코드 정책
| 파일 | 정책 |
|---|---|
| `verify.py` | 모든 템플릿과 같은 파일(테스트가 검사) |
| `sched.py` | `autonomous-parallel-dev`의 상위 집합. 기존 명령·출력은 유지하고 확장만 한다. 기존 autonomous 테스트를 이 템플릿에 다시 돌린다(새 관문인 검증자 배정·설계 검토만 끄고). 2026-11-26 내부를 `sched_store`(저장·잠금·git)·`sched_rules`(상태 규칙)·`sched_view`(지표·스냅샷)로 나눴다. 이름은 `sched`에서 그대로 보이고(integrate.py·테스트 무변경), 함수 본문은 옮기기만 했다 |
| `integrate.py` | 두 곳만 다르다: 결정 대기 거부(`hold merge` 포함), 통합 기록 뒤 스냅샷 갱신(`asterbraid 추가` 주석) |
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
