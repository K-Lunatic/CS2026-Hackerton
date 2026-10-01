# Shared data model draft

이 문서는 팀원이 공통 데이터를 사용할 때 지켜야 하는 1차 계약이다. `database/schema.sql`은 각 채팅 기기에 생성되는 로컬 SQLite DB의 스키마다.

## 기기 로컬 저장

각 기기에 Skill을 설치하면 기본적으로 다음 DB가 생성된다.

```text
~/.university-agent/university.db
```

기기마다 데이터가 분리되며 네트워크 동기화나 공유 폴더를 사용하지 않는다. 다른 경로가 필요하면 `UNIVERSITY_AGENT_DB`를 지정한다. TLS 담당자는 `ingest_tls.py`로 정규화한 TLS 데이터를 현재 기기의 DB에 넣는다.

## 소유권

| 영역 | 데이터 | 담당 |
| --- | --- | --- |
| TLS 동기화 | `users`, `courses`, `enrollments`, `assignments`, `lectures`, `notices`, `resources` | TLS 담당자 |
| 사용자별 학업 상태 | `assignment_submissions`, `lecture_progress` | TLS 담당자 + 기능 담당자 소비 |
| 서비스 기능 | `bookmarks`, `context_bookmarks`, `projects`, `project_members`, `project_tasks`, `handovers`, `handover_items` | 기능 담당자 |
| 조회 조합 | `StudentContext` | 저장하지 않고 조회 시 생성 |

## 핵심 결정

1. 과제와 강의는 여러 사용자가 공유하므로 본체와 사용자별 상태를 분리한다.
2. TLS 외부 ID는 `external_id`에 보존하고 `(source, external_id)`를 유일하게 유지한다. 재동기화는 insert가 아니라 upsert 대상이다.
3. DB 시간은 ISO-8601 UTC 문자열로 저장하고 화면에서 Asia/Seoul로 변환한다.
4. 외부 TLS에 없는 앱 데이터는 TLS 테이블에 섞지 않는다.
5. `StudentContext`는 중복 캐시 테이블로 만들지 않는다. 과제·강의·북마크·프로젝트 조회를 조합한다.
6. 이 DB는 의도적으로 기기별 단일 저장소다. 기기 간 공유와 충돌 해결은 제공하지 않는다.
7. 과제 Context Bookmark는 사용자가 보낸 정형 명령에서만 읽거나 쓴다. 저장은 진행 이력을 append-only로 남긴다.

## 기능팀이 받는 형태

TLS 담당자는 DB를 직접 노출하지 않고 `TLSProvider`가 아래처럼 사용자별 상태가 합쳐진 레코드를 반환한다.

```json
{
  "id": "assignment-network-5",
  "courseId": "course-network",
  "title": "컴퓨터네트워크 과제 5",
  "dueAt": "2026-10-02T23:59:00+09:00",
  "submissionStatus": "NOT_SUBMITTED",
  "submittedAt": null,
  "source": "tls"
}
```

즉 기능팀은 `assignment_submissions` 조인 방식이나 TLS API를 알 필요가 없다. DB 스키마가 바뀌어도 이 반환 계약을 유지한다.

## 1차 기능 범위

- 과제: 전체, 미제출, 임박한 과제
- 강의: 전체, 미시청, 진행률
- 공지: 과목별 최신 공지
- 자료: 과목 PPT/PDF 메타데이터와 기기 로컬 다운로드 경로
- 북마크: 사용자별 대상 북마크
- 과제 컨텍스트 북마크: 사용자별 과제 진행 체크포인트와 복귀 카드
- 프로젝트: 멤버, 업무, 상태
- 인수인계: 완료·미완료·파일·환경·다음 액션 항목

성적, 알림 발송, 벡터 검색, 장기 메모리는 이 스키마에 넣지 않는다.
