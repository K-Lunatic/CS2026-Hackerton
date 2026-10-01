# 프로젝트 전체 테스트 결과

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

- Python 테스트 총 37개: 통과 31개, 실제 AI 설정이 필요한 테스트 3개 건너뜀,
  재현한 통합 오류 3개 `expected failure`.
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

### 1. 다른 사용자 동기화로 기존 공지·자료 삭제 (우선 수정)

임시 DB에 student-a의 과목/공지/자료를 넣은 뒤 student-b의 다른 과목을 동기화하면
student-a의 공지와 자료가 삭제된다. `upsert_tls_snapshot`의 정리 SQL이 해당 사용자/과목
범위 없이 모든 `source='tls'` 레코드를 삭제하기 때문이다.

위치: `storage/local_db.py`의 notices/resources DELETE.
재현 테스트: `test_other_users_notices_survive_sync`.
단일 계정 시나리오는 통과했지만 계정 전환/복수 사용자 데이터 공존은 보호되지 않는다.

### 2. 팀플 요청을 체크포인트 불러오기 안내로 잘못 연결

`팀플 진행 상황 알려줘`에 `--records`와 `--prepare`를 제공해도
`prepare_handover` 대신 `prompt_context_command`가 반환된다.
체크포인트 자연어 판별이 팀플 의도보다 먼저 실행되며 `진행 ... 알려`에 매칭되기 때문이다.

위치: `scripts/run_agent.py`의 ask 및 `features/context_commands.py`의 detect_context_intent.
재현 테스트: `test_team_progress_intent_reaches_handover`.
명시적인 과제 저장/불러오기 계약을 유지하면서 팀플 요청을 구별해야 한다.

### 3. 실제 사용자 이름·학과 대신 기본 정보 표시

student-a를 이름으로 등록하고 해당 사용자 컨텍스트를 조회해도
사용자 이름은 홍길동, 학과는 컴퓨터공학과로 반환된다.

위치: `features/context.py`의 get_current_context.user.
재현 테스트: `test_current_context_uses_real_user_identity`.
사용자 ID는 전달된 값이지만 프로필 정보가 하드코딩되어 있다.

## 검증하지 못한 항목

실제 TLS 로그인·실제 학교 HTML 변형·실제 Keychain 저장/복원·실제 파일 다운로드 및
외부 AI 호출은 수행하지 않았다. 그 성공을 모의 테스트 결과로 주장하지 않는다.
팀플 임시 분석은 규칙 기반이며 일반 자연어 해석 정확도를 보장하지 않는다.
이 결과는 나열한 입력과 실행 경로의 검증이며 프로젝트의 모든 가능한 동작을 보장하지 않는다.
