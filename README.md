# 터틀넥

대학생활 관리 기능을 두 방식으로 제공합니다.

- **ChatGPT 웹·모바일:** 이 Mac에서 실행하는 로컬 게이트웨이에 HTTPS 터널로 연결합니다. 별도 호스팅 서버·도메인은 필요 없습니다. Mac에서 [University Agent.command](University%20Agent.command)를 더블클릭하고 Custom GPT Actions를 설정합니다. 휴대폰 사용자는 GPT 로그인 화면을 사용하며 Python을 실행하지 않습니다. [로컬 연결 안내](server/README.md)를 참고하세요. 실제 GPT 연결은 GPT ID와 Actions 설정이 필요합니다. 강의 파일 학습 기능은 로컬 Codex 전용입니다.
- **로컬 Codex:** 기존 플러그인과 현재 기기의 SQLite·macOS Keychain을 사용합니다. 아래 설치 안내는 이 모드에만 해당합니다.

## 처음 사용한다면

기능 이름이나 명령어를 외울 필요 없이 대화에서 원하는 일을 말하세요.

- “뭘 할 수 있어?” — 사용할 수 있는 일을 예시로 안내합니다.
- “오늘 뭐 해야 해?” — 기한 지난 과제, 다음 마감과 급한 강의를 확인합니다.
- “이번 주 안 낸 과제 알려줘” — 이번 주 마감되는 미제출 과제를 확인합니다.
- “할 일 추가해줘” — 제목을 물어보고, 과목·마감일은 아는 경우만 받습니다.
- “팀플 진행 상황 정리해줘” — 회의 내용이나 작업 메모를 받아 정리합니다.
- “자료구조 3주차 자료로 퀴즈 만들어줘” — 동기화한 강의 파일을 읽고 페이지·슬라이드 출처가 있는 학습 자료를 만듭니다.

대화의 앞뒤 문맥은 Codex가 해석하고 필요한 기능을 선택합니다. 로컬 실행기는 정해진
표현을 처리하는 보조 도구이며, 혼자서 모든 자연어를 이해하는 모델은 아닙니다.
기능 안내는 로그인 없이 볼 수 있고 학교 데이터 조회에는 아래 최초 연결이 필요합니다.

## 로컬 플러그인 설치·테스트

이 저장소를 받은 Mac에서 다음을 실행합니다. 설치 후 새 Codex 대화를 시작하고 `$turtleneck`으로 호출합니다.

```bash
codex plugin marketplace add .
codex plugin add university-agent@kku-university-agent-local
python3 skills/university-agent/scripts/sync_tls.py
```

TLS 로그인은 첫 동기화 때 로컬 터미널에서 진행합니다. 자격 증명이 Keychain에 이미 있으면 재사용합니다. 이 플러그인은 **Codex가 해당 Mac의 로컬 파일·Keychain에 접근할 수 있는 환경**에서만 동작합니다. ChatGPT 웹·모바일에서는 별도의 서버/Actions 연결을 사용하며 로컬 DB와 Keychain에 직접 접근하지 않습니다. 로컬 플러그인은 공개 Plugins Directory에 게시된 상태가 아닙니다.

팀원은 `dev` 브랜치를 직접 등록할 수도 있습니다.

```bash
codex plugin marketplace add K-Lunatic/CS2026-Hackerton --ref dev
codex plugin add university-agent@kku-university-agent-local
```

코드 변경 후 마켓플레이스를 갱신하고 다시 설치합니다. 설치된 코드는 사본이므로 저장소 수정이 즉시 반영되지 않습니다.
TLS 계정, 실제 DB, API 키는 플러그인에 포함되지 않습니다.
팀플 화면은 스킬이 로컬 Python 서버를 실행해 연결합니다.

## 스킬만 설치

이 저장소의 `skills/university-agent` 폴더를 Codex의 Skills 디렉터리에 복사합니다.

```bash
cp -R skills/university-agent ~/.codex/skills/
```

