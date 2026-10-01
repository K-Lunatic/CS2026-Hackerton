from __future__ import annotations

from typing import Any, Callable
from datetime import datetime

from providers.tls_provider import TLSProvider
from features.assignments import filter_assignments
from features.lectures import get_lectures
from features.deadlines import SEOUL, deadline, format_deadline


def get_current_context(provider: TLSProvider, user_id: str, bookmarks: Callable[[], list[dict[str, Any]]], *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    assignments = provider.get_assignments(user_id)
    pending = filter_assignments(assignments, unsubmitted=True, now=now)
    upcoming, overdue, undated = [], [], []
    for item in pending:
        due = deadline(item.get("dueAt"))
        if due is None:
            undated.append(item)
        elif due >= now:
            upcoming.append(item)
        else:
            overdue.append(item)
    user = provider.get_user(user_id)
    return {
        "user": user,
        "asOf": now.isoformat(),
        "lastSyncedAt": user.get("lastSyncedAt") if user else None,
        "dataSource": "local",
        "activeCourses": provider.get_courses(user_id),
        "upcomingAssignments": upcoming,
        "overdueAssignments": overdue,
        "undatedAssignments": undated,
        "unknownSubmissionAssignments": [item for item in assignments if item["submissionStatus"] == "UNKNOWN"],
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
    actionable = [(deadline(item.get("dueAt")), item, "과제") for item in data["upcomingAssignments"]]
    for item in data["unfinishedLectures"]:
        due, start = deadline(item.get("availableUntil")), deadline(item.get("availableFrom"))
        available = start is None or (start.date() <= now.date() if len(item.get("availableFrom") or "") == 10 else start <= now)
        if due and due >= now and available:
            actionable.append((due, item, "강의"))
    if actionable:
        _, item, kind = min(actionable, key=lambda entry: entry[0])
        lines.append(f"지금 할 일: {courses.get(item.get('courseId'), '기타 과제')} / {item['title']} — 가장 가까운 마감의 {kind}부터 진행해 보세요.")
    elif data["overdueAssignments"]:
        lines.append("지금 할 일: 기한 지난 과제가 아직 제출 가능한지 TLS에서 확인해 보세요.")
    elif data["undatedAssignments"] or data["unknownSubmissionAssignments"]:
        lines.append("지금 할 일: 마감이나 제출 여부가 확인되지 않은 과제부터 확인해 보세요.")
    elif data["unfinishedLectures"]:
        lines.append("지금 할 일: 미완료 강의의 시청 가능 기간을 확인해 보세요.")
    else:
        lines.append("현재 기록에는 남은 과제·강의가 없어요. 최근 변경 여부가 궁금하면 TLS 새로고침을 요청해 주세요.")
    lines.append("실시간 TLS 조회 결과가 아닙니다. 이후 제출·시청한 내용은 동기화 후 반영됩니다.")
    return "\n".join(lines)
