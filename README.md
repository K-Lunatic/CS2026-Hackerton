# University Agent Skill

설치 즉시 사용할 수 있는 대학생활 관리 Skill입니다. 별도 서버·동기화 없이 현재 채팅 기기의 로컬 SQLite DB만 사용합니다. npm이나 DB 서버는 필요 없습니다.

## 설치

이 저장소의 `skills/university-agent` 폴더를 Codex의 Skills 디렉터리에 복사합니다.

```bash
cp -R skills/university-agent ~/.codex/skills/
```

이후 `$university-agent`를 호출하거나 자연어로 과제·강의·북마크·인수인계를 요청하면 됩니다.

## 직접 실행

```bash
cd ~/.codex/skills/university-agent
python3 scripts/run_agent.py ask --text "아직 안 낸 과제 있어?"
python3 scripts/run_agent.py context
python3 scripts/run_agent.py handover --text "로그인 UI 구현했고 refresh token은 아직이야. API는 /api/auth/login."
python3 scripts/ingest_tls.py --input tls_snapshot.json
```

상세 동작은 [skills/university-agent/SKILL.md](skills/university-agent/SKILL.md)에 있습니다. TLS 담당자는 [provider-contract.md](skills/university-agent/references/provider-contract.md)와 `providers/tls_provider.py`를 기준으로 연동하고, 나머지 팀원은 `features/` 아래에서 기능을 추가합니다. 로컬 DB는 [data-model.md](skills/university-agent/references/data-model.md)와 `database/schema.sql`에 있습니다.

## 팀플 진행 정리·인수인계 (Python)

DB 없이 회의록·작업 기록 텍스트로 사용할 수 있습니다. 현재 스킬을 호출한 AI가
`--prepare`로 분석 요청을 받고 `--analysis-json`으로 결과를 검증하는 방식과,
향후 Chat Completions 호환 API를 직접 호출하는 방식을 지원합니다.

```bash
python3 skills/university-agent/scripts/run_agent.py ask --text "팀플 진행 상황 정리해줘." --records "민수는 로그인 구현 중. 현우는 DB 생성 완료." --prepare
```

호출 방법, 결과 구조, 팀원 연결 인터페이스와 실제 API 검증 방법은
[handover.md](skills/university-agent/references/handover.md)를 참고하세요.
분석 요청과 테스트용 응답은 실제 API 분석 결과로 취급하지 않습니다.
