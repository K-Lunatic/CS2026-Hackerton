# Secure conversation form pattern

## 범위

ChatGPT의 대화 안에 보이는 네이티브 구조화 폼은 MCP/플러그인 확장 기능이다. 현재 저장소는 Skill만 배포하므로 앱·서버를 추가하지 않고, 동일한 폼 계약을 로컬 터미널 어댑터로 사용한다.

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

현재 TLS 로그인은 이 패턴의 첫 구현이다. 저장된 계정이 있으면 폼을 생략하고 Keychain에서 프로세스 메모리로만 읽는다.
