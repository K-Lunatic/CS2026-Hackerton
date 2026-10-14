# TLS 강좌 자료·활동 편집 흐름

이 문서는 `tls.kku.ac.kr/course/view.php?id=65513`의 편집 화면에서 실제로 확인한 흐름만 기록한다. `sesskey`, 쿠키, 업로드 식별자와 같은 실행 값은 저장하지 않는다.

## 공통 진입

1. 로그인 후 `/course/view.php?id=<courseId>`를 연다.
2. 화면의 `편집` 양식이 가리키는 `POST /course/view.php`에 화면에서 받은 `id`, `sesskey`, `edit=on`을 보낸다.
3. 편집 모드 응답에서 활동 메뉴의 `data-modname`과 토픽의 실제 편집 링크를 읽는다.

## 토픽 수정

화면에서 관찰된 링크는 다음 형태다.

```text
GET  /course/editsection.php?id=<실제 section id>&sr=0
POST /course/editsection.php
```

현재 양식에서 확인한 필드는 `id`, `sr`, `sesskey`, `name`, `usedefaultname`, `summary_editor[text]`, `summary_editor[format]`, `summary_editor[itemid]`, `availabilityconditionsjson`, `submitbutton`이다. 기본 토픽명을 바꾸려면 `name`을 넣고 `usedefaultname`을 보내지 않는다. 저장 뒤 강좌 화면에서 해당 토픽의 표시명을 다시 읽어야 한다.

## 활동·자료 추가 메뉴

강좌 화면에서 확인된 메뉴와 목록 주소는 다음과 같다.

| 식별자 | 화면 이름 | 목록 주소 |
| --- | --- | --- |
| `ubfile` | 파일 | `/mod/ubfile/index.php?id=<courseId>` |
| `vod` | 동영상 | `/mod/vod/index.php?id=<courseId>` |
| `assign` | 과제 | `/mod/assign/index.php?id=<courseId>` |
| `ubboard` | 게시판 | `/mod/ubboard/index.php?id=<courseId>` |
| `url` | URL링크 | `/mod/url/index.php?id=<courseId>` |
| `label` | 개요 | `/mod/label/index.php?id=<courseId>` |
| `feedback` | 설문조사 | `/mod/feedback/index.php?id=<courseId>` |
| `page` | 웹문서 | `/mod/page/index.php?id=<courseId>` |
| `wiki` | 위키 | `/mod/wiki/index.php?id=<courseId>` |
| `econtents` | 이러닝콘텐츠 | `/mod/econtents/index.php?id=<courseId>` |
| `silverstream` | 자이닉스 실버스트림 | `/mod/silverstream/index.php?id=<courseId>` |
| `estream` | 자이닉스 콘텐츠 | `/mod/estream/index.php?id=<courseId>` |
| `ubchat` | 채팅방 | `/mod/ubchat/index.php?id=<courseId>` |
| `quiz` | 퀴즈 | `/mod/quiz/index.php?id=<courseId>` |
| `forum` | 토론방 | `/mod/forum/index.php?id=<courseId>` |
| `choice` | 투표 | `/mod/choice/index.php?id=<courseId>` |
| `ubpeer` | 팀플평가 | `/mod/ubpeer/index.php?id=<courseId>` |
| `resource` | 파일 | `/mod/resource/index.php?id=<courseId>` |
| `folder` | 폴더 | `/mod/folder/index.php?id=<courseId>` |
| `zoom` | 화상강의 | `/mod/zoom/index.php?id=<courseId>` |

추가 버튼을 누른 뒤 각 항목은 화면 스크립트가 다음 형태의 주소를 만든다.

```text
GET  /course/modedit.php?course=<courseId>&section=<sectionNumber>&add=<식별자>
POST /course/modedit.php
```

초기 점검에서는 20개 유형을 요청했고, 최신 재점검에서는 19개 양식이 열렸으며 `zoom`은 현재 계정에서 HTTP 404였다. 저장은 아직 실행하지 않았다.

## 2026-10-14 실제 양식 점검 결과

다음 명령으로 로그인한 계정의 양식을 다시 확인할 수 있다. 결과에는 입력값이 아니라 필드 이름만 남긴다.

