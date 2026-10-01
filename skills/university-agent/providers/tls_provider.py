"""TLS boundary: one owner supplies data, feature modules consume this contract."""
from __future__ import annotations

from typing import Any, Protocol


class TLSProvider(Protocol):
    def get_courses(self, user_id: str) -> list[dict[str, Any]]: ...
    def get_assignments(self, user_id: str) -> list[dict[str, Any]]: ...
    def get_lectures(self, user_id: str) -> list[dict[str, Any]]: ...


class MockTLSProvider:
    """Stable fixture so every teammate can build without real TLS credentials."""

    def get_courses(self, _user_id: str) -> list[dict[str, Any]]:
        return [
            {"id": "course-network", "name": "컴퓨터네트워크", "professor": "김교수", "semester": "2026-2"},
            {"id": "course-java2", "name": "Java2", "professor": "이교수", "semester": "2026-2"},
            {"id": "course-db", "name": "데이터베이스", "professor": "박교수", "semester": "2026-2"},
        ]

    def get_assignments(self, _user_id: str) -> list[dict[str, Any]]:
        return [
            {"id": "assignment-network-5", "courseId": "course-network", "title": "컴퓨터네트워크 과제 5", "dueAt": "2026-10-02T23:59:00+09:00", "submissionStatus": "NOT_SUBMITTED", "source": "mock-tls"},
            {"id": "assignment-java2-6", "courseId": "course-java2", "title": "Java2 과제 6", "dueAt": "2026-10-05T23:59:00+09:00", "submissionStatus": "NOT_SUBMITTED", "source": "mock-tls"},
            {"id": "assignment-db-4", "courseId": "course-db", "title": "데이터베이스 과제 4", "dueAt": "2026-09-28T23:59:00+09:00", "submissionStatus": "SUBMITTED", "submittedAt": "2026-09-27T20:00:00+09:00", "source": "mock-tls"},
        ]

    def get_lectures(self, _user_id: str) -> list[dict[str, Any]]:
        return [
            {"id": "lecture-java2-6", "courseId": "course-java2", "title": "Java2 6주차 강의", "durationSeconds": 3120, "watchedSeconds": 1859, "watchProgress": 59.6, "completed": False, "source": "mock-tls"},
            {"id": "lecture-network-6", "courseId": "course-network", "title": "컴퓨터네트워크 6주차 강의", "durationSeconds": 2700, "watchedSeconds": 2700, "watchProgress": 100, "completed": True, "source": "mock-tls"},
        ]
