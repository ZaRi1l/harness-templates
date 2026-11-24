# verified-parallel-dev

검증 게이트가 붙은 병렬 개발용 Claude Code 하네스 템플릿이다.

- **개발**: manager가 작업을 나누고, implementer가 최대 3명까지 각자 git worktree에서 동시에 구현한다.
- **검증 게이트**: 구현 결과는 두 번 검증한다. 먼저 `verify.py` Hook이 자동으로 테스트하고,
  이어서 verifier 에이전트가 읽기 전용으로 독립 검토한다. 둘 다 통과한 브랜치만 "병합 후보"가 된다.
- **안전장치**: push와 PR 생성은 거부(push는 사람이 한다), 인증 파일은 읽기 거부.
  병합·rebase·브랜치 삭제는 자동 루프를 위해 승인 없이 허용한다.

단순한 1인 대화형 개발에도 쓸 수 있다. 메인 세션이 끝날 때도 Stop Hook이 테스트를 돌린다.
다만 이 구조가 제값을 하는 곳은 "여러 작업을 병렬로 맡기되, 검증 안 된 결과는 받지 않는" 개발이다.

## 구성
```
CLAUDE.md                        프로젝트 규칙 템플릿 (TODO 채우기)
.claude/
  settings.json                  권한(거부/승인), Hook 등록, worktree 설정
  hooks/verify.py                Stop/SubagentStop 검증 Hook (표준 라이브러리만 사용, Python 3.8+)
  hooks/verify.config.json       프로젝트별 테스트 명령·경로 설정
  agents/manager.md              팀장: 분할·배정·판정 (직접 구현 안 함)
  agents/implementer.md          구현자: worktree에서 구현·테스트·커밋
  agents/verifier.md             검증자: 읽기 전용 독립 검증
```

## 적용 방법
1. 이 폴더의 `CLAUDE.md`와 `.claude/`를 대상 프로젝트 루트(git 저장소)에 복사한다.
   대상에 이미 `CLAUDE.md`나 `.claude/settings.json`이 있으면 덮어쓰지 말고 합친다.
2. `.gitignore`에 `.claude/worktrees/`와 `.claude/settings.local.json`을 추가한다.
3. `CLAUDE.md`의 `<...>`와 `TODO`를 채운다. 특히 테스트 명령, 읽기 전용 경로, 도메인 검토 기준.
4. `.claude/hooks/verify.config.json`을 프로젝트에 맞게 고친다 (아래 표).
5. 수동으로 한 번 돌려 본다: `python .claude/hooks/verify.py --target . --force`
   결과는 stderr에 나오고, 보고서는 `.git/claude-verify-report.json`에 남는다.
6. 병렬 개발을 하려면 `claude --agent manager`로 실행한다.

### 필요한 것
- git
- Hook 실행용 `uv`. `settings.json`은 `uv run --no-project --offline python ...`으로 Hook을 실행한다.
  uv가 없으면 두 Hook의 `command`/`args`를 Python 직접 실행으로 바꾼다.
  예: `"command": "python"`, `"args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/verify.py"]`.
  Windows의 `python`이 Microsoft Store 별칭이 아닌지 확인한다.
- 에이전트 모델은 `opus` / `inherit` / `sonnet`이다. 사내 별칭 모델을 쓰면 각 frontmatter의 `model:`을 바꾼다.

## verify.config.json
| 키 | 의미 |
|---|---|
| `test_command` | 전체 테스트 명령(문자열 리스트). `{python}`은 선택된 인터프리터로, `{root}`는 검증 대상 worktree 경로로 바뀐다. 종료 코드 0이면 통과 |
| `test_env` | 테스트 실행 때 추가할 환경 변수 |
| `python_candidates` | `{python}` 후보. 메인 체크아웃 기준 상대 경로이며, 없으면 Hook을 실행한 Python을 쓴다. `VERIFY_PYTHON`이 우선 |
| `relevant_prefixes` / `relevant_suffixes` / `relevant_files` | 이 경로가 바뀌었을 때만 테스트한다. 아니면 SKIP |
| `ignored_prefixes` / `ignored_suffixes` | relevant보다 먼저 제외할 경로. 테스트가 만드는 산출물(`.pyc`, 빌드 결과 등)은 `.gitignore`나 여기서 빼야 한다. 안 그러면 매번 "변경"으로 잡혀 캐시가 듣지 않는다 |
| `base_refs` | 커밋된 변경을 계산할 기준 브랜치. 앞에서부터 처음 존재하는 것을 쓴다 |
| `timeout_s` | 테스트 제한 시간. `settings.json` Hook의 `timeout`(300)보다 작게 둔다 |
| `success_pattern` | 출력에 반드시 있어야 하는 정규식(여러 줄 모드). 종료 코드 0인데 이게 없으면 FAIL. `null`이면 검사 안 함 |
| `blocked_patterns` | 테스트가 실패했고 출력이 이 정규식과 맞으면 FAIL이 아니라 BLOCKED(환경 문제)로 본다 |
| `preflight` | 테스트 전 환경 점검. `[{"name": "...", "command": [...]}]`. 하나라도 실패하면 BLOCKED |

