# 팀플 진행 정리 및 인수인계

Python 표준 라이브러리만 사용한다. DB/TLS와 독립적이며 기본 입력은 붙여넣은 텍스트다.
기존 프로젝트에 파일 업로드·텍스트 추출기가 없어 파일 입력은 추가하지 않았다.

## 지금 사용: 스킬을 호출한 AI와 연결

기존 Codex 스킬을 호출한 AI가 실제 분석을 수행한다. 별도 API나 DB가 필요 없다.
요청만 있고 기록이 없으면 사용자에게 회의록/작업 기록을 요청한다.

```bash
python3 scripts/run_agent.py ask --text '팀플 진행 상황 정리해줘.' \
  --records '10월 2일 회의: 민수는 로그인 화면 담당. 현우는 DB 테이블 생성 완료.' \
  --project-name '학교생활 AI' --team '민수: 화면, 현우: DB' --prepare
```

1. `data.messages`의 시스템 분석 규칙과 사용자 데이터로 AI가 JSON을 생성한다.
2. 같은 원문과 선택 입력을 유지하여 `--prepare` 대신 `--analysis-json '<AI가 생성한 JSON>'`으로 실행한다.
3. 성공한 `answer`를 보여준다. 실패하면 원문에 맞게 분석을 수정하고 재검증한다.

`--prepare`는 **분석 요청**이며 진행 상황 분석 결과가 아니다. `--analysis-json`은 외부에서
제공한 분석이라는 뜻이며 Python이 AI를 직접 호출했다는 뜻이 아니다.
AI가 스킬에서 두 단계를 수행할 때 shell quoting 대신 Python 함수 호출도 가능하다.

## 향후 실제 API 자동 호출

팀원이 연동 정보를 확정하면 다음 환경 변수를 설정한다. 기본값·샘플 응답은 없다.

- `TEAM_HANDOVER_API_URL`: Chat Completions 호환 **전체 URL** (예: 팀 서버의 `/v1/chat/completions`)
- `TEAM_HANDOVER_MODEL`: 모델 식별자
- `TEAM_HANDOVER_API_KEY`: 인증이 필요한 서버의 키 (출력하지 않음)

`--prepare`와 `--analysis-json` 없이 실행하면 Python이 API를 호출한다.
서버는 `messages`, `temperature: 0`, `response_format: {type: json_object}`를 지원해야 한다.
응답은 `choices[0].message.content`에 JSON 문자열을 반환해야 한다.
HTTPS 또는 로컬 HTTP만 허용하며 리다이렉트를 따라가지 않는다. 연결 제한은 60초다.

```bash
python3 scripts/run_agent.py handover --text '회의록과 작업 기록' --assignee '민수'
python3 scripts/run_agent.py ask --text '아직 안 끝난 작업과 담당자를 알려줘.' --records '회의록과 작업 기록'
```

`--assignee`는 **작업을 넘기는 기존 담당자**다. 선택하면 그 사람과 담당자 미정 작업을
인수인계에 포함한다. 전체 프로젝트 요약은 유지한다. 다음 담당자의 이름을 추정하지 않는다.
`--team`은 참고 문맥이다. 작업 담당 배정의 근거는 회의록/작업 기록에 있어야 한다.

## 다른 팀원과의 연결

```python
from features.handover import create_handover, prepare_handover, HandoverError

messages = prepare_handover(text, project_name=project_name, team=team, assignee=assignee)
# 기존 AI 호출 함수를 그대로 주입: messages -> JSON 문자열
result = create_handover(text, project_name=project_name, team=team,
                         assignee=assignee, ai_call=existing_ai_call)
# 또는 현재 AI가 만든 결과를 검증
result = create_handover(text, analysis_json=generated_json)
```

