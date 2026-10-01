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