```text
python3 scripts/run_agent.py tls-activity-inspect --course-id 65513
```

현재 확인된 모든 추가 화면의 저장 주소는 `POST /course/modedit.php`로 같고, 유형은 `add` 값으로 구분된다. 추가 화면 주소와 대표 필드는 다음과 같다.

| 유형 | 실제 추가 주소의 `add` 값 | 대표적으로 확인된 필드 |
| --- | --- | --- |
| 파일 | `ubfile` | `name`, `files`, `display`, `download`, `introeditor[text]` |
| 동영상 | `vod` | `name`, `uuid`, `selectcontentbtn`, `uploadcontentbtn`, `timeopen[...]`, `timeclose[...]`, `is_progress` |
| 과제 | `assign` | `name`, `introeditor[text]`, `duedate[...]`, `cutoffdate[...]`, `assignsubmission_*`, `assignfeedback_*` |
| 게시판 | `ubboard` | `name`, `notice`, `comment`, `reply`, `type`, `category`, `upload` |
| URL링크 | `url` | `name`, `externalurl`, `display`, `introeditor[text]` |
| 개요 | `label` | `introeditor[text]` |
| 설문조사 | `feedback` | `name`, `timeopen[...]`, `timeclose[...]`, `anonymous`, `multiple_submit` |
| 웹문서 | `page` | `name`, `page[text]`, `page[format]`, `display` |
| 위키 | `wiki` | `name`, `firstpagetitle`, `defaultformat`, `wikimode` |
| 이러닝콘텐츠 | `econtents` | `name`, `econtentscontents`, `econtentscontentsname`, `selectcontentbtn`, `timeopen[...]`, `timeclose[...]` |
| 실버스트림 | `silverstream` | `name`, `package`, `packagechoose`, `mobilevod`, `timeopen[...]`, `timeclose[...]` |
| 자이닉스 콘텐츠 | `estream` | `name`, `packagechoose`, `timeopen[...]`, `timeclose[...]` |
| 채팅방 | `ubchat` | `name`, `timeopen[...]`, `logs`, `groupmode` |
| 퀴즈 | `quiz` | `name`, `timeopen[...]`, `timeclose[...]`, `timelimit[...]`, `attempts`, `questionsperpage`, `shuffleanswers` |
| 토론방 | `forum` | `name`, `introeditor[text]`, `type`, `maxattachments`, `trackingtype`, `assesstimestart[...]`, `assesstimefinish[...]` |
| 투표 | `choice` | `name`, `option[0..4]`, `limit[0..4]`, `allowmultiple`, `timeopen[...]`, `timeclose[...]` |
| 팀플평가 | `ubpeer` | `name`, `ptype`, `timestart[...]`, `timeend[...]`, `groupmode` |
| 파일(표준) | `resource` | `name`, `files`, `display`, `filterfiles` |
| 폴더 | `folder` | `name`, `files`, `showexpanded`, `display` |
| 화상강의 | `zoom` | 현재 계정에서 `HTTP 404`, 양식 미확인 |

공통 진입은 다음과 같다.

```text
GET  /course/modedit.php?course=<courseId>&section=<sectionNumber>&add=<add 값>
POST /course/modedit.php
```

`files`, `package`, `uuid`, `econtentscontents`처럼 보이는 값은 단순 문자열을 추측해 채우지 않는다. 먼저 화면의 파일 선택기·콘텐츠 선택 결과를 받아야 하며, `sesskey`, 쿠키, 업로드 식별자와 실제 값은 문서·로그·AI 응답에 저장하지 않는다.

`zoom`은 주소를 추측하지 않고 현재 계정의 실제 화면이 404를 반환한 상태로 기록했다. 나중에 메뉴가 다시 노출될 때만 재점검한다.

## 통합 제어

## 퀴즈 문제은행 문항

퀴즈 활동을 만든 뒤 문제를 추가하는 흐름도 화면에서 확인한 주소만 사용한다.

