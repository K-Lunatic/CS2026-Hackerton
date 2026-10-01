# 터틀넥

대학생활 관리 기능을 두 방식으로 제공합니다.

- **ChatGPT 웹·모바일:** 컴퓨터에서 실행하는 로컬 게이트웨이에 HTTPS 터널로 연결합니다. Mac에서는 [University Agent.command](University%20Agent.command), Windows에서는 [University Agent.bat](University%20Agent.bat)을 실행하고 Custom GPT Actions를 설정합니다. 휴대폰 사용자는 GPT 로그인 화면을 사용합니다. [로컬 연결 안내](server/README.md)를 참고하세요. 실제 GPT 연결은 GPT ID와 Actions 설정이 필요합니다. 학교 자료 자동 조회·본문 읽기와 코드가 관리하는 문제 풀이 세션은 로컬 Codex에서 사용합니다. ChatGPT 웹·모바일은 대화에 직접 첨부한 파일의 읽을 수 있는 본문으로 학습할 수 있습니다.
- **로컬 Codex:** 플러그인과 현재 기기의 SQLite·운영체제 보안 저장소를 사용합니다. 아래 설치 안내는 이 모드에 해당합니다.

## 처음 사용한다면

기능 이름이나 명령어를 외울 필요 없이 대화에서 원하는 일을 말하세요.

- “뭘 할 수 있어?” — 사용할 수 있는 일을 예시로 안내합니다.
- “오늘 뭐 해야 해?” — 기한 지난 과제, 다음 마감과 급한 강의를 확인합니다.
- “이번 주 안 낸 과제 알려줘” — 이번 주 마감되는 미제출 과제를 확인합니다.
- “할 일 추가해줘” — 제목을 물어보고, 과목·마감일은 아는 경우만 받습니다.
- “자료구조 공부 좀 해야겠다” — 읽은 수업자료로 복습 문제를 만들지 한 번 제안합니다.
- “자료구조 3주차 자료로 퀴즈 만들어줘” — 동기화한 강의 파일을 읽고 페이지·슬라이드 출처가 있는 학습 자료를 만듭니다.
- “캡처한 과제 지금까지 한 것 저장해줘” — 대화 속 단서로 TLS 과제를 찾고, 검색어 보완이나 직접 정한 이름으로 이어서 저장합니다.
- `list` — 저장된 미완성 과제를 모두 봅니다. 이 명령을 정확히 입력해야 목록을 읽습니다.

대화의 앞뒤 문맥은 Codex가 해석하고 필요한 기능을 선택합니다. TLS에서 관련 과제를 찾지 못하면 제목이 다른지 또는 학교 과제 목록에 없는지 먼저 확인한 뒤 저장 경로를 안내합니다. 로컬 실행기는 정해진
표현을 처리하는 보조 도구이며, 혼자서 모든 자연어를 이해하는 모델은 아닙니다.
기능 안내는 로그인 없이 볼 수 있고 학교 데이터 조회에는 아래 최초 연결이 필요합니다.

## 로컬 플러그인 설치·테스트

이 저장소를 받은 Mac 또는 Windows에서 다음을 실행합니다. 설치 후 새 Codex 대화를 시작하고 `$turtleneck`으로 호출합니다.

```bash
codex plugin marketplace add .
codex plugin add university-agent@kku-university-agent-local
# Mac/Linux: python3 skills/university-agent/scripts/sync_tls.py
# Windows:   py -3 skills/university-agent/scripts/sync_tls.py
```

TLS 로그인은 첫 동기화 때 로컬 숨김 입력으로 진행합니다. Mac은 Keychain, Windows는 현재 사용자의 보호 저장소를 사용합니다. 이 플러그인은 **Codex가 학사 DB와 파일이 있는 컴퓨터에서 실행될 때** 동작합니다. ChatGPT 웹·모바일은 별도 Actions 연결을 사용합니다. 로컬 플러그인은 공개 Plugins Directory에 게시된 상태가 아닙니다.