이후 `$turtleneck`을 호출하거나 과제·강의·북마크·팀플 진행·인수인계를 자연어로 요청할 수 있습니다. 강의 자료 학습 요청은 TLS 동기화로 내려받은 과목 PDF/PPT를 로컬에서 읽어 요약·핵심 개념·암기 카드·퀴즈를 만들며, 자료와 페이지/슬라이드 출처를 함께 표시합니다. 원본은 기기에 남지만, 생성에 필요한 추출 텍스트는 호출한 ChatGPT/Codex 모델에 전달됩니다. 과제 Context Bookmark의 저장과 불러오기는 정해진 명령 형식을 다시 입력해야 실행됩니다.

TLS 강의실 항목이나 해당 파일을 지목한 공지에서 다운로드 금지를 감지하면 동기화 단계에서 파일을 요청하지 않고, 제외된 과목·자료명을 안내합니다. 금지 자료는 학습 자료 생성에서도 열지 않습니다.

문서 페이지의 실제 링크와 리다이렉트 주소도 검사합니다. 다운로드 금지 플래그가 있는 주소는 차단하며, 자료구조에서 확인된 `ubfile/viewer.php` 뷰어 전용 자료는 원본 다운로드 허용이 확인되지 않아 제외합니다. 뷰어 전용 주소만으로 명시적 금지라고 단정하지 않으며, 제외 이유를 안내합니다. `forcedownload=0`은 화면 표시 옵션이므로 금지로 판단하지 않습니다.

## 직접 실행

```bash
cd ~/.codex/skills/university-agent
python3 scripts/run_agent.py ask --text "아직 안 낸 과제 있어?"
python3 scripts/run_agent.py context
python3 scripts/run_agent.py assignments --this-week
python3 scripts/run_agent.py assignments --upcoming
python3 scripts/run_agent.py assignments --overdue
python3 scripts/run_agent.py study-materials --course "자료구조"
python3 scripts/run_agent.py study-materials --course "자료구조" --resource "3주차"
python3 scripts/run_agent.py ask --text "나.. 지금은 어때?"
python3 scripts/run_agent.py handover --text "로그인 UI 구현했고 refresh token은 아직이야. API는 /api/auth/login."
python3 scripts/run_agent.py ask --text "지금까지 진행 상황 저장해줘"
python3 scripts/run_agent.py ask --text '과제 불러오기 자바 Ex05'
python3 scripts/sync_tls.py
python3 scripts/run_agent.py todos
python3 scripts/tls_fetch.py --path /my/ --output /tmp/tls-my.html
```

자연어로 저장이나 복귀를 요청하면 명령 형식만 안내하며 체크포인트 DB를 읽거나 쓰지 않습니다. 이 규칙은 첫 사용부터 적용하며, `지금 이 대화를 북마크로 저장해줘`도 저장 명령 안내 대상입니다. 대화 내용을 일반 북마크나 파일로 대신 저장하지 않습니다. 실행기는 `CUSTOM` 북마크 생성을 거부하며, 기존 북마크 조회·삭제와 학업 대상 북마크 생성은 유지합니다. 저장할 때 사용자는 `과제 저장 자바 Ex05`처럼 과목명이나 과제 키워드를 입력하면 됩니다. 대화에 나온 이름으로 후보를 찾아 안내하고, 여러 개가 일치하면 과제명·마감일과 함께 선택할 명령을 보여줍니다. 실제 과제 ID는 사용자에게 표시하거나 입력하도록 요구하지 않습니다. ChatGPT/Codex Skill이 현재 대화에서 한국어 진행·막힘·다음 행동을 정리해 내부 JSON으로 전달하며, 이 Skill은 외부 AI에 체크포인트 생성을 요청하지 않습니다. 키워드는 현재 사용자의 등록된 과제에만 연결하며, 일치하는 과제가 없거나 여러 개이면 저장하지 않습니다. 대화에서 확인할 수 없는 내용은 지어내지 않고, 필요한 경우 사용자에게 물어봅니다.

불러오기는 `과제 불러오기`이며, 특정 기록은 `과제 불러오기 자바 Ex05`처럼 키워드를 덧붙입니다. `과제 저장 --과목 "과목명" --과제 "과제명"`처럼 구체적으로 선택할 수도 있습니다. 제목까지 같으면 안내된 마감일을 함께 지정하세요. 직접 CLI 실행은 대화 기록을 볼 수 없으므로 자동 요약하지 않습니다. 자동 저장 요약은 ChatGPT 또는 Codex에서 Skill을 통해 사용하세요.

