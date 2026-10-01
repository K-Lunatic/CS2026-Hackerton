# Secure conversation form pattern

## 범위

ChatGPT의 대화 안에 보이는 네이티브 구조화 폼은 MCP/플러그인 확장 기능이다. 현재 터틀넥 Skill은 데스크탑·랩탑의 로컬 숨김 입력 어댑터를 사용하며, 모바일·원격 로그인 화면은 범위에 포함하지 않는다. ChatGPT 네이티브 폼 구현으로 설명하지 않는다.

앞으로 폼을 추가할 때는 [providers/forms.py](../providers/forms.py)의 `FormDefinition`과 `FormField`를 먼저 정의한다.
ChatGPT용 어댑터가 추가될 때는 `requested_schema()` 결과를 `requestedSchema`로 전달하고, 응답을 받자마자 `redact()`를 적용한다.

```python
FormDefinition(
    form_id="example",
    title="예시 입력",
    fields=(
        FormField("name", "이름"),
        FormField("secret", "비밀값", secret=True),
    ),
)
```

규칙:

1. `secret=True` 값은 대화 메시지, 로그, JSON, DB에 넣지 않는다.
2. 수집은 `collect_local()`처럼 숨김 입력을 사용한다.
3. 모델이나 화면에 상태를 돌려줄 때는 `redact()` 결과만 사용한다.
4. 저장이 필요한 값은 폼 수집 함수가 아니라 전용 저장소(Keychain 등)가 처리한다.
5. 비대화형 실행에서는 입력을 추측하거나 환경변수로 대체하지 않고 중단한다.

현재 TLS 로그인은 이 패턴의 첫 구현이다. 저장된 계정이 있으면 폼을 생략하고 macOS Keychain 또는 Windows DPAPI에서 프로세스 메모리로만 읽는다.

## 서버 로그인

`GET /oauth/authorize`가 검증된 GPT 콜백과 state에 연결된 일회용 로그인 ticket을 발급한다. `POST /oauth/login`만 TLS 비밀번호를 받으며 이 경로는 OpenAPI Action 목록에 포함하지 않는다. 비밀번호는 브라우저→서비스 서버→학교 TLS로만 전달하고 저장하지 않는다. HTTPS 종료 프록시는 본문·Authorization 헤더·OAuth 쿼리를 로그에 기록하지 않아야 한다. 서버 응답은 비밀번호나 원본 TLS 오류를 반환하지 않는다. 별도 TLS 자격 증명 저장을 추가하려면 암호화·키 관리 계약부터 정의한다.
