# 터틀넥 엔드포인트 지도

2026-10-14 기준으로 코드가 실제 호출하거나 수동으로 관찰한 주소를 흐름별로 정리한 문서다.
아래 주소는 경로 템플릿만 적으며, 개인별 ID·쿠키·세션 키·서명된 다운로드 주소는 기록하지 않는다.

표시 의미:

- `구현`: 현재 로컬 실행기가 호출하고 결과를 검증한다.
- `관찰`: 실제 화면/요청에서 확인했지만 자동화 코드에는 연결하지 않았다.
- `미구현`: 메뉴가 존재해도 엔드포인트를 확인하지 않았거나 안전한 계약이 없다.
- `로컬`: 외부 학교 서비스가 아니라 이 컴퓨터에서만 열리는 주소다.

## 1. TLS 로그인과 학사 데이터

기본 호스트: `https://tls.kku.ac.kr`

| 흐름 | Method | 경로 | 상태 | 용도와 제한 |
| --- | --- | --- | --- | --- |
| 로그인 화면/제출 | GET, POST | `/login/index.php` | 구현 | 로그인은 로컬 보안 폼에서만 진행한다. 비밀번호는 결과에 넣지 않는다. |
| 내 강좌 | GET | `/local/ubion/user/` | 구현 | 강좌 링크에서 `courseId`를 확인한다. |
| 강좌 상세 | GET | `/course/view.php?id={courseId}` | 구현 | 일반 동기화는 활동 링크를 재사용하고, 자율강좌 구조 조회는 토픽·활동 이름과 ID만 로컬 설계 자료로 추출한다. |
| 과제 | GET | `/mod/assign/view.php?id={activityId}` | 구현 | 마감일과 제출 상태를 읽기만 한다. 제출 요청은 보내지 않는다. |
| 동영상 상태 | GET | `/mod/vod/viewer.php?id={activityId}` | 구현 | `is_progress`, `is_complete`, 재생 시간과 시청 기간만 읽는다. 시청/출석 요청은 보내지 않는다. |
| 공지 목록 | GET | `/mod/ubboard/view.php?id={boardId}` | 구현 | 강좌에 연결된 공지 게시판만 읽는다. |
| 공지 본문 | GET | `/mod/ubboard/article.php?id={boardId}&bwid={articleId}` | 구현 | 제목·본문·작성 시각을 읽는다. |
| 일반 파일 활동 | GET | `/mod/resource/view.php?id={activityId}` | 구현 | 관찰된 원본 주소가 있을 때만 다음 단계로 이동한다. |
| 뷰어 파일 활동 | GET | `/mod/ubfile/view.php?id={activityId}` | 구현 | 뷰어·원본 링크를 최대 4단계까지 확인한다. |
| 문서 뷰어 | GET | `/local/ubdoc/?id={documentId}&tp=m&pg=ubfile` | 구현 | 학교가 표시한 다운로드 상태의 기준 주소다. 주소를 추측하지 않는다. |
| 문서 상태 | POST | `/local/ubdoc/worker.php` | 구현 | `job=checkState`로 `state_code`, `file_download`만 확인한다. 변환 시작·업로드 요청은 보내지 않는다. |
| 문서 원본 | GET | `/local/ubdoc/download.php?id={documentId}&tp=m&pg=ubfile` | 구현 | 관찰된 주소와 허용 상태가 모두 확인된 경우에만 파일을 받는다. |
| 직접 파일 | GET | 관찰된 동일 학교 `pluginfile.php` 또는 파일 주소 | 구현 | 응답 헤더·원본 형식·학교 도메인을 검증한 뒤에만 저장한다. |

과제·강의·공지·자료는 강좌 상세 페이지의 활동 링크에서 발견한다. 목록에 없는 ID를
만들어 요청하지 않는다. 자료는 화면에 다운로드 금지가 보이거나 서버가 거부한 경우
가져오지 않으며, 상태가 불명확한 경우에도 파일을 받지 않는다.

## 2. TLS 완료 강의의 음성 추출

이 흐름은 `completed=true`인 강의만 대상으로 하며, 시청 기록을 변경하지 않는다.

