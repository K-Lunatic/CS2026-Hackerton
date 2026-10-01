from __future__ import annotations

from typing import Any, Callable

from providers.tls_provider import TLSProvider
from features.assignments import get_assignments
from features.lectures import get_lectures


def get_current_context(provider: TLSProvider, user_id: str, bookmarks: Callable[[], list[dict[str, Any]]]) -> dict[str, Any]:
    pending = get_assignments(provider, user_id, unsubmitted=True)
    return {
        "user": {"id": user_id, "name": "홍길동", "department": "컴퓨터공학과"},
        "activeCourses": provider.get_courses(user_id),
        "upcomingAssignments": pending,
        "unsubmittedAssignments": pending,
        "unfinishedLectures": get_lectures(provider, user_id, unfinished=True),
        "bookmarks": bookmarks(),
        "activeProjects": [],
        "pendingProjectTasks": [],
    }
