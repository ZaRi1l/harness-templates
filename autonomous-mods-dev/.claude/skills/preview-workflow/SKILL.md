---
name: preview-workflow
description: autonomous-mods-dev의 미리보기 제작·등록 절차. designer가 작업 ID의 미리보기 HTML을 만들고 sched.py에 버전으로 등록할 때, verifier가 승인된 미리보기와 구현을 비교할 때 쓴다.
---

# 미리보기 절차

좋은 화면을 만드는 방법은 디자인 스킬이 맡고, 이 절차는 **어디에 무엇을 어떻게 등록하는지**를 맡는다. 둘이 어긋나면 이 절차가 우선이다.
아래에서 `sched`는 `uv run --no-project --offline python .claude/hooks/sched.py`다.

## 1. 확인
- 지시문 첫 줄의 `[auto-task id=… pool=preview attempt=…]`로 작업 ID를 확인한다. 없으면 manager에게 묻는다.
- `sched status --json`에서 그 작업의 `title`, `criteria`, `preview.versions`, `preview.feedback`(이전 수정 요청)을 읽는다.
- 프로젝트의 기존 화면·디자인 규칙(색, 글꼴, 컴포넌트)을 찾는다. 있으면 그것을 따른다.

## 2. 디자인 스킬 고르기 (선택)
세션의 사용 가능한 스킬 목록에 있을 때만 `Skill` 도구로 부른다. 없으면 이 절차만으로 만든다. 설치하지 않는다.

| 작업 | 스킬(설치 방법에 따라 이름이 다르다) |
|---|---|
| 화면·컴포넌트 프로토타입 | `frontend-design` (`frontend-design:frontend-design`) |
| 디자인 시스템·토큰·접근성 규칙 | `ui-ux-pro-max` |
| 구조도·흐름도·ERD·상태 머신·대안 비교 | `visual-explainer` (`visual-explainer:visual-explainer`) |
| 만든 화면의 품질 검토 | `impeccable` |

한 작업에 필요한 것만 부른다. 스킬이 권하는 외부 CDN·폰트·패키지 설치는 아래 규칙 때문에 쓰지 않는다.

## 3. 만들기
- 작업 폴더: `sched notes-dir <작업 ID>`가 준 폴더. 저장소 안에는 쓰지 않는다.
- 한 파일 HTML(CSS·JS 포함). 외부 네트워크 요청·CDN·원격 폰트를 쓰지 않는다. 더블클릭으로 열려야 한다.
- 실제 인증 정보·운영 데이터·개인정보를 넣지 않는다. 그럴듯한 가짜 데이터를 쓴다.
- 로컬 파일 읽기, 시스템 명령, `sched` 호출처럼 승인 흐름에 닿는 코드를 넣지 않는다. 대시보드는 미리보기를 sandbox iframe으로 띄운다.
- 눌러 볼 수 있게 만든다(상태 전환, 오류·빈 화면, 좁은 화면). 대안이 있으면 한 파일 안에 탭이나 나란히 보여 주고, 각 대안의 차이를 화면에 적는다.
- UI가 아니면 구조도·ERD·API 예시 응답·시뮬레이션처럼 눈으로 판단할 수 있게 만든다.
- 맨 아래에 "검토할 점" 목록을 둔다: 사용자가 정해야 할 것, 구현 때 지킬 동작.

## 4. 등록
```
sched preview <작업 ID> --file <노트 폴더>/preview.html --summary "한 줄: 무엇이 바뀌었나"
```
- 프로그램이 `previews/<ID>/vN.html`로 복사하고 해시를 기록한다. 이전 버전과 승인된 버전은 덮어쓰지 않는다. 이미 등록한 파일을 고치지 않는다(해시가 달라져 승인이 거부된다).
- 수정 요청을 받았으면 새 파일로 다시 등록한다. 요청한 점을 어떻게 반영했는지 summary에 쓴다.

## 5. 보고 (manager에게)
등록된 버전, 해시 앞 12자, 사용자가 정해야 할 점, 대안별 장단점과 추천. 승인은 사용자만 한다.

## verifier가 쓸 때
- `sched status --json`의 `preview.approval`(버전·해시)과 `previews/<ID>/v<N>.html`을 연다. 파일 해시가 기록과 같은지 확인한다.
- 구현이 승인된 화면의 구성·문구·상태 전환·오류 처리와 맞는지 본다. 테스트가 통과해도 승인된 동작과 다르면 FAIL이다.
