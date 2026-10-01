# University Agent Skill

설치 즉시 사용할 수 있는 대학생활 관리 Skill입니다. 별도 서버·기기 간 동기화 없이 현재 기기의 SQLite DB를 사용합니다. npm이나 DB 서버는 필요 없습니다.

## 설치

이 저장소의 `skills/university-agent` 폴더를 Codex의 Skills 디렉터리에 복사합니다.

```bash
cp -R skills/university-agent ~/.codex/skills/
```

이후 `$university-agent`를 호출하거나 과제·강의·북마크·팀플 진행·인수인계를 자연어로 요청할 수 있습니다. 과제 Context Bookmark의 저장과 불러오기는 정해진 명령 형식을 다시 입력해야 실행됩니다.

## 직접 실행

```bash
cd ~/.codex/skills/university-agent
python3 scripts/run_agent.py ask --text "아직 안 낸 과제 있어?"
python3 scripts/run_agent.py context
python3 scripts/run_agent.py handover --text "로그인 UI 구현했고 refresh token은 아직이야. API는 /api/auth/login."
python3 scripts/run_agent.py ask --text '과제 저장 --과제ID assignment-network-5 --진행 "자료 3개 수집 완료" --완료항목 "자료 3개 수집" --막힘 "없음" --다음행동 "두 번째 자료의 통계를 본문에 넣기"'
python3 scripts/run_agent.py ask --text '과제 불러오기 --과제ID assignment-network-5'
python3 scripts/ingest_tls.py --input tls_snapshot.json
```

자연어로 진행 상황 저장이나 복귀를 요청하면 명령 형식만 안내하며 DB를 읽거나 쓰지 않습니다. 저장 형식은 `과제 저장 --과제ID <id> --진행 "..." --막힘 "..." --다음행동 "..."`이고, 불러오기는 `과제 불러오기`입니다. 특정 기록은 불러오기 명령 뒤에 `--과제ID <id>`를 추가합니다.

DB 기본 경로는 `~/.university-agent/university.db`입니다. `UNIVERSITY_AGENT_DB` 환경 변수로 현재 기기의 다른 로컬 경로를 지정할 수 있습니다.

## 팀플 진행 정리·인수인계

회의록·작업 기록 텍스트를 사용합니다. 현재 Skill을 호출한 AI가 `--prepare`로 분석 요청을 받고 `--analysis-json`으로 결과를 검증하거나, 설정된 API를 통해 분석할 수 있습니다.

```bash
python3 scripts/run_agent.py ask --text "팀플 진행 상황 정리해줘." --records "민수는 로그인 구현 중. 현우는 DB 생성 완료." --prepare
```

호출 방법과 결과 구조는 [handover.md](skills/university-agent/references/handover.md)를 참고하세요. 분석 요청과 테스트용 응답은 실제 분석 결과로 취급하지 않습니다.

상세 동작은 [skills/university-agent/SKILL.md](skills/university-agent/SKILL.md)에 있습니다. TLS 연동은 [provider-contract.md](skills/university-agent/references/provider-contract.md), 로컬 DB 구조는 [data-model.md](skills/university-agent/references/data-model.md)와 `skills/university-agent/database/schema.sql`을 참고하세요.

## 팀플 화면 실행

```bash
python3 skills/university-agent/scripts/handover_web.py
```

[http://127.0.0.1:8765/](http://127.0.0.1:8765/)에서 자료 입력 → 결과 수정·원문 확인 →
인수인계 생성·편집 → 복사를 진행할 수 있습니다. AI 설정 전에는 **임시 규칙 분석 (AI 아님)**으로
동작하며, API 연결 후 같은 화면에서 실제 AI를 사용합니다. 대화에서 화면 링크를 받으려면
`python3 skills/university-agent/scripts/run_agent.py ask --text "팀플 정리해줘" --ui`를 실행합니다.
화면은 별도 설치 없이 Python으로 실행되며, 저장소의 DB/TLS 코드와 분리되어 있습니다.
실행·프론트엔드 API·검증·제한 사항은 [handover.md](skills/university-agent/references/handover.md)에 있습니다.