입력 문서를 DB에서 읽는 쪽은 원문 텍스트를 넘긴다. 이 기능은 TLS/DB를 직접 호출하지 않는다.
`create_handover`는 성공 시 dict, 실패 시 `HandoverError`를 반환/발생시킨다.
runner는 기존 `toolCalls`, `data`, `answer` 형식을 유지한다. 실패 시 `data: null`,
`error.code: HANDOVER_FAILED`와 비정상 종료 코드 1을 반환한다.
`ask --text`는 요청, `--records`는 분석 데이터로 분리한다.

출력: `summary`, `completed`, `inProgress`, `pending`, `decisions`, `roles`, `handover`.
전체 `tasks`와 `records`를 함께 보존한다. 각 작업은 `title`, `owner`, `deadline`,
`deadlineKind`, `status`, `evidence`를 가진다. `evidence`는 원문 행 식별자와 정확한 인용이다.
`pending`에는 미완료와 확인 필요가 포함된다. `suggestions`는 `AI 제안`으로 표시하며
`basedOn`의 작업 인덱스를 통해 원문 근거를 추적한다.
`analysisSource`: `remote-ai` / `injected-provider` / `supplied-analysis`.

## 검증 및 제한

```bash
python3 -m unittest discover -s skills/university-agent/tests -v
# 실제 API 설정 후에만 (실제 기록이 서버에 전송됨):
TEAM_HANDOVER_LIVE_TEST=1 python3 -m unittest discover -s skills/university-agent/tests -v
```

기본 테스트는 명시적인 테스트 응답과 로컬 HTTP 모의 서버를 사용한다. 정상 예시,
정보 부족/예정, 충돌 보존, 원문에 없는 인용·담당자·기한 거부, 자연어 라우팅,
AI 실패 및 빈 입력을 검증한다. 이 테스트는 AI의 의미 분석 정확도를 증명하지 않는다.
실제 API 테스트 3개는 별도 설정이 없으면 skip된다.

원문 인용 및 담당자·기한이 인용에 등장하는지는 코드로 검사한다. 완료/예정의 의미,
작업 동일성, 시간 순서, 지시문과 기록 구분은 AI 분석 규칙에 의존한다.
인용이 있다고 해서 해석까지 사실임이 보장되지는 않는다. 충돌은 AI가 확인 필요로
남겨야 한다. 입력 한도는 선택 입력 포함 80,000자이며 긴 자료는 나누어 입력한다.
자동 파일 추출, DB 저장, 원문 문서 URL 자동 조회는 지원하지 않는다.

## 화면 실행 및 대화 → 화면 연결

```bash
python3 skills/university-agent/scripts/handover_web.py
# 브라우저에서 http://127.0.0.1:8765/ 열기
# 다른 포트: --port 8766
```

저장소에 기존 프론트엔드/디자인 컴포넌트가 없어 독립적인 반응형 화면을 추가했다.
서버는 Python 표준 라이브러리, 화면은 HTML/CSS/JavaScript만 사용한다.
SQLite/TLS 기능의 스키마와 코드는 변경하지 않는다. 이 화면은 로컬 주소에만 바인딩한다.

대화 AI는 기존 호출 방식으로 화면 링크를 얻을 수 있다 (서버는 먼저 실행해야 한다).

```bash
python3 skills/university-agent/scripts/run_agent.py ask --text '팀플 정리해줘' --ui
```

반환되는 `data.url`은 `http://127.0.0.1:8765/?request=...`다. 원문은 URL에 넣지 않는다.
화면에서도 대화 요청 → 스킬 호출 → 자료 입력을 진행할 수 있다.
다른 포트를 선택했다면 실행된 서버 URL에 `?request=팀플%20정리해줘`를 붙여 연다.
링크는 사용자가 화면을 열어야 하며 CLI가 브라우저를 자동으로 실행하지 않는다.

AI API 설정이 있으면 화면은 AI 모드로 시작한다. 설정 전에는 **임시 규칙 분석**으로
시작하며, 결과에도 **AI 아님**을 표시한다. 임시 분석은 붙여넣은 실제 입력을 분석하고
고정 샘플 응답을 반환하지 않는다. 이름+`는/은`, 한국어 월/일, 명시적인 상태 표현을
중심으로 처리하므로 복잡한 기록은 현재 AI의 분석 또는 실제 API 연결을 사용해야 한다.
알 수 없는 상태/담당자/기한은 확인 필요/미정으로 보존한다.

