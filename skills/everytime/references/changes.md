# 웹 변경 기능

공식 웹 코드에서 확인한 요청만 구현한다. 참조:
[게시판](https://everytime.kr/js/board.index.js),
[시간표 저장](https://everytime.kr/js/timetable.tablesave.js),
[시간표 설정](https://everytime.kr/js/timetable.index.js),
[직접 추가 일정](https://everytime.kr/js/timetable.customsubjects.js).
이들은 공식 공개 API 계약이 아니므로 화면/API 변경이나 인증 제한 시 중단한다.

## 실행

사용자 요청을 아래 JSON으로 로컬 파일에 작성한 후:

```text
python3 <스킬 경로>/scripts/everytime_actions.py --prepare <요청.json>
python3 <스킬 경로>/scripts/everytime_actions.py --apply <planId> --confirm <confirmation>
python3 <스킬 경로>/scripts/everytime_actions.py --list-actions
```

첫 명령은 읽기 검증과 DB에 미리보기 저장만 한다. 미리보기를 사용자에게 설명하고 승인받기
전에는 두 번째 명령을 실행하지 않는다. 이미 대상·최종 내용까지 명시적으로 승인한 요청은
그 내용과 미리보기가 정확히 일치하는지 확인하고 적용할 수 있다. 내용이 바뀌면 다시 승인받는다.
미리보기는 20분 후 만료되며, 승인 시에도 실제 대상의 변경 여부를 다시 확인한다.
`confirmation`은 내용 일치 확인값이지 AI를 격리하는 권한 장치가 아니다.

## 작업별 입력

모든 입력에 `action`을 넣는다. 표의 선택 항목 이외의 값·임의 URL·인증값은 허용하지 않는다.
시간표 작업은 `year`, `semester`를 반드시 지정한다. `table.create`를 제외한 시간표 작업은
`tableId`도 필수다. 학기는 `1`, `2`, `여름`, `겨울`이다.
게시판 작업은 `boardId`, 글 대상 작업은 `articleId`, 댓글 대상 작업은 `commentId`를 지정한다.

| action | 추가 입력 | 요청 |
| --- | --- | --- |
| `table.create` | `name` | `/save/timetable/table` |
| `table.rename` | `name` | `/update/timetable/table/name` |
| `table.primary` | 없음 | `/update/timetable/table/primary` |
| `table.delete` | 없음 | `/remove/timetable/table` |
| `table.add_subject` | `subjectId` | 전체 기존 과목을 보존해 `/save/timetable/table` |
| `table.remove_subject` | `subjectId` | 해당 블록만 빼고 `/save/timetable/table` |
| `table.add_custom` | `name`, `meetings`, 선택 `professor` | `/save/timetable/subject/custom` → 시간표 저장 |
| `table.edit_custom` | 음수 `subjectId`, `name`, `meetings`, 선택 `professor` | `/update/timetable/subject/custom` → 시간표 저장 |
| `article.create` | `text`, 제목형 게시판은 `title`, 선택 `anonymous`, `question`, `categoryId` | `/save/board/article` |
| `article.edit` | `text`, 제목형 게시판은 `title`, 선택 `anonymous`, `question`, `categoryId` | `/save/board/article` + `article_id` |
| `article.delete` | 없음 | `/remove/board/article` |
| `article.like` | 없음 | `/save/board/article/vote` |
| `article.scrap` | 없음 | `/save/board/article/scrap` |
| `article.unscrap` | 없음 | `/remove/board/article/scrap` |
| `comment.create` | `text`, 선택 `anonymous`, 대댓글이면 `commentId` | `/save/board/comment` |
| `comment.delete` | `commentId` | `/remove/board/comment` |
| `comment.like` | `commentId` | `/save/board/comment/vote` |

새 글·댓글은 익명 기본값 `true`, 새 글의 질문 여부는 `false`다. 사용자에게 익명 여부를
반드시 보여준다. 글 수정은 생략한 익명/질문 설정을 기존 값으로 유지한다.
질문 글은 댓글이 달리면 수정/삭제할 수 없으므로 작성 전 그 제약을 알린다.
게시판 규칙은 미리보기의 `before.board.placeholder`를 참고한다.

시간표 과목 추가는 `everytime.py --search-subjects "검색어" --year ... --semester ...` 결과의
`subjectId`로만 지정한다. 강의실의 `courseId`와 다르다. 캠퍼스·학기는 실제 웹 응답에서 확인한다.
분반 코드의 현재 개설 여부를 재확인하고 시간 겹침을 검사한다.
에타 시간표 변경은 학교 수강신청이나 수강 취소가 아니다.

직접 추가 일정 예:

```json
{
  "action": "table.add_custom",
  "tableId": "사용자의 시간표 ID",
  "year": "선택한 연도",
  "semester": "2",
  "name": "시험 공부",
  "meetings": [{"dayIndex": 2, "startTime": "18:00", "endTime": "19:00", "room": "도서관"}]
}
```

요일은 월요일 0~일요일 6, 시간은 5분 단위다. 학교 개설 강의의 시간을 임의 수정하지 않는다.
직접 추가 일정의 생성/수정과 시간표 연결은 두 요청이므로 원자적 저장이 아니다. 중간 실패 시
생성된 ID와 성공 단계를 DB에 남기고 자동 재시도하지 않는다.

## 결과와 제한

- `PREPARED`: 미리보기만 저장, 실제 요청 전.
- `SENDING`: DB에서 실행권을 먼저 확보한 상태. 중단되더라도 같은 작업을 다시 전송하지 않는다.
- `APPLIED`: 웹의 응답이 요청을 수락했음. 다음 읽기에서 실제 상태를 확인한다.
- `REJECTED`: 웹이 요청을 거절함.
- `PARTIAL`: 여러 단계 중 일부만 성공함. 성공한 단계/ID를 확인하고 남은 작업만 별도 승인한다.
- `UNKNOWN`: 연결 끊김·응답 형식 변경 등으로 결과 불확실. 같은 요청을 다시 보내지 말고 읽기로 확인한다.

성공/불확실 결과 모두 관련 읽기 캐시를 무효화해 옛 상태가 표시되지 않게 한다.
본인 글·댓글만 수정/삭제하며, 첨부 있는 글 수정은 첨부 보존 검증 전까지 공식 웹을 안내한다.
첨부 업로드, 시간표 공개 범위, 강의평/시험정보 작성, 친구·쪽지·거래·신고·관리자 기능은
이 실행기에 아직 없다. 미확인 요청 주소를 추측해 만들어 보내지 않는다.
