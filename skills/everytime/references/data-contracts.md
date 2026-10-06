# 에브리타임 영역별 양식

모든 양식은 인증된 로컬 HTTP 세션에서 브라우저 탭 없이 읽기 작업을 수행하기 위한 요청 계약이다.
`source`는 항상 `everytime`이며, `url`은 실제로 확인한 현재 주소만 기록한다.

## 공통 실행 양식

```json
{
  "area": "board | timetable | classroom | grade_calculator | friends | bookstore | campus_pick",
  "mode": "inspect | collect",
  "maxItems": 20,
  "from": null,
  "to": null,
  "save": false
}
```

- `inspect`: 메뉴와 로그인 상태만 확인한다.
- `collect`: 사용자가 지정한 범위의 원문을 읽는다.
- `maxItems`가 없으면 목록만 확인하고 본문 전체를 순회하지 않는다.
- `save`가 `true`여도 비밀번호·OTP·쿠키·세션 토큰은 저장하지 않는다.

## 1. 게시판

```json
{
  "area": "board",
  "boardName": "정보게시판",
  "boardUrl": "https://everytime.kr/258604",
  "keywords": [],
  "onlyNew": false,
  "includePinned": true,
  "includeContent": true,
  "includeComments": false,
  "from": "2026-10-01",
  "to": "2026-10-06",
  "maxItems": 20
}
```

결과는 `boardName`, `articleId`, `title`, `content`, `publishedAt`, `isPinned`, `matchedKeywords`,
`url`을 사용한다. `articleId`가 같은 글은 페이지가 바뀌어도 한 번만 반환한다. 목록을 여러
페이지 읽더라도 `maxItems`를 넘기지 않는다.
댓글·공감·작성 기능은 별도 명시 없이는 읽지 않는다.

## 2. 시간표

```json
{
  "area": "timetable",
  "semester": "2026-2",
  "timetableName": null,
  "includeCourseCards": true,
  "includeAllTimetables": false,
  "checkConflicts": true
}
```

결과는 `semester`, `timetableName`, `courseId`, `courseName`, `professor`, `credits`,
`day`, `startTime`, `endTime`, `room`, `conflicts`, `url`을 사용한다. 여러 시간표가 있으면
현재 선택된 시간표를 우선하고, `includeAllTimetables`가 `true`일 때만 나머지를 읽는다.
시간표를 수정하거나 수강 신청을 전송하는 기능은 이 읽기 양식에 포함하지 않는다.

실행기의 `--timetable` 결과는 현재 학기의 대표 시간표(없으면 기존 첫 시간표) 하나를 읽는다.
`items`에는 `subjectId`, `courseId`, `classCode`, `courseName`, `professor`, `credits`, `isCustom`,
`isClosed`, `meetings`, `url`을 둔다. `meetings`의 `dayIndex`는 월요일 0부터 일요일 6까지이며,
`day`, `startTime`, `endTime`, `room`을 함께 둔다. 공식 API의 시간 단위는 5분이고 반환 시
`HH:MM`으로 바꾼다. 블록이 없는 과목은 `meetings: []`다.
`courseId`는 내 강의의 분반 코드·과목명·교수가 정확히 일치할 때만 채우며, 시간표의
`subjectId`를 강의실 ID로 사용하지 않는다.

## 3. 강의실

```json
{
  "area": "classroom",
  "courseName": "자료구조",
  "semester": "2026-2",
  "source": "timetable | explicit",
  "includeCourseNotice": true,
  "includeReviews": true,
  "includeExamTips": true,
  "examOnly": true,
  "maxItems": 20
}
```

결과는 `courseName`, `professor`, `examInfo`, `examDate`, `examTime`, `examLocation`,
`examFormat`, `examRange`, `examNotes`, `sourceType`, `publishedAt`, `observedAt`, `url`을
사용한다. `sourceType`은 `OFFICIAL`, `STUDENT_REPORT`, `UNKNOWN` 중 하나다. 강의실에서
찾은 시험정보는 시간표의 `courseId`와 연결하고, 공식 안내와 학생 경험담은 절대 합치지 않는다.
시험 날짜·범위가 보이지 않으면 추측하지 않고 `UNKNOWN`으로 반환한다.

초기 강의평 수집과 `--reviews <courseId>`는 최신 20개를 읽는다. 결과에는 `courseId`,
`courseName`, `professor`, `rate.average`, `rate.count`, `items`를 둔다. 각 항목은 `reviewId`,
`year`, `semester`, `text`, `rate`, `posvote`, `sourceType: STUDENT_REPORT`, `url`이다.
저장한 표본이 전체 강의평 수보다 적으면 `partial: true`다.

`statistics`는 서비스가 집계한 과제·조모임·성적·출결·시험 선택지와 응답 수를 보존한다.
`--purpose study|enrollment` 결과는 `topics`, 원문 `items`, `matchedTerms`, `sampleCount`,
`sourceHash`, `sourceObservedAt`를 추가한다. 이것은 근거 색인이지 자동 긍정/부정 판정이 아니다.
원문 갱신 시 내용 해시가 바뀌면 근거 색인도 갱신한다.

## 4. 학점 계산기

```json
{
  "area": "grade_calculator",
  "calculatorName": null,
  "readCurrentValues": true,
  "includeSubjects": true
}
```

결과는 `calculatorName`, `subjects`, `totalCredits`, `gpa`, `scale`, `url`을 사용한다.
계산 결과를 바꾸거나 과목을 추가·삭제하지 않는다. 사용자가 수정안을 요청할 때는 별도의
미리보기 결과로만 제안한다.

## 5. 친구

```json
{
  "area": "friends",
  "target": "profile | friend-list | group-list",
  "includeMessages": false
}
```

친구·프로필·그룹 목록은 사용자가 대상을 지정한 경우에만 읽는다. 쪽지와 그룹 채팅 내용은
기본적으로 수집하지 않는다. 결과는 `target`, `displayName`, `groupName`, `url` 정도로
최소화하고 연락처·인증정보는 저장하지 않는다.

## 6. 책방

```json
{
  "area": "bookstore",
  "query": "자료구조",
  "condition": null,
  "maxItems": 20
}
```

결과는 `title`, `author`, `price`, `condition`, `sellerDisplayName`, `publishedAt`, `url`을
사용한다. 판매자에게 연락하거나 거래·예약·결제를 진행하지 않는다.

## 7. 캠퍼스 픽

```json
{
  "area": "campus_pick",
  "category": "학사일정",
  "campus": "건국대 GLOCAL캠",
  "from": "2026-10-01",
  "to": "2026-10-31",
  "maxItems": 20
}
```

결과는 `category`, `title`, `content`, `publishedAt`, `campus`, `url`을 사용한다. 알림
구독이나 외부 공유는 별도 동의 없이는 실행하지 않는다.

## 결과 봉투

```json
{
  "source": "everytime",
  "area": "board",
  "fetchedAt": "2026-10-06T00:00:00+09:00",
  "items": [],
  "partial": false,
  "message": null
}
```

페이지 구조 변경·로그인 만료·접근 제한으로 일부만 확인했으면 `partial: true`와 원인을
기록한다. 확인하지 못한 내용을 빈 배열로 성공 처리하지 않는다.