| 단계 | 주소 | 상태 | 제한 |
| --- | --- | --- | --- |
| 완료 재확인 | `/mod/vod/viewer.php?id={activityId}` | 구현 | 현재 응답에서도 `is_complete=1`인지 다시 확인한다. |
| 재생 목록 | 관찰된 `https://tls.kku.ac.kr` 또는 `https://tlsvod.kku.ac.kr`의 `.m3u8` | 구현 | 뷰어에서 실제 주소를 얻는다. 보호된 HLS 키·DRM은 거부한다. |
| 음성/조각 | 재생 목록이 가리키는 동일 허용 호스트의 주소 | 구현 | 최대 4개 연결 추적, 조각 수·용량·형식을 제한한다. |

`attendance`, `progress`, `complete`를 변경하는 엔드포인트는 의도적으로 사용하지 않는다.

## 3. TLS 자율강좌와 Moodle 백업

자율강좌 목록은 로그인 후 읽기 전용으로 자동화하며, 강좌 변경 주소는 여전히 승인 전용이다.

| 흐름 | Method | 경로 | 상태 | 현재 정책 |
| --- | --- | --- | --- | --- |
| 내 자율강좌 목록 | GET | `/local/ubeclass/my.php` | 구현 | 표의 공개 여부·분류·강좌명·기간·역할·상태와 관찰된 운영자 표시만 읽는다. 삭제·수정 요청은 보내지 않는다. |
| 자율강좌 신청 화면 | GET | `/local/ubeclass/request.php` | 관찰 | 화면에 표시된 필드와 동의 내용을 먼저 확인한다. |
| 자율강좌 생성 제출 | POST | `/local/ubeclass/action.php` | 관찰 | `type=add_eclass`가 포함된 요청을 관찰했지만 자동 전송하지 않는다. |
| 백업 생성 화면 | GET | `/backup/backup.php?id={courseId}` | 관찰 | 운영자 화면에서 백업 설정·구성·실행을 확인했다. 백업 실행 요청은 화면 승인 흐름에 한정한다. |
| 내 백업 목록 | GET | `/backup/restorefile.php?contextid={contextId}` | 구현 | 먼저 `/course/view.php?id={courseId}`의 `<body class="context-{contextId}">`에서 현재 강좌 컨텍스트를 읽은 뒤 개인 백업 영역을 조회한다. |
| 표시된 백업 파일 | GET | `/pluginfile.php/{context}/user/backup/{filename}?forcedownload=1` | 구현 | 목록에서 같은 강좌의 다운로드 링크를 발견한 뒤 같은 학교 원본에서만 로컬로 저장한다. URL을 추측하지 않는다. |
| 백업 복구 업로드 | POST | `/backup/restorefile.php?contextid={contextId}` | 관찰 | 파일 선택기 양식의 업로드 진입점이다. 로컬 백업 검증 후에만 운영자 화면에서 파일을 선택한다. |
| 복구 설정·목적지·스키마 | GET, POST | `/backup/restore.php?contextid={contextId}&restore={restoreId}&stage={stage}` | 실행 검증 | 현재 자율강좌 병합, 사용자 정보·로그·성적 이력 제외, 활동·파일·블록·필터·달력·팀 포함 흐름을 실제 완료했다. 토큰 값은 기록하지 않는다. |
| 로컬 백업 조립 | 로컬 파일 | `scripts/assemble_moodle_backup.py` | 구현 | 검증된 `.mbz` 템플릿의 기존 토픽에 새 퀴즈 활동과 Moodle XML 문항을 추가한다. TLS 요청은 보내지 않는다. |
| 로컬 백업 재구성 | 로컬 파일 | `scripts/rebuild_moodle_backup.py` | 구현 | 로컬 수강 과목 기준으로 기존 활동·파일·문제은행을 제거하고 빈 토픽 구조를 만든다. TLS 요청은 보내지 않는다. |
| 직접 XML import API | - | 확인된 계약 없음 | 미구현 | 주소를 추측하거나 Moodle 내부 API를 임의로 호출하지 않는다. |

