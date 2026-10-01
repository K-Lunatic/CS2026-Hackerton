# University Agent Skill

설치 즉시 사용할 수 있는 대학생활 관리 Skill입니다. 기본값은 Mock TLS이며 npm, DB, 서버, API Key가 필요 없습니다.

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
```

상세 동작은 [skills/university-agent/SKILL.md](skills/university-agent/SKILL.md)에 있습니다. TLS 담당자는 [provider-contract.md](skills/university-agent/references/provider-contract.md)와 `providers/tls_provider.py`를 기준으로 연동하고, 나머지 팀원은 `features/` 아래에서 기능을 추가합니다.
