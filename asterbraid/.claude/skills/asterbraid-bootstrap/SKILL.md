---
name: asterbraid-bootstrap
description: asterbraid의 시작 준비(처음 한 번). 템플릿과 설계 문서만 있는 새 저장소, 또는 하네스를 이식한 기존 프로젝트에서 manager가 CLAUDE.md·verify.config.json·.gitignore를 채우고(기존 내용은 살린다), integration 브랜치를 만들고, 기준선 테스트를 확인하고, sched init까지 할 때 쓴다.
---

# 시작 준비 (manager, 처음 한 번)
사용자는 템플릿과 설계 문서만 넣고 시작하거나, 진행 중인 프로젝트에 템플릿을 이식한다. 나머지는 문서·코드를 보고 채운다. 아래에서 `sched`는 manager.md와 같다.

0. `git rev-parse --show-toplevel`. 실패하면(git 저장소가 아니면) 현재 폴더에서 `git init -b main`.
   결과가 현재 폴더와 다르면(다른 저장소의 하위 폴더) 그 저장소를 쓸지, 여기서 새로 `git init`할지 묻는다.
   **이미 진행 중인 프로젝트에 이식한 경우**(코드·커밋 이력이 있다): 아래 2~4에서 기존 파일을 덮어쓰지 않는다.
   기존 `CLAUDE.md`·`.claude/settings.json`·`.gitignore`가 있으면 기존 내용을 살리고 하네스에 필요한 항목만 더한 안을 보여 확인받는다.
1. 설계·요구사항 문서를 찾는다(사용자가 준 경로, 없으면 `docs/`·루트의 `*.md`). 없으면 README·코드·대화로 받는다.
   기존 프로젝트면 코드에서 언어·테스트 도구·디렉터리 구조를 읽어 채우고, 도메인 검토 기준이 비면 묻는다.
2. 채울 값을 정한다. 문서에 없는 것만 묻는다.
   - `CLAUDE.md`의 TODO: 프로젝트 설명, 현재 단계, 수정 금지 경로, 테스트 명령, 커밋 규칙, 도메인 검토 기준
     (테스트가 통과해도 틀릴 수 있는 이 프로젝트 고유 항목: 값의 범위·단위, 경계, 보안, 데이터 보존).
     커밋 규칙에 이 세션이 받은 attribution 줄(`Co-Authored-By: …`)을 옮겨 적지 않는다(클라이언트·모델마다 다르다).
   - `.claude/hooks/verify.config.json`: 아래 표. 언어와 테스트 도구는 설계 문서를 따른다.
   - `.gitignore`: `.claude/settings.local.json`, `.claude/worktrees/`, 언어별 산출물(예: `__pycache__/`, `.no-pycache/`, `node_modules/`).
     `docs/asterbraid/`는 무시하지 않는다(커밋한다). `verify.config.json`의 `ignored_prefixes`에 `docs/`(또는 `docs/asterbraid/`)를 남겨 문서만 바뀌면 테스트를 건너뛰게 한다.
3. 바꿀 내용을 한 번에 요약해 `AskUserQuestion`으로 확인받는다(추천을 첫 선택지로). 여기서는 설정만 묻는다.
   설계 결정(저장 방식, 화면 방향 등)은 8 뒤에 `sched decide add`로 연 다음 묻는다(관제 화면과 기록에 남게).
4. 적용한다. `verify.config.json` 수정은 승인 창이 뜬다. 패키지는 설치하지 않는다(필요하면 사용자에게 명령을 알린다).
5. 메인 체크아웃이 보호 브랜치(`main`/`master`)면 **커밋하기 전에** `git switch -c integration`(`main`에는 커밋하지 않는다).
   이미 다른 브랜치면 그것을 쓸지 묻는다. 커밋이 하나도 없는 저장소면 `git switch -c integration`이 빈 브랜치를 만든다.
6. 커밋한다(`chore: 하네스 시작 준비`). 다른 사용자 변경이 섞여 있으면 커밋하지 말고 묻는다.
7. 기존 코드가 있으면 `sched init` 전에 지금 상태로 전체 테스트를 한 번 돌려 기준선을 확인한다
   (`uv run --no-project --offline python .claude/hooks/verify.py --target . --force`). 실패하면 통합이 모두 막히므로,
   실패 목록을 보여 주고 먼저 고칠지(작업으로 등록) 사용자 결정을 받는다. 테스트를 지우거나 건너뛰지 않는다.
8. `sched init`. 그다음 설계 결정이 있으면 한꺼번에 `sched decide add`로 열고 `AskUserQuestion` 한 번으로 묻는다.

| `verify.config.json` 키 | 의미 |
|---|---|
| `test_command` | 전체 테스트 명령(리스트). `{python}`은 고른 인터프리터, `{root}`는 검증 대상 worktree. 종료 코드 0이면 통과 |
| `success_pattern` | 출력에 있어야 하는 정규식. 없으면 FAIL. 도구가 고정 문구를 안 내면 `null` |
| `relevant_suffixes` / `relevant_prefixes` / `relevant_files` | 하나라도 맞는 파일이 바뀌었을 때만 테스트한다. 확장자를 우선 맞춘다 |
| `ignored_prefixes` / `ignored_suffixes` | 먼저 제외할 경로. 테스트가 만드는 산출물은 여기나 `.gitignore`로 뺀다 |
| `python_candidates` | `{python}` 후보(가상환경 경로) |
| `blocked_patterns`, `preflight` | 환경 문제를 FAIL이 아닌 BLOCKED로 본다 / 테스트 전 환경 점검 |
| `timeout_s` | 테스트 제한 시간(300보다 작게) |
| `test_env` | 테스트에 더할 환경 변수. 기본값의 바이트코드 관련 두 개는 지우지 않는다 |
| `base_refs` | 커밋된 변경을 계산할 기준 브랜치 후보 |

예: Python `unittest`(기본값, `tests/__init__.py`가 있어야 테스트를 찾는다. 0개면 FAIL),
pytest `["{python}","-m","pytest","-q"]`+`success_pattern: null`, Node 내장 `["node","--test"]`+`success_pattern: null`+`relevant_suffixes: [".js",".mjs"]`.