기본값 `{python} -m unittest`는 패키지 안의 테스트만 찾는다. `tests/`에 `__init__.py`가 없으면 테스트를 0개 찾아
종료 코드 5로 FAIL이 된다. `tests/__init__.py`를 두거나 `test_command`를 pytest 등으로 바꾼다.

`blocked_patterns`의 `ModuleNotFoundError`는 의존성 누락을 잡으려는 것이다. 다만 코드 버그로 자기 모듈을
못 찾아도 BLOCKED로 분류된다. 통과로 처리되지는 않지만, manager가 implementer 대신 사용자에게 보고하게 된다.

### 예시
**Python + pytest**
```json
"test_command": ["{python}", "-m", "pytest", "-q"],
"success_pattern": null
```

**worktree에서 내 패키지를 import하는지 확인** (editable 설치는 메인 체크아웃을 가리킬 수 있다)
```json
"preflight": [{
  "name": "mypkg imports from target tree",
  "command": ["{python}", "-c",
    "import os,sys,mypkg; r=os.path.normcase(os.path.realpath(sys.argv[1])); p=os.path.normcase(os.path.realpath(mypkg.__file__)); sys.exit(0 if p.startswith(r) else 'mypkg from '+p)",
    "{root}"]
}]
```

**Node.js**
```json
"test_command": ["npm", "test", "--silent"],
"relevant_prefixes": ["src/", "test/"],
"relevant_suffixes": [".js", ".ts", ".tsx"],
"relevant_files": ["package.json", "package-lock.json", "tsconfig.json"],
"success_pattern": null,
"blocked_patterns": ["Cannot find module '(?!\\.)"],
"preflight": [{"name": "node_modules present", "command": ["node", "-e", "require.resolve('typescript')"]}]
```

**Java + Gradle**
```json
"test_command": ["{root}/gradlew", "test"],
"relevant_prefixes": ["src/"],
"relevant_suffixes": [".java", ".kt"],
"relevant_files": ["build.gradle", "build.gradle.kts", "settings.gradle"],
"success_pattern": "BUILD SUCCESSFUL",
"blocked_patterns": []
```
Windows에서는 `{root}/gradlew.bat`로 쓴다.

**환경 변수 예** (numpy 스레드 폭주로 Windows에서 메모리가 부족할 때)
```json
"test_env": {"OPENBLAS_NUM_THREADS": "1"}
```

## 판정
| 상태 | 종료 코드 | 의미 |
|---|---|---|
| PASS | 0 | 테스트 통과. `scope: full`일 때만 병합 근거 |
| SKIP | 0 | 관련 변경이 없거나, 같은 트리가 이미 전체 통과 |
| FAIL | 2 | 테스트 실패, 시간 초과, success_pattern 없음 |
| BLOCKED | 2 | 환경 미비, 설정 파일 오류, 대상이 git 트리가 아님, Hook 내부 오류 |

같은 실패가 반복되면 무한 루프를 막으려고 종료 코드를 1로 낮춘다. 그래도 상태는 FAIL/BLOCKED다.

수동 실행 옵션은 다음과 같다.
- `--target DIR`: 검증할 트리
- `--force`: 캐시와 "변경 없음" SKIP 무시
- `--only ARG...`: `test_command` 뒤에 인자를 붙여 일부만 실행. 결과는 scoped PASS이며 캐시하지 않는다
