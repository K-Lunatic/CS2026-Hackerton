# Shared data model draft

이 문서는 팀원이 공통 데이터를 사용할 때 지켜야 하는 1차 계약이다. `database/schema.sql`은 논리 모델의 SQLite 초안이고, 현재 실행 방식은 별도 서버 없이 사용자 소유 동기화 폴더의 JSON 파일이다.

## 서버 없는 저장 방식

각 기기에 Skill을 설치하고 같은 동기화 폴더를 지정한다.

```bash
export UNIVERSITY_AGENT_DATA_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/university-agent"
export UNIVERSITY_AGENT_USER_ID="user-hong"
```

실제 경로는 iCloud Drive, Dropbox, OneDrive, Syncthing 등 사용자가 신뢰하는 동기화 도구로 정한다.

```text
<sync-root>/
└── users/
    └── <user-id>/
        ├── tls_snapshot.json
        └── app_state.json
```

`user-id`가 개인 공간 경계다. 서로 다른 사용자는 같은 동기화 루트에 있어도 서로의 파일을 읽지 않는다. TLS 담당자는 `sync_tls_snapshot.py`로 `tls_snapshot.json`을 원자적으로 교체하고, 기능은 그 파일을 읽는다.

## 소유권

| 영역 | 데이터 | 담당 |
| --- | --- | --- |
| TLS 동기화 | `users`, `courses`, `enrollments`, `assignments`, `lectures`, `notices` | TLS 담당자 |
| 사용자별 학업 상태 | `assignment_submissions`, `lecture_progress` | TLS 담당자 + 기능 담당자 소비 |
| 서비스 기능 | `bookmarks`, `projects`, `project_members`, `project_tasks`, `handovers`, `handover_items` | 기능 담당자 |
| 조회 조합 | `StudentContext` | 저장하지 않고 조회 시 생성 |

## 핵심 결정

1. 과제와 강의는 여러 사용자가 공유하므로 본체와 사용자별 상태를 분리한다.
2. TLS 외부 ID는 `external_id`에 보존하고 `(source, external_id)`를 유일하게 유지한다. 재동기화는 insert가 아니라 upsert 대상이다.
3. DB 시간은 ISO-8601 UTC 문자열로 저장하고 화면에서 Asia/Seoul로 변환한다.
4. 외부 TLS에 없는 앱 데이터는 TLS 테이블에 섞지 않는다.
5. `StudentContext`는 중복 캐시 테이블로 만들지 않는다. 과제·강의·북마크·프로젝트 조회를 조합한다.
6. 동기화 폴더는 서버가 아니다. 같은 사용자의 여러 기기에서 순차적으로 사용하는 것을 기본으로 하며, 두 기기에서 동시에 같은 파일을 수정하는 충돌 해결은 아직 제공하지 않는다.

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
- 북마크: 사용자별 대상 북마크
- 프로젝트: 멤버, 업무, 상태
- 인수인계: 완료·미완료·파일·환경·다음 액션 항목

성적, 알림 발송, 벡터 검색, 장기 메모리는 이 스키마에 넣지 않는다.