팀원은 `dev` 브랜치를 직접 등록할 수도 있습니다.

```bash
codex plugin marketplace add K-Lunatic/CS2026-Hackerton --ref dev
codex plugin add university-agent@kku-university-agent-local
```

코드 변경 후 마켓플레이스를 갱신하고 다시 설치합니다. 설치된 코드는 사본이므로 저장소 수정이 즉시 반영되지 않습니다.
TLS 계정, 실제 DB, API 키는 플러그인에 포함되지 않습니다.
학습보조는 대화에서 개념을 정리하고 문제를 한 번에 풀며 별도 화면이 필요하지 않습니다.

## 스킬만 설치

이 저장소의 `skills/university-agent` 폴더를 Codex의 Skills 디렉터리에 복사합니다.

```bash
cp -R skills/university-agent ~/.codex/skills/
```

이후 `$turtleneck`을 호출하거나 과제·강의·북마크·수업자료 기반 학습을 자연어로 요청할 수 있습니다. 로컬 학습보조는 내려받은 과목 PDF/PPTX/TXT/MD·DOCX/HWPX 등의 읽은 본문 또는 직접 첨부한 파일에서 핵심 개념과 연습문제를 만들고 출처 위치를 표시합니다. 원본은 기기에 남지만 추출 텍스트는 호출한 Codex 모델에 전달됩니다. 과제 Context Bookmark의 저장과 불러오기는 정해진 명령 형식을 다시 입력해야 실행됩니다.

TLS 강의실 항목이나 해당 파일을 지목한 공지에서 다운로드 금지를 감지하면 동기화 단계에서 파일을 요청하지 않고, 제외된 과목·자료명을 안내합니다. 금지 자료는 학습 자료 생성에서도 열지 않습니다.

문서 페이지의 보이는 내용과 리다이렉트 주소도 검사합니다. 실제 다운로드 금지 표시나 서버 거부가 있을 때 파일을 가져오지 않습니다. `viewer.php` 주소나 `forcedownload=0`만으로 금지로 판단하지 않지만, 응답 본문이 실제 파일이 아닌 HTML 문서 뷰어 페이지이면 금지 자료로 분류합니다.

## 직접 실행

```bash
cd skills/university-agent
python3 scripts/run_agent.py ask --text "아직 안 낸 과제 있어?"
python3 scripts/run_agent.py context
python3 scripts/run_agent.py assignments --this-week
python3 scripts/run_agent.py assignments --upcoming
python3 scripts/run_agent.py assignments --overdue
python3 scripts/run_agent.py study-materials --course "자료구조"
python3 scripts/run_agent.py study-materials --course "자료구조" --resource "3주차"
python3 scripts/run_agent.py ask --text "나.. 지금은 어때?"
python3 scripts/run_agent.py ask --text "자료구조 공부 좀 해야겠다" --conversation demo-study
python3 scripts/run_agent.py study --conversation demo-study --event-json '{"action":"status"}'
python3 scripts/run_agent.py ask --text "지금까지 진행 상황 저장해줘"
python3 scripts/run_agent.py ask --text 'load "자바 Ex05"'
python3 scripts/sync_tls.py
python3 scripts/run_agent.py todos
python3 scripts/tls_fetch.py --path /my/ --output /tmp/tls-my.html
```

저장 요청은 대화 속 단서로 저장된 TLS 과제의 제목·과목·설명을 조회하는 것부터 시작합니다.
호스트 AI가 `ask --text "과제 저장해줘" --assignment-query "구조체 배열"`처럼 원문과 검색어를
함께 전달합니다. 검색은 학사 정보만 읽으며 진행 기록은 아직 저장하지 않습니다.
후보가 없으면 다른 과목명·단어를 받거나 “새 이름: 복소수 구조체 과제”처럼 직접 정한 이름으로
이어갑니다. 한 번의 검색 실패로 TLS에 없는 과제라고 단정하지 않습니다. 짧은 후속 답변의
의미는 현재 대화를 보는 Codex가 해석하며, 실행기 자체에 대화 기억이 있는 것은 아닙니다.