자율강좌를 진행할 때는 `features/autonomous_course_policy.py`와 `features/moodle_xml.py`의
로컬 정책을 먼저 통과시킨다. 현재 XML 생성은 로컬 파일 생성까지만 완료된 것이며, TLS에
업로드·복구되었다고 말하지 않는다. 검증된 기존 `.mbz`를 템플릿으로 쓰는 로컬 조립은 가능하지만,
이미지가 포함된 문항은 파일 연결 전 중단한다. 실서버 변경은 별도 즉시 승인 이후에만 검토한다.
사용자가 제공한 강좌 URL은 먼저 `autonomous-course-verify`로 로컬 수강 목록과 대조한다.
이 대조는 주소·수강 데이터 확인일 뿐 운영자 권한 확인이 아니며, 원격 강좌 변경을 승인하지 않는다.
현재 확인된 강좌에서는 백업 생성 화면으로 `2026년 2학기 자율강좌`의 백업을 실제 생성했고,
개인 백업 영역에 표시된 61.7KB `.mbz` 파일을 로컬 Downloads에 저장했고, 운영자 화면에서 현재 자율강좌에 병합 복구를 완료했다. 복구 후 강의 개요·자료구조 토픽과 게시판·퀴즈 복사본이 강좌 화면에 나타나는 것을 확인했다. 기존 강좌 삭제 후 복구는 실행하지 않았다.

## 4. 에브리타임 로그인과 세션

| 흐름 | Method | 주소/경로 | 상태 | 제한 |
| --- | --- | --- | --- | --- |
| 공식 로그인/2차 인증 | 브라우저 | `https://account.everytime.kr/login` | 구현 | 사용자가 직접 입력한다. 비밀번호·OTP는 코드나 대화로 받지 않는다. |
| 인증 강의실 확인 | GET | `https://everytime.kr/lecture` | 구현 | 로그인 성공 판정에만 사용한다. |
| 임시 브라우저 대상 확인 | GET | `http://127.0.0.1:{port}/json/list` | 구현 | 로컬 DevTools 연결용이며 외부에 공개하지 않는다. |
| 에타 API | POST | `https://api.everytime.kr{path}` | 구현 | `Client.post()`의 허용 경로만 사용한다. 세션 쿠키는 OS 보호 저장소에서 메모리로만 불러온다. |

브라우저 인증이 끝난 뒤에는 전용 HTTP 세션을 사용하며, 브라우저 탭을 크롤링 대상으로
재사용하지 않는다. 로그인 실패·추가 인증·캡차가 나오면 우회하지 않고 공식 화면으로
돌아간다.

## 5. 에브리타임 읽기 흐름

| 기능 | API Method | 경로 | 상태 |
| --- | --- | --- | --- |
| 내 강의 목록 | POST JSON | `/v2/find/lecture/list/mine` | 구현 |
| 강의실 과목 화면 | GET | `/lecture/view/{courseId}` | 구현 |
| 강의평 등록 과목 검색 | POST | `/find/lecture/list/keyword` | 구현 |
| 강의평 본문 목록 | POST | `/find/lecture/article/list` | 구현 |
| 강의평 집계 | POST | `/find/lecture` | 구현 |
| 학기 목록 | POST XML | `/find/timetable/subject/semester/list` | 구현 |
| 개설 과목 필터 | POST XML | `/find/timetable/subject/filter/list` | 구현 |
| 개설 과목 검색 | POST XML | `/find/timetable/subject/list` | 구현 |
| 시간표 목록 | POST XML | `/find/timetable/table/list/semester` | 구현 |
| 시간표 상세 | POST XML | `/find/timetable/table` | 구현 |
| 게시판 글 목록 | POST XML | `/find/board/article/list` | 구현 |
| 게시판 글·댓글 상세 | POST XML | `/find/board/comment/list` | 구현 |

게시판 읽기는 사용자가 지정한 게시판·기간·최대 개수만 대상으로 한다. 친구·쪽지·그룹
대화는 최초 수집에서 제외한다.

## 6. 에브리타임 변경 흐름

변경 주소는 `everytime_actions.py`에서 미리보기 생성 후 승인된 계획에만 사용한다.