```text
GET  /mod/quiz/edit.php?cmid=<확인된 퀴즈 활동 ID>
GET  /question/addquestion.php?addonpage=0&category=<화면에서 확인한 범주>&cmid=<퀴즈 활동 ID>
GET  /question/question.php?category=<화면에서 확인한 범주>&cmid=<퀴즈 활동 ID>&qtype=<화면에서 선택한 유형>
POST /question/question.php
```

현재 계정의 실제 TLS에서 추가 양식이 제공된 유형은
`multichoiceset`, `truefalse`, `calculatedmulti`, `calculated`, `calculatedsimple`,
`randomsamatch`, `multianswer`, `essay`, `multichoice`, `numerical`, `shortanswer`,
`match`, `description`이다. 설치된 플러그인·권한에 따라 달라질 수 있으므로 실행 시마다
양식을 다시 확인한다. 양식에 존재하는 hidden/default 값, 문항 본문·답안·피드백·채점 기준·단위·허용 오차
등은 그대로 보존해 POST payload를 만들고, 같은 이름의 반복 입력·다중 선택값도 목록으로
보존한다. 계획은 화면에서 확인된 일반 입력 필드만 덮어쓴다.
보호 필드와 세션 값은 계획 파일에 넣지 않는다. 여러 저장 버튼이 있는 경우 일반 저장 버튼 하나만 사용한다.

```text
python3 scripts/run_agent.py tls-quiz-question-inspect --quiz-id '<확인된 퀴즈 활동 ID>'
python3 scripts/run_agent.py tls-quiz-question-control --quiz-id '<확인된 퀴즈 활동 ID>' --plan '<문항 계획.json>'
python3 scripts/run_agent.py tls-quiz-question-control --quiz-id '<확인된 퀴즈 활동 ID>' --plan '<문항 계획.json>' --apply
```

기본은 계획만 확인한다. `--apply` 뒤에도 응답이 로그인 화면으로 바뀌었거나 같은 입력 양식이 다시
표시되면 실패로 처리한다. 저장 결과는 퀴즈 편집 화면과 문제은행에서 다시 읽어 확인해야 한다.

활동별 화면을 따로 열지 않도록 로컬 계획 파일 하나로 여러 토픽의 활동을 함께 점검할 수 있다.

```json
{
  "courseId": "65513",
  "items": [
    {
      "section": 3,
      "module": "label",
      "fields": {"introeditor[text]": "자료 안내"}
    },
    {
      "section": 3,
      "module": "quiz",
      "fields": {"name": "자료구조 · 모의시험", "timeopen[enabled]": "0"}
    }
  ]
}
```

`tls-activity-control`은 한 번 로그인한 세션으로 모든 항목의 실제 양식을 먼저 읽고, 확인된 필드만 채운다. 기본 실행은 계획만 만들며, 결과 확인 뒤 `--apply`를 붙여야 `POST /course/modedit.php`가 실행된다. `sesskey`, 쿠키, 강좌·토픽 대상 ID 같은 보호 필드는 계획에서 바꿀 수 없다. 파일 선택기나 콘텐츠 선택기가 필요한 유형은 화면에서 얻은 실제 선택 결과가 계획에 들어오기 전까지 저장하지 않는다.

## 구현 순서와 주의점

- `label`, `page`, `url`은 텍스트·주소만으로 먼저 구현할 수 있다.
- `ubfile`, `resource`, `folder`는 파일 선택기 업로드를 먼저 완료한 뒤 `files` 필드를 저장해야 한다.
- `vod`, `silverstream`, `estream`, `econtents`는 콘텐츠 선택·업로드 흐름을 별도로 확인해야 한다. 이미 시청한 동영상의 다운로드 제한과 대리 시청 금지는 그대로 적용한다.
- `assign`, `ubboard`, `forum`, `feedback`, `choice`, `wiki`, `ubpeer`는 본문·기간·권한·그룹 필드를 양식에서 읽어 최소 필드부터 구현한다.
- `quiz`는 활동 생성 뒤 문제은행 문항 연결 단계가 별도이므로 활동 생성과 문항 추가를 한 요청으로 합치지 않는다.
- 모든 저장은 로그인한 계정의 자율강좌 목록에서 `운영자` 또는 `교수` 권한과 실제 화면 양식을 확인한 뒤에만 수행한다.