후보에는 과목·제목·마감·제출 상태를 표시합니다. 한 후보라면 반환된 저장 명령이 선택 단계이고,
여러 후보라면 번호나 이름으로 좁힌 뒤 해당 명령을 안내합니다. 제출 완료인 후보에는 저장
명령을 제시하지 않고, 상태 새로고침이나 별도 이름의 복습 과제로 이어갈 수 있게 안내합니다.
실제 TLS 제출 상태를 로컬에서 바꾸지는 않습니다.

최종 저장은 `save "TLS 과제명"` 또는 `save new "직접 정한 이름"`으로 요청합니다.
단일 코드 블록에 담긴 명령도 그대로 사용할 수 있습니다. `save new`는 같은 제목의 직접 등록
과제가 있으면 재사용합니다. ChatGPT/Codex가 대화에서 확인한 진행·막힘·다음 행동을 요약해
전달하므로 사용자는 내부 ID나 요약 필드를 입력하지 않습니다. 일반 북마크나 파일로 대화를
대신 저장하지 않습니다. 후보 선택 중에는 필요한 질문·명령만, 저장 뒤에는 이어서 불러올
명령을 표시합니다.

불러오기는 `load`이며, 특정 기록은 `load "자바 Ex05"`처럼 이름을 덧붙입니다. 후보가 겹치면 안내된 `--course`, `--title`, `--due` 명령으로 구분할 수 있습니다. 직접 CLI 실행은 대화 기록을 볼 수 없으므로 자동 요약하지 않습니다. 자동 저장 요약은 ChatGPT 또는 Codex에서 Skill을 통해 사용하세요.

저장된 미완성 과제 전체는 `list`를 정확히 입력하면 볼 수 있습니다. 비슷한 표현에는 명령만 안내합니다. 명령을 실행할 수 있는 단계에서는 관련 명령만 나오며, `list` 뒤에는 각 과제를 불러오는 명령도 나옵니다. 직접 등록한 과제를 모두 마치면 제출 여부를 묻고 정확히 `예`라고 답했을 때만 저장 기록을 제거합니다. `아니요`나 다른 답변에는 기록을 유지합니다. TLS 과제는 동기화에서 제출 완료(`SUBMITTED` 또는 `LATE`)가 확인되면 제거합니다.

DB 기본 경로는 `~/.university-agent/university.db`입니다. `UNIVERSITY_AGENT_DB` 환경 변수로 현재 기기의 다른 로컬 경로를 지정할 수 있습니다.

학업 조회는 저장된 데이터 기준입니다. 최신 제출·시청 상태는 TLS 동기화 후 반영됩니다.
“이번 주 미제출 과제”는 한국 시간 월~일 마감만, “앞으로 해야 하는 과제”는 기한이 남은
미제출 과제만 반환합니다. 마감 미상은 별도로 안내하며, 날짜만 있는 마감에 시간을 덧붙이지 않습니다.
“나.. 지금은 어때?”는 마지막 동기화 시각, 기한 지난 과제, 다음 마감, 급한 미완료 강의를 요약합니다.
시청률은 0~100 단위이므로 `1.0`은 1%이며, 완료 여부는 별도 필드를 따릅니다.

TLS 동기화는 수강 과목별 과제(마감·제출 상태), 영상(길이·시청 진행률·완료 여부·표시된 시청 기간), 공지(제목·본문·게시일), PDF/PPT 자료를 저장합니다. `todos`는 미제출 과제와 미완료 영상을 과목별로 묶어 보여줍니다. TLS에 기간이 표시되지 않은 영상의 시청 기한은 비워 둡니다.

## 수업자료 기반 학습보조

