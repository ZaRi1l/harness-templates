---
name: project-intro
description: 프로젝트 소개서 작성. 사용자가 프로젝트 소개서·개요 문서를 원할 때 manager가 researcher에게 맡기고, researcher가 프로젝트 지도(MAP)와 실제 코드·문서를 근거로 사람이 읽을 소개 문서를 쓴다.
---

# 프로젝트 소개서

## manager
사용자가 원하면 `sched add --id INTRO --title "프로젝트 소개서" --profile research --risk low --paths docs/asterbraid/reports/ --criteria "소개서 문서"`.
지도가 없으면 MAP 작업 뒤로 `--deps MAP`. researcher에게 이 스킬과 지도 경로, 읽을 사람(예: 새 팀원, 외부 소개)을 넘긴다. 끝나면 `done INTRO --close --notes 경로` 후 커밋한다.

## researcher
1. 프로젝트 지도(`docs/asterbraid/research/…-MAP.md`)를 먼저 읽고, 적힌 진입점·모듈·문서를 실제로 열어 확인한다. 지도와 코드가 다르면 코드를 따른다.
2. `docs/asterbraid/reports/YYYY-MM-DD-HHMM-INTRO.md`를 쓴다. 읽는 사람이 코드를 열지 않아도 알 수 있게, 한 화면에 들어오는 절부터:
   - 한 줄 소개와 누가 무엇에 쓰는가
   - 주요 기능(사용자가 할 수 있는 일)
   - 구조: 큰 부분과 서로의 관계(텍스트 그림 하나), 데이터가 어디에 저장되는가
   - 실행·테스트 방법(실제 명령)
   - 기술 스택(실제 의존 파일에서), 현재 상태와 알려진 한계, 더 읽을 문서
3. 지어내지 않는다. 확인하지 못한 것은 [미확인]으로 쓰거나 뺀다. 내부 비밀·인증 정보·개인 데이터는 넣지 않는다.
4. 문서 경로와 요약을 manager에게 보고한다.
