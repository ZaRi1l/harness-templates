# team-hybrid-dev

`verified-parallel-dev`에 Agent Teams를 더한 템플릿.
**조사·검증은 팀원**(서로 메시지로 반박)이, **구현은 worktree에 격리된 서브에이전트**가 한다.

## 구성
```
CLAUDE.md                      규칙 (TODO 채우기)
.claude/settings.json          Agent Teams·작업 도구 켜기, 권한, Stop/SubagentStop 검증 Hook
.claude/hooks/verify.py        테스트 실행기 (다른 템플릿과 같은 파일)
.claude/hooks/verify.config.json   테스트 명령·경로
.claude/agents/manager.md      팀장 (claude --agent manager)
.claude/agents/researcher.md   조사 팀원  → docs/에 정리
.claude/agents/implementer.md  구현 서브에이전트 (worktree)
.claude/agents/verifier.md     검증 팀원 (읽기 전용)
```

## 사용법
1. `CLAUDE.md`와 `.claude/`를 프로젝트 루트에 복사하고 TODO와 `verify.config.json`을 채운다.
   `verify.config.json`의 키 설명은 `verified-parallel-dev/README.md`에 있다.
2. `.gitignore`에 `.claude/worktrees/`와 `.claude/settings.local.json`을 추가한다.
3. `claude --agent manager`로 실행하고 할 일을 말한다.

## 동작
조사(researcher 팀) → 작업 분할 → 구현(implementer, worktree) → SubagentStop에서 verify.py 자동 검증
→ verifier 팀 교차 검증 → PASS면 병합 후보 (병합은 사용자 승인)

## 주의
- Agent Teams는 **실험 기능**이다. 대화형 세션에서만 팀이 생기고(`-p` 불가), `/resume`하면 팀원이 복원되지 않는다.
- implementer는 **반드시 `isolation: "worktree"`로** 띄워야 한다. 빠뜨리면 팀원으로 떠서 메인 체크아웃을 고친다.
  이 경우 verify.py 보고서에 "not in a linked worktree" 경고가 남는다.
- 팀원에게 작업 도구가 안 보이면 `claude --settings '{"env":{"CLAUDE_CODE_ENABLE_TODO_TOOLS":"1"}}' --agent manager`로 실행한다.
- 팀원 수만큼 토큰이 든다. 단순한 일에는 `verified-parallel-dev`나 `solo-guarded-dev`를 쓴다.
- Hook 실행에 `uv`가 필요하다. 없으면 `command`를 `python`으로 바꾼다.