“자료구조 공부 좀 해야겠다”는 실제 자료의 필수 개념을 먼저 정리한 뒤
“문제 10개 풀기 / 문제 20개 풀기 / 개념 다시 설명해줘”를 제안합니다.
문제 풀이에 동의하면 기본 10문제(4지선다 6, 단답 2, 서술 2)를 한 번에 보여줍니다.
“객관식 20문제 만들어줘”처럼 직접 요청하면 개념 설명을 거치지 않고 준비합니다.
답은 한 번에 제출·채점하며 답변 전에는 정답·해설·원문 인용을 숨깁니다.
문항별 “힌트”, “건너뛰기”, “정답 보기”와 “그만하기”를 사용할 수 있습니다.
문항 수는 요청대로 1~20개, “한 문제씩”을 명시하면 개별 풀이도 지원합니다.
“핵심 개념부터 정리해줘”는 개념 정리 모드입니다. 문제와 개념에는 읽은 파일의
페이지·슬라이드·줄 위치를 연결합니다. 읽지 못한 파일은 출제에 사용하지 않습니다.

로컬 Codex는 TLS 동기화로 내려받은 과목 파일(PDF/PPTX/TXT/MD·DOCX/HWPX 등) 또는 직접 첨부한
파일을 사용합니다. `study` 명령의 대화별 세션과 JSON 이벤트 계약은
[study.md](skills/university-agent/references/study.md)에 있습니다. 호스트 AI가 문제를 생성하고
단답·서술을 평가하며, 코드는 선택한 과목·자료·범위와 정확한 인용 여부, 풀이 상태를 검증합니다.
이 검증만으로 문제의 의미가 정확하다고 보장하지는 않습니다.

ChatGPT 웹·모바일의 현재 Actions는 과목·과제 등을 조회하지만 학교 파일 본문은 받지 않습니다.
대화에 PDF/PPTX/TXT를 직접 첨부해 학습을 요청하면 ChatGPT가 읽을 수 있는 텍스트 범위에서
진행할 수 있습니다. 이 경로는 로컬 `study` 세션 검증기에 연결되어 있지 않습니다.
첨부 본문이 읽히지 않거나 출처 위치가 확인되지 않으면 문제를 만들어서는 안 됩니다.

저장된 TLS 계정이 없으면 `sync_tls.py`가 로컬 터미널에서 아이디와 숨김 비밀번호 입력 양식을 띄웁니다. 로그인 성공 후 아이디는 `~/.university-agent/tls-account.json`(권한 600), 비밀번호는 macOS Keychain에 저장합니다. 스킬 명령은 비밀번호를 환경변수·SQLite·로그·JSON 출력으로 받거나 반환하지 않습니다. 다만 로컬 명령 실행 권한이 있는 AI 실행 환경을 Keychain 비밀값과 완전히 격리하는 장치는 아직 없으므로, 모델이 기술적으로 읽을 수 없다고 보장하지 않습니다. 이 프로젝트의 모델 범위는 ChatGPT/Codex로 고정하며 새 모델 제공자 연동은 추가하지 않습니다.

향후 입력 폼은 [form-pattern.md](skills/university-agent/references/form-pattern.md)의 공통 계약을 사용합니다. 로컬 모드는 숨김 터미널 입력을, ChatGPT 서버 모드는 OAuth 브라우저 로그인 화면을 사용합니다. 서버의 로그인 경로는 Action 스키마에 포함하지 않습니다.

상세 동작은 [skills/university-agent/SKILL.md](skills/university-agent/SKILL.md)에 있습니다. TLS 담당자는 [provider-contract.md](skills/university-agent/references/provider-contract.md)와 `providers/moodle_provider.py`를 기준으로 연동하고, 나머지 팀원은 `features/` 아래에서 기능을 추가합니다. 로컬 DB는 [data-model.md](skills/university-agent/references/data-model.md)와 `database/schema.sql`에 있습니다.

프로젝트 전체 테스트 실행 방법과 통과·미해결·미검증 항목은 [project-tests.md](skills/university-agent/references/project-tests.md)에 정리되어 있습니다.
