# University Agent Skill

설치 즉시 사용할 수 있는 대학생활 관리 Skill입니다. 별도 서버 없이 사용자 소유 동기화 폴더를 사용하므로 npm, DB 서버, API Key가 필요 없습니다.

## 설치

이 저장소의 `skills/university-agent` 폴더를 Codex의 Skills 디렉터리에 복사합니다.

```bash
cp -R skills/university-agent ~/.codex/skills/
```

이후 `$university-agent`를 호출하거나 자연어로 과제·강의·북마크·인수인계를 요청하면 됩니다.

각 기기에서 같은 개인 폴더를 지정합니다.

```bash
export UNIVERSITY_AGENT_DATA_DIR="/내/동기화폴더/university-agent"
export UNIVERSITY_AGENT_USER_ID="user-hong"
```

## 직접 실행

```bash
cd ~/.codex/skills/university-agent
python3 scripts/run_agent.py ask --text "아직 안 낸 과제 있어?"
python3 scripts/run_agent.py context
python3 scripts/run_agent.py handover --text "로그인 UI 구현했고 refresh token은 아직이야. API는 /api/auth/login."
python3 scripts/sync_tls_snapshot.py --input tls_snapshot.json
```

상세 동작은 [skills/university-agent/SKILL.md](skills/university-agent/SKILL.md)에 있습니다. TLS 담당자는 [provider-contract.md](skills/university-agent/references/provider-contract.md)와 `providers/tls_provider.py`를 기준으로 연동하고, 나머지 팀원은 `features/` 아래에서 기능을 추가합니다. 공통 DB 초안은 [data-model.md](skills/university-agent/references/data-model.md)와 `database/schema.sql`에 있습니다.
