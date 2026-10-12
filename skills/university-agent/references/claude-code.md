# Claude Code 실행

데스크톱의 로컬 Claude Code에서 사용한다. 일반 Claude 웹 채팅·클라우드 세션에는 이 컴퓨터의
Keychain/DPAPI·브라우저·DB가 없으므로 같은 기능을 제공한다고 설명하지 않는다.

- 플러그인 호출은 `/university-agent:turtleneck`, 스킬 폴더 단독 설치는 `/turtleneck`이다.
  Codex의 `$turtleneck`, `codex plugin` 명령이나 Codex 전용 도구를 Claude에서 호출하지 않는다.
- 이 SKILL.md가 있는 절대 디렉터리를 기준으로 실행한다. 프로젝트의 현재 작업 폴더를
  설치 폴더로 가정하지 않는다. 경로에 공백이 있어도 실행 파일과 인수를 각각 인용한다.
  플러그인 루트가 주어지면 그 아래 `skills/university-agent`가 스킬 루트다.
- `Bash`로 기존 Python 실행기를 사용한다. macOS는 `python3`, Windows는 실제 사용 가능한
  `py -3`/`python`을 확인한다. Python이 없으면 패키지의 `run_with_python.command` 또는
  `run_with_python.bat`를 사용한다. 실행 환경 추가 설치는 사용자 승인 후 진행한다.
- 설치 갱신과 DB 쓰기·localhost 서버·브라우저 열기는 Claude의 권한 승인 절차를 따른다.
  거부되면 해당 기능만 중단한다. `--dangerously-skip-permissions`나 전역 허용 규칙을 추가하지 않는다.
- `setup_turtleneck.py`는 연결 프로세스를 별도로 시작하므로 장시간 대기 명령을 만들 필요가 없다.
  사용자가 직접 TLS 로그인·에타 2차 인증을 마치면 `--status`로 완료를 확인한다.
  비밀번호·쿠키·Keychain 값을 Read/Bash 결과나 모델 대화로 가져오지 않는다.
- 공부는 [공통 학습 계약](study.md)을 그대로 사용한다. SOURCE의 현재 부분만 분석하고
  `extract` 성공 뒤 다음 부분으로 넘어간다. 큰 JSON은 작업용 임시 파일에 기록해 `--event-file`로
  전달한다. 셸 문자열에 긴 본문·코드·따옴표를 억지로 끼워 넣거나 내부 자료를 출력하지 않는다.
- `webOpened: true`이면 반환 URL을 안내하고 서버를 또 실행하지 않는다. 서버를 직접 실행해야 할
  때만 `exam_web.py`를 Bash의 백그라운드 작업으로 유지한다. 시험 중에는 작업을 종료하지 않는다.
  확인은 반환된 localhost URL의 HTML·CSS·문항 API로 하고, 새 HTML이나 채팅 퀴즈로 대체하지 않는다.
- 사용자가 제출 후 돌아오면 `status`의 `hostOnly.answers`를 **현재 Claude가** 평가하고
  `gradeId`와 모든 기준의 `met`/`feedback`을 `grade_batch`로 전달한다. 다른 모델/API 키는 필요 없다.
  풀이를 기다려 달라는 요청에는 기존 `exam_web.py --wait 30`을 사용한다.
- 컨텍스트가 줄어들거나 요약된 뒤에는 현재 대화의 conversation ID·과목·선택 자료·requestId를
  유지하고 `study ... status`로 저장된 진행을 확인한다. 유실된 SOURCE나 답안을 기억으로 복원하지
  않는다. ID를 잃었다면 `exam-history` 등 허용된 목록에서 사용자가 이어갈 기록을 고른다.

학교·에타 로그인 정보와 데이터 위치는 Codex와 같다. 두 앱이 같은 기기에서 기존 데이터를
재사용할 수 있지만 동시에 동기화를 시작하거나 다른 대화의 진행 상태를 덮어쓰지 않는다.
