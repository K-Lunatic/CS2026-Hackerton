# 프로젝트 전체 테스트 결과

## 모바일 사용 흐름 추가 검증 (2026-10-01)

Chromium의 iPhone 13 (390×844), Pixel 5 (393×851) 장치 에뮬레이션으로
처음부터 끝까지 테스트했다. 모바일 viewport, user agent, 터치 설정을 사용한다.
기존 테스트의 마지막 화면 크기 변경에 더해 모바일 환경에서 전체 흐름을 실행한다.

```bash
HANDOVER_MOBILE_DEVICE='iPhone 13' \
PLAYWRIGHT_MODULE=/private/tmp/handover-browser/node_modules/playwright \
  node skills/university-agent/tests/handover_browser.cjs
# Android: HANDOVER_MOBILE_DEVICE='Pixel 5'로 동일 명령 실행
```

두 환경 모두 통과: 대화 호출, 빈 입력 안내, 분석 진행 표시, 실패 시 입력 보존·재시도,
작업 상태·담당자·기한 수정, 원문 근거 보존, 특정 담당자 초안에 수정 반영,
미정 항목 확인 사항 표시, 편집 초안 재생성 거부와 생성 중 편집 보호,
실제 브라우저 클립보드 내용 일치, 세로 작업 목록과 가로 넘침 없음.
브라우저 JavaScript 오류 없음. iPhone 전체 화면 캡처도 확인했다.

분석은 명시적인 임시 규칙 모드이고, 제안 구분은 모의 응답으로 검증했다.
실제 AI 호출·실물 휴대폰·iOS Safari·모바일 키보드·OS 클립보드 권한은 검증하지 않았다.
이 화면은 localhost 서버이므로 다른 휴대폰에서 데스크톱의 127.0.0.1 주소로 접근할 수 없다.
Codex 플러그인의 TLS·로컬 DB 기능도 웹·모바일 앱 지원을 의미하지 않는다.

검증일: 2026-10-01 (Asia/Seoul). 기능 코드 기준 커밋: `ba48c3a`.
이번 작업은 테스트 추가 및 결과 기록이며, 발견한 다른 기능의 로직은 변경하지 않았다.

## 실행

```bash
python3 -m unittest discover -s skills/university-agent/tests -v
PLAYWRIGHT_MODULE=/tmp/handover-browser/node_modules/playwright \
  node skills/university-agent/tests/handover_browser.cjs
```

브라우저 도구 설치는 [handover.md](handover.md)의 화면 검증 절차를 따른다.
테스트는 임시 SQLite DB를 사용하며 실제 사용자 DB, 저장된 TLS 계정, Keychain을 읽거나
변경하지 않는다. TLS 페이지와 키체인은 명시적인 모의 객체로 검증한다.

## 결과

- 현재 테스트는 빈 DB에 가짜 학사 데이터를 넣지 않는 동작과 DB에서 사용자 이름·학과를 읽는 동작도 검증한다.
  최신 코드 `9f120b2` 기준 40개 중 36개 통과, 실제 AI 테스트 3개 건너뜀, 팀플 의도 오분류 1개는 알려진 실패다.
- `expected failure`는 알려진 실패를 추적하는 표시다. 기능 통과나 수정 완료가 아니다.
  문제가 수정되어 해당 테스트가 성공하면 `unexpected success`로 전체 테스트가 실패하므로
  수정 담당자는 기대 실패 표시를 제거하고 정상 회귀 테스트로 전환해야 한다.
- Python 소스 25개와 두 JavaScript 파일의 문법 검사 통과.
- 실제 Chromium에서 대화 호출, 빈 입력, 진행 표시, 분석 실패/재시도, 원문 보존,
  작업 수정, 담당자별 인수인계, 초안 덮어쓰기 보호, 실제 클립보드 복사,
  한 문단 입력 및 390px 모바일 가로 넘침 검증 통과.

주요 추가 검증: CLI 전체 조회 명령, 과제/강의 필터, 북마크 생성·재조회·삭제,
체크포인트 저장 안내 시 무변경, 저장·재실행 후 불러오기·이력 보존·잘못된 JSON 거부,
정규화 TLS JSON 가져오기·재동기화·정리, DB 무결성/외래 키,
과목·과제·강의·공지·PDF 자료 HTML 파싱, 로그인 전 요청 거부·로그인 실패,
비밀 입력 폼의 비터미널 거부·출력 마스킹, 모의 Keychain 저장 계약,
폼을 열 수 없을 때 TLS CLI가 네트워크 호출 전에 중단되는지 확인.

## 미해결 문제

### 1. 팀플 요청을 체크포인트 불러오기 안내로 잘못 연결