| 기능 | Method | 경로 | 상태 |
| --- | --- | --- | --- |
| 시간표 생성 | POST | `/save/timetable/table` | 구현·승인 필요 |
| 시간표 이름 변경 | POST | `/update/timetable/table/name` | 구현·승인 필요 |
| 대표 시간표 지정 | POST | `/update/timetable/table/primary` | 구현·승인 필요 |
| 시간표 삭제 | POST | `/remove/timetable/table` | 구현·승인 필요 |
| 직접 일정 생성 | POST | `/save/timetable/subject/custom` 후 `/save/timetable/table` | 구현·승인 필요 |
| 직접 일정 수정 | POST | `/update/timetable/subject/custom` 후 `/save/timetable/table` | 구현·승인 필요 |
| 과목 추가/삭제 | POST | `/save/timetable/table` | 구현·승인 필요 |
| 글 작성/수정 | POST | `/save/board/article` | 구현·승인 필요 |
| 댓글 작성 | POST | `/save/board/comment` | 구현·승인 필요 |
| 글 삭제 | POST | `/remove/board/article` | 구현·승인 필요 |
| 댓글 삭제 | POST | `/remove/board/comment` | 구현·승인 필요 |
| 글 공감/스크랩 | POST | `/save/board/article/vote`, `/save/board/article/scrap` | 구현·승인 필요 |
| 글 공감 취소/스크랩 취소 | POST | `/remove/board/article/scrap` | 구현·승인 필요 |
| 댓글 공감 | POST | `/save/board/comment/vote` | 구현·승인 필요 |

첨부 글 수정, 친구 추가, 쪽지, 거래, 신고, 학점 계산기 변경, 학교 수강신청은 구현하지
않는다. 실제 주소와 요청 필드가 확인되기 전까지 새 엔드포인트를 만들지 않는다.

## 7. 로컬 시험지와 선택적 게이트웨이

### 로컬 시험지

시험 서버는 `http://127.0.0.1:{port}/#{examToken}`으로만 열고, API 요청에는
`X-Exam-Token`이 필요하다.

| 기능 | Method | 경로 |
| --- | --- | --- |
| 시험 상태/목록 | GET | `/api/exam`, `/api/history` |
| 시험 시작/재시험 | POST | `/api/start`, `/api/retry` |
| 임시 답안/제출 | POST | `/api/draft`, `/api/submit` |
| 헷갈림·중요 표시 | POST | `/api/confusion`, `/api/focus` |
| 개념 매칭 | GET | `/api/match` |
| 문제 세트 선택 | GET, POST | `/api/sets`, `/api/sets/select` |

### ChatGPT Actions 게이트웨이

선택 기능이며 기본 로컬 흐름과 분리한다.

| 기능 | Method | 경로 |
| --- | --- | --- |
| 상태 확인 | GET | `/health` |
| 개인정보 안내 | GET | `/privacy` |
| Actions 스키마 | GET | `/openapi.json` |
| OAuth 승인/로그인/토큰 | GET, POST | `/oauth/authorize`, `/oauth/login`, `/oauth/token` |
| TLS 동기화 | GET, POST | `/v1/sync` |
| 학사 조회 | GET | `/v1/courses`, `/v1/assignments`, `/v1/lectures`, `/v1/notices`, `/v1/todos` |
| 진행 기록 | GET, POST | `/v1/checkpoints`, `/v1/checkpoint`, `/v1/assignments/complete` |
| 학습·시험 | GET, POST | `/v1/study/analysis`, `/v1/study/exam`, `/v1/study/status`, `/v1/study/grade` |
| 학습 팩 링크/업로드 | POST, GET | `/v1/study-pack/link`, `/study/{ticket}`, `/study/{ticket}/upload` |
| 연결 해제 | POST | `/v1/disconnect` |

OAuth 토큰과 로컬 시험 토큰은 각각 프로토콜상 필요한 위치에서만 사용한다. TLS/에타
비밀번호·원본 쿠키·세션 값은 학술 응답이나 로그에 포함하지 않는다.

## 스킬 적용 규칙

1. 새 기능은 먼저 이 문서에서 대상 흐름과 상태를 찾는다.
2. `구현` 주소는 기존 provider/runner를 재사용하고, feature에서 직접 HTTP 요청하지 않는다.
3. `관찰` 주소는 사용자 승인·소유권·응답 검증 없이는 자동 호출하지 않는다.
4. `미구현` 영역은 주소를 추측하지 말고 확인 불가로 보고한다.
5. 모든 요청은 허용된 학교/에타 원본과 현재 사용자가 지정한 범위 안에서만 만든다.
6. 엔드포인트 변경이 확인되면 코드·테스트·이 문서를 함께 갱신한다.