## 프론트엔드 API 계약

| 요청 | 입력 | 응답 |
| --- | --- | --- |
| `GET /api/config` | 없음 | `aiConfigured` (키/모델 정보는 반환하지 않음) |
| `POST /api/start` | `{request}` | 기존 스킬의 팀플 의도 판별, `needsInput`, `answer` |
| `POST /api/analyze` | `{text, sourceName?, mode: "ai" 또는 "local"}` | `{toolCalls, analysisId, data}` |
| `POST /api/handover` | `{analysisId, edits: [{status, owner, deadline}], assignee?: ""}` | `{draft}` |

`edits`는 원래 `tasks` 순서와 같은 길이여야 한다. 원문 근거/제목은 클라이언트가 수정할
수 없다. 서버가 보관한 원본에 상태·담당자·기한만 적용한다. 수정 표시는 원래 분석과
비교하여 표시한다. 빈 담당자·기한은 미정이다. 초안에는 원문과 수정 이력이 함께 남는다.
특정 담당자를 선택해도 미정 작업은 함께 포함한다. 미정 자료 위치/담당자/기한/진행 상태는
확인 사항에 남긴다. 초안 생성은 추가 AI 호출 없이 수정된 결과를 구조화한다.
실패 응답은 `{error: "사용자용 메시지"}`이며 화면은 입력과 이전 결과/초안을 보존한다.

다른 프론트엔드에서 `web/`의 화면을 연결하거나 위 API를 같은 출처의 경로로 연결한다.
다른 AI 호출 함수를 가진 서버는 `create_server(port, ai_call=existing_ai_call)`로 주입한다.
AI 키는 서버 환경 변수에만 설정하고 브라우저에 전달하지 않는다.
완전한 새 앱/채팅 시스템을 만드는 대신 팀플 화면만 추가했다.

## 화면 검증

```bash
python3 -m unittest discover -s skills/university-agent/tests -v
# 브라우저 도구는 테스트용 임시 경로에 설치 (앱 실행에는 필요 없음)
npm install --prefix /tmp/handover-browser playwright --no-audit --no-fund
node /tmp/handover-browser/node_modules/playwright/cli.js install chromium
PLAYWRIGHT_MODULE=/tmp/handover-browser/node_modules/playwright \
  node skills/university-agent/tests/handover_browser.cjs
```

Chromium에서 대화 호출, 입력 누락, 진행 표시, 실패 후 입력 보존/재시도, 작업 수정,
원문 보존, 특정 담당자 인수인계, 미정 확인 항목, 초안 편집 후 재생성 취소,
생성 중 편집 보호, 실제 클립보드 복사, 390px 모바일 배치와 가로 넘침을 검사한다.
AI 제안 표시 테스트는 명시적인 모의 응답을 사용한다. 실제 AI 테스트는 별도 설정이
필요하다. 브라우저 테스트는 외부 AI를 호출하지 않는다.
스크린샷은 OS 임시 폴더의 `handover-ui`에 저장한다. `HANDOVER_SCREENSHOTS`로 경로를 지정할 수 있다.

화면은 서버 프로세스의 메모리에 최대 100개 분석을 보관한다. 새로고침/서버 재시작 뒤
수정과 초안을 자동 복원하지 않는다. 페이지를 벗어나면 브라우저 확인을 요청하며,
남길 초안은 내용 복사로 저장해야 한다. 여러 기기 공유, 파일 업로드, DB 저장은 제외했다.
HTTP에서도 localhost는 클립보드를 지원하지만 브라우저 권한이 막히면 내용을 선택하고
수동 복사 방법을 안내한다. 편집된 초안은 재생성 확인 또는 명시적 교체 없이 덮어쓰지 않는다.