`팀플 진행 상황 알려줘`에 `--records`와 `--prepare`를 제공해도
`prepare_handover` 대신 `prompt_context_command`가 반환된다.
체크포인트 자연어 판별이 팀플 의도보다 먼저 실행되며 `진행 ... 알려`에 매칭되기 때문이다.

위치: `scripts/run_agent.py`의 ask 및 `features/context_commands.py`의 detect_context_intent.
재현 테스트: `test_team_progress_intent_reaches_handover`.
명시적인 과제 저장/불러오기 계약을 유지하면서 팀플 요청을 구별해야 한다.

## 검증하지 못한 항목

최초 전체 테스트 당시 실제 TLS 로그인·실제 학교 HTML 변형·실제 Keychain 저장/복원·실제 파일 다운로드 및
외부 AI 호출은 수행하지 않았다. 이후 실제 TLS 검증은 아래에 별도 기록했다. 그 성공을 모의 테스트 결과로 주장하지 않는다.
팀플 임시 분석은 규칙 기반이며 일반 자연어 해석 정확도를 보장하지 않는다.
이 결과는 나열한 입력과 실행 경로의 검증이며 프로젝트의 모든 가능한 동작을 보장하지 않는다.


## 실제 TLS 데이터의 DB 검증

검증일: 2026-10-01 (Asia/Seoul). 최신 기능 코드: `303181b`.

`database/schema.sql`은 테이블 정의이며 SQLite DB 파일이 아니다. 이 기기에서는
기본 경로 `~/.university-agent/university.db`, workspace 내부 DB, 설치된 스킬 DB와
`UNIVERSITY_AGENT_DB` 별도 설정을 확인했으나 실제 저장된 DB 파일은 발견하지 못했다.

제공받은 계정으로 실제 TLS 로그인 후 현재 수집기로 데이터를 조회하고, Mock 데이터를
넣지 않은 별도 SQLite 검증 DB에 저장했다. 계정/비밀번호/개인 기록 원문은 보고서나 Git에
넣지 않았고, 인증 정보는 저장하지 않았다. 검증 DB는 테스트 완료 후 삭제했다.

| 종류 | 실제 수집 및 DB 조회 건수 |
| --- | ---: |
| 과목 | 7 |
| 과제 | 22 |
| 강의 | 18 |
| 공지 | 3 |
| PDF/PPT 자료 | 0 |

확인한 항목:

- 실제 로그인과 위 데이터 수집 성공.
- 저장 후 사용자별 조회 건수가 수집 건수와 일치하고 출처가 모두 `tls`임을 확인.
- `PRAGMA integrity_check` 통과, `PRAGMA foreign_key_check` 위반 없음.
- 동일 데이터를 재저장해도 중복 없이 조회 건수 유지.
- 실제 데이터의 과목별 할 일 조회 성공 (과목 묶음 7개).
- 이후 최신 코드 `9f120b2`의 전체 자동 테스트: 36개 통과, 알려진 실패 1개, 실제 AI 테스트 3개 건너뜀.
- 기존 사용자 공지/자료 보존 문제 수정에 대한 회귀 테스트 통과.

이 검증은 기존 운영 DB의 존재나 내용 검증이 아니다. 운영용 DB 파일 생성/동기화는
별도로 필요하며 이번 테스트로 영구 DB를 만들지는 않았다. 자료 0건은 현재 수집기의
반환 결과일 뿐 실제 학교에 자료가 전혀 없다는 뜻은 아니다. 따라서 실제 파일 다운로드와
수집 누락 여부, Keychain 저장/복원, 외부 AI 연결은 여전히 미검증이다.

## ChatGPT 로컬 게이트웨이 확인 (2026-10-01)

- `python3 -m unittest server.test_app server.test_local`: 8개 통과. OAuth 코드 재사용 차단, 사용자 격리, 토큰 만료/회전/연결 해제, 날짜 필터, 부분 동기화 보존, 로컬 HTTP, 설정 파일 권한을 확인했다. TLS는 테스트 fixture를 사용한다.
- `python3 -m unittest discover -s skills/university-agent/tests -p 'test_*.py'`: 45개 실행, 통과(3개 skip, 기존 예상 실패 1개). 로컬 포트를 사용하는 검사는 샌드박스 밖 실행에서 확인했다.
- 공식 cloudflared 실행 파일을 로컬에 설치하고 SHA-256을 검증했다. 실행 파일은 커밋하지 않는다.
- Quick Tunnel URL은 발급됐으나 실제 HTTPS `/health` 왕복 확인은 실패했다. 터널 경로의 동작을 검증 완료로 취급하지 않는다.
- Custom GPT ID/Actions 설정과 ChatGPT 웹·모바일의 실제 로그인·조회는 미검증이다. 로컬 Python 실행기를 만든 것만으로 모바일 연결이 완료된 상태는 아니다.
