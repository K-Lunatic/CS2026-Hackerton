# 이 Mac에서 ChatGPT 웹·모바일 연결

별도 호스팅 서버·도메인·Docker 없이 이 Mac에서 실행합니다. ChatGPT가 Mac에 도달하도록 HTTPS 터널을 사용합니다. 휴대폰 사용자는 Custom GPT의 로그인 화면에서 TLS에 로그인하며 Python이나 터미널을 사용하지 않습니다. Mac에서 데이터 조회·동기화를 처리하므로 Mac과 실행 프로세스가 켜져 있어야 합니다.

현재 연결 방식은 **Custom GPT Actions**입니다. 로컬 플러그인만 설치하면 일반 ChatGPT 대화에 자동 연결되는 방식은 아닙니다. 실제 GPT ID와 Actions 설정을 마쳐야 조회할 수 있습니다. ChatGPT 앱 디렉터리/MCP 등록은 포함하지 않습니다.

## Mac에서 시작

[University Agent.command](../University%20Agent.command)를 더블클릭합니다. 처음에는 저장한 Custom GPT의 `g-...` ID를 입력합니다. 이후 저장된 ID를 재사용합니다. 터널이 연결되면 비공개 설정 페이지가 Mac 브라우저에서 열립니다.

런처에 Python 3.9 이상과 `cloudflared` 실행 파일이 필요합니다. 이 저장소 작업 환경에는 `.university-agent/bin/cloudflared`로 설치했습니다. 다른 Mac에서 실행할 때는 공식 [다운로드 안내](https://developers.cloudflare.com/tunnel/downloads/)를 따라 설치하거나 `brew install cloudflared`를 사용합니다. 런처는 PATH 또는 저장소의 `.university-agent/bin/cloudflared`를 찾습니다. Python 패키지 설치는 필요 없습니다.

CLI 실행도 가능합니다. 다음 명령은 Mac 운영자용이며 휴대폰 사용자에게 요구하지 않습니다.

```sh
python3 -m server.local --gpt-id g-YOUR-GPT-ID
```

직접 준비한 고정 HTTPS 터널을 쓰려면 `--public-url https://<터널 주소>`를 추가하고 터널을 `http://127.0.0.1:8766`에 연결합니다. 기본 실행은 계정·도메인 없이 임시 Quick Tunnel을 시작합니다. [Cloudflare 공식 문서](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)에 따르면 임시 터널은 테스트용이며 재실행 시 주소가 바뀌고 가동 시간을 보장하지 않습니다. 주소가 바뀌면 GPT의 스키마·OAuth URL을 갱신합니다. 안정적인 고정 주소가 필요할 때도 Mac에서 named tunnel을 운영할 수 있습니다.

## GPT에 연결

Mac에 열리는 `.university-agent/chatgpt/settings.html`을 따라 설정합니다. 파일은 권한 600으로 저장하며 API로 공개하지 않습니다. 학교 비밀번호와 다른 OAuth Client secret이 들어 있으므로 이 파일을 채팅에 보내지 않습니다.

| GPT 설정 | 값 |
| --- | --- |
| Action schema | 런처가 알려준 `https://<터널 주소>/openapi.json` |
| Authentication | OAuth |
| Client ID, Client secret | 비공개 설정 페이지의 값 |
| Authorization URL | `https://<터널 주소>/oauth/authorize` |
| Token URL | `https://<터널 주소>/oauth/token` |
| Scope | `academic:read` |
| Token exchange | POST, `application/x-www-form-urlencoded`; Basic 인증도 지원 |
| Privacy policy | `https://<터널 주소>/privacy` |

GPT Instructions에는 [gpt-instructions.md](gpt-instructions.md)를 넣습니다. OAuth 콜백은 입력한 GPT ID의 `https://chatgpt.com/aip/g-.../oauth/callback`과 `https://chat.openai.com/aip/g-.../oauth/callback`만 허용합니다.

OpenAI의 [GPT Action 인증](https://developers.openai.com/api/docs/actions/authentication)과 [HTTPS·시간 제한](https://developers.openai.com/api/docs/actions/production)을 따릅니다. 계정의 GPT/Actions 사용 가능 여부와 웹·iOS·Android 인증 동작은 실제 대상 계정에서 확인해야 합니다.

## 데이터와 실패 처리

- TLS 비밀번호는 브라우저→터널→Mac→학교 TLS로 전송되며 저장하지 않습니다. Action 스키마에 로그인 비밀번호를 받는 경로는 없습니다. HTTPS 터널 제공자는 연결을 중계합니다.
- TLS 쿠키·OAuth 토큰은 Mac 프로세스 메모리에만 있고 학사 데이터는 `.university-agent/chatgpt/data/`의 사용자별 DB에 있습니다. 기존 Codex DB를 자동 업로드하거나 합치지 않습니다. 설정·DB·터널 실행 파일은 `.gitignore`로 제외합니다.
- 로그인 ticket 10분, OAuth 코드 2분, Access token 1시간, Refresh token 30일입니다. 코드와 Refresh token은 일회용이며 state·콜백·선택적 S256 PKCE를 검증합니다.
- 로그인 이후 동기화는 백그라운드에서 실행합니다. 과제를 저장한 뒤 강의 조회가 실패해도 과제는 조회할 수 있습니다. 실패 시 이미 저장된 과제를 지우지 않습니다.
- 응답의 `sync`는 `syncedAt`, `availableSections`, `running`, `stage`, `error`를 포함합니다. 아직 동기화되지 않은 섹션은 503이며 “과제 없음”이 아닙니다. 프로세스 재시작·학교 세션 만료 후에는 재연결합니다.
- 과제 마감 범위는 시간대가 있는 `due_from` 이상, `due_before` 미만입니다. 이미 마감된 과제도 필요하면 하한 없이 조회합니다. 마감일 없는 과제는 날짜 필터에서 제외되므로 별도 미제출 조회로 확인합니다.
- 페이지 기본 20개, 최대 50개이며 `nextOffset`을 따라 조회합니다. 공지 본문은 1,000자 미리보기입니다. 할 일은 과목명이 있는 항목 목록입니다.
- 연결 해제는 계정의 모든 토큰을 폐기합니다. 데이터까지 삭제하려면 Mac 운영자가 진행 중인 동기화 종료 후 해당 DB와 백업을 삭제합니다.

현재 모바일 지원: 과목·과제·강의·공지·할 일 조회, 동기화, 연결 해제. 일반 과제 편집·북마크·체크포인트·파일 다운로드·팀플 저장은 아직 Actions에 연결하지 않았습니다. 이 기능의 기존 로컬 CLI는 유지합니다.

이 실행기는 단일 Mac에서 연결을 확인하는 용도입니다. 표준 라이브러리 WSGI 실행기와 메모리 토큰을 사용합니다. 여러 프로세스나 장기 운영용으로 확대할 때는 HTTP 실행기·공유 토큰 저장소를 교체해야 합니다.

## 확인

```sh
python3 -m unittest server.test_app server.test_local
python3 -m unittest discover -s skills/university-agent/tests -p 'test_*.py'
```

가짜 TLS로 사용자 격리·OAuth 재사용 차단·만료·토큰 회전·마감 필터·부분 동기화 보존·비밀값 미노출을 확인합니다. 실제 ChatGPT 왕복 조회는 GPT 연결 설정 후 별도로 확인합니다.