DB 기본 경로는 `~/.university-agent/university.db`입니다. `UNIVERSITY_AGENT_DB` 환경 변수로 현재 기기의 다른 로컬 경로를 지정할 수 있습니다.

학업 조회는 저장된 데이터 기준입니다. 최신 제출·시청 상태는 TLS 동기화 후 반영됩니다.
“이번 주 미제출 과제”는 한국 시간 월~일 마감만, “앞으로 해야 하는 과제”는 기한이 남은
미제출 과제만 반환합니다. 마감 미상은 별도로 안내하며, 날짜만 있는 마감에 시간을 덧붙이지 않습니다.
“나.. 지금은 어때?”는 마지막 동기화 시각, 기한 지난 과제, 다음 마감, 급한 미완료 강의를 요약합니다.
시청률은 0~100 단위이므로 `1.0`은 1%이며, 완료 여부는 별도 필드를 따릅니다.

TLS 동기화는 수강 과목별 과제(마감·제출 상태), 영상(길이·시청 진행률·완료 여부·표시된 시청 기간), 공지(제목·본문·게시일), PDF/PPT 자료를 저장합니다. `todos`는 미제출 과제와 미완료 영상을 과목별로 묶어 보여줍니다. TLS에 기간이 표시되지 않은 영상의 시청 기한은 비워 둡니다.

## 팀플 진행 정리·인수인계

회의록·작업 기록 텍스트를 사용합니다. 현재 Skill을 호출한 AI가 `--prepare`로 분석 요청을 받고 `--analysis-json`으로 결과를 검증하거나, 설정된 API를 통해 분석할 수 있습니다.

```bash
python3 scripts/run_agent.py ask --text "팀플 진행 상황 정리해줘." --records "민수는 로그인 구현 중. 현우는 DB 생성 완료." --prepare
```

호출 방법과 결과 구조는 [handover.md](skills/university-agent/references/handover.md)를 참고하세요. 분석 요청과 테스트용 응답은 실제 분석 결과로 취급하지 않습니다.

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
저장된 TLS 계정이 없으면 `sync_tls.py`가 로컬 터미널에서 아이디와 숨김 비밀번호 입력 양식을 띄웁니다. 로그인 성공 후 아이디는 `~/.university-agent/tls-account.json`(권한 600), 비밀번호는 macOS Keychain에 저장합니다. 스킬 명령은 비밀번호를 환경변수·SQLite·로그·JSON 출력으로 받거나 반환하지 않습니다. 다만 로컬 명령 실행 권한이 있는 AI 실행 환경을 Keychain 비밀값과 완전히 격리하는 장치는 아직 없으므로, 모델이 기술적으로 읽을 수 없다고 보장하지 않습니다. 이 프로젝트의 모델 범위는 ChatGPT/Codex로 고정하며 새 모델 제공자 연동은 추가하지 않습니다.

향후 입력 폼은 [form-pattern.md](skills/university-agent/references/form-pattern.md)의 공통 계약을 사용합니다. 로컬 모드는 숨김 터미널 입력을, ChatGPT 서버 모드는 OAuth 브라우저 로그인 화면을 사용합니다. 서버의 로그인 경로는 Action 스키마에 포함하지 않습니다.

상세 동작은 [skills/university-agent/SKILL.md](skills/university-agent/SKILL.md)에 있습니다. TLS 담당자는 [provider-contract.md](skills/university-agent/references/provider-contract.md)와 `providers/moodle_provider.py`를 기준으로 연동하고, 나머지 팀원은 `features/` 아래에서 기능을 추가합니다. 로컬 DB는 [data-model.md](skills/university-agent/references/data-model.md)와 `database/schema.sql`에 있습니다.

프로젝트 전체 테스트 실행 방법과 통과·미해결·미검증 항목은 [project-tests.md](skills/university-agent/references/project-tests.md)에 정리되어 있습니다.
