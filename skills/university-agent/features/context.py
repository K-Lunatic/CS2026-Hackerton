from __future__ import annotations

from typing import Any, Callable
from datetime import datetime

from providers.tls_provider import TLSProvider
from features.assignments import get_assignments
from features.lectures import get_lectures
from features.deadlines import SEOUL, deadline, format_deadline


def get_current_context(provider: TLSProvider, user_id: str, bookmarks: Callable[[], list[dict[str, Any]]], *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    pending = get_assignments(provider, user_id, unsubmitted=True)
    user = provider.get_user(user_id)
    return {
        "user": user,
        "asOf": now.isoformat(),
        "lastSyncedAt": user.get("lastSyncedAt") if user else None,
        "dataSource": "local",
        "activeCourses": provider.get_courses(user_id),
        "upcomingAssignments": [item for item in pending if (due := deadline(item.get("dueAt"))) is not None and due >= now],
        "overdueAssignments": [item for item in pending if (due := deadline(item.get("dueAt"))) is not None and due < now],
        "undatedAssignments": [item for item in pending if deadline(item.get("dueAt")) is None],
        "unknownSubmissionAssignments": [item for item in provider.get_assignments(user_id) if item["submissionStatus"] == "UNKNOWN"],
        "unsubmittedAssignments": pending,
        "unfinishedLectures": get_lectures(provider, user_id, unfinished=True),
        "bookmarks": bookmarks(),
    }


def format_current_context(data: dict[str, Any]) -> str:
    now = datetime.fromisoformat(data["asOf"])
    lines = [f"저장된 학사 데이터 기준 · 조회 {now:%Y-%m-%d %H:%M} (한국 시간)",
             f"마지막 TLS 동기화: {format_deadline(data.get('lastSyncedAt')) if data.get('lastSyncedAt') else '확인 불가'}",
             f"기한 지난 미제출 과제 {len(data['overdueAssignments'])}개 · 예정 {len(data['upcomingAssignments'])}개 · 마감 확인 필요 {len(data['undatedAssignments'])}개",
             f"제출 상태 확인 필요 {len(data['unknownSubmissionAssignments'])}개 · 미완료 강의 {len(data['unfinishedLectures'])}개"]
    courses = {item["id"]: item["name"] for item in data["activeCourses"]}
    for label, items in (("기한 지남", data["overdueAssignments"]), ("다음 마감", data["upcomingAssignments"][:1])):
        for item in items:
            lines.append(f"{label}: {courses.get(item.get('courseId'), '기타 과제')} / {item['title']} — {format_deadline(item.get('dueAt'))}")
    for item in data["unfinishedLectures"]:
        due = deadline(item.get("availableUntil"))
        if due is not None and due.date() <= now.date():
            state = "기한 지남" if due < now else "오늘 마감"
            lines.append(f"{state}: {courses.get(item.get('courseId'), '과목 확인 필요')} / {item['title']} — {format_deadline(item.get('availableUntil'))} · 시청률 {item['watchProgress']:g}% · 미완료")
    lines.append("실시간 TLS 조회 결과가 아닙니다. 이후 제출·시청한 내용은 동기화 후 반영됩니다.")
    return "\n".join(lines)
