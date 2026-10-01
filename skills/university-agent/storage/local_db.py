"""One private SQLite database per chat device."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from providers.tls_provider import MockTLSProvider

SCHEMA = Path(__file__).resolve().parents[1] / "database" / "schema.sql"


class LocalDatabase:
    def __init__(self, path: Path, *, seed_mock: bool = True):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        lecture_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(lectures)")}
        for column in ("available_from", "available_until"):
            if column not in lecture_columns:
                self.connection.execute(f"ALTER TABLE lectures ADD COLUMN {column} TEXT")
        if seed_mock:
            self.seed_mock_if_empty()

    def close(self) -> None:
        self.connection.close()

    def seed_mock_if_empty(self) -> None:
        if self.connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        provider = MockTLSProvider()
        now = datetime.now(timezone.utc).isoformat()
        self.upsert_tls_snapshot("user-hong", provider.get_courses("user-hong"), provider.get_assignments("user-hong"), provider.get_lectures("user-hong"), "홍길동", "컴퓨터공학과", now)

    def upsert_tls_snapshot(self, user_id: str, courses: list[dict[str, Any]], assignments: list[dict[str, Any]], lectures: list[dict[str, Any]], user_name: str, department: str, now: str | None = None, notices: list[dict[str, Any]] | None = None, resources: list[dict[str, Any]] | None = None) -> None:
        now = now or datetime.now(timezone.utc).isoformat()
        db = self.connection
        db.execute("INSERT INTO users(id, external_id, name, department, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, department=excluded.department, updated_at=excluded.updated_at", (user_id, user_id, user_name, department, now, now))
        course_ids = [course["id"] for course in courses]
        if course_ids:
            marks = ",".join("?" for _ in course_ids)
            db.execute(f"DELETE FROM enrollments WHERE user_id=? AND course_id NOT IN ({marks})", [user_id, *course_ids])
        else:
            db.execute("DELETE FROM enrollments WHERE user_id=?", (user_id,))
        assignment_ids = [item["id"] for item in assignments]
        if assignment_ids:
            marks = ",".join("?" for _ in assignment_ids)
            db.execute(f"DELETE FROM assignment_submissions WHERE user_id=? AND assignment_id NOT IN ({marks})", [user_id, *assignment_ids])
        else:
            db.execute("DELETE FROM assignment_submissions WHERE user_id=?", (user_id,))
        lecture_ids = [item["id"] for item in lectures]
        if lecture_ids:
            marks = ",".join("?" for _ in lecture_ids)
            db.execute(f"DELETE FROM lecture_progress WHERE user_id=? AND lecture_id NOT IN ({marks})", [user_id, *lecture_ids])
        else:
            db.execute("DELETE FROM lecture_progress WHERE user_id=?", (user_id,))
        if notices is not None:
            notice_ids = [item["id"] for item in notices]
            if notice_ids:
                marks = ",".join("?" for _ in notice_ids)
                db.execute(f"DELETE FROM notices WHERE source='tls' AND id NOT IN ({marks})", notice_ids)
            else:
                db.execute("DELETE FROM notices WHERE source='tls'")
        if resources is not None:
            resource_ids = [item["id"] for item in resources]
            if resource_ids:
                marks = ",".join("?" for _ in resource_ids)
                db.execute(f"DELETE FROM resources WHERE source='tls' AND id NOT IN ({marks})", resource_ids)
            else:
                db.execute("DELETE FROM resources WHERE source='tls'")
        for course in courses:
            db.execute("INSERT INTO courses(id, external_id, name, professor, semester, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, professor=excluded.professor, semester=excluded.semester, updated_at=excluded.updated_at", (course["id"], course.get("externalId", course["id"]), course["name"], course.get("professor"), course.get("semester"), course.get("source", "tls"), now))
            db.execute("INSERT OR IGNORE INTO enrollments(user_id, course_id) VALUES (?, ?)", (user_id, course["id"]))
        for item in assignments:
            db.execute("INSERT INTO assignments(id, external_id, course_id, title, description, due_at, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, description=excluded.description, due_at=excluded.due_at, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item["courseId"], item["title"], item.get("description"), item["dueAt"], item.get("source", "tls"), now))
            db.execute("INSERT INTO assignment_submissions(assignment_id, user_id, submission_status, submitted_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(assignment_id, user_id) DO UPDATE SET submission_status=excluded.submission_status, submitted_at=excluded.submitted_at, updated_at=excluded.updated_at", (item["id"], user_id, item.get("submissionStatus", "UNKNOWN"), item.get("submittedAt"), now))
        for item in lectures:
            db.execute("INSERT INTO lectures(id, external_id, course_id, title, duration_seconds, available_from, available_until, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, duration_seconds=excluded.duration_seconds, available_from=excluded.available_from, available_until=excluded.available_until, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item["courseId"], item["title"], item["durationSeconds"], item.get("availableFrom"), item.get("availableUntil"), item.get("source", "tls"), now))
            db.execute("INSERT INTO lecture_progress(lecture_id, user_id, watched_seconds, watch_progress, completed, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(lecture_id, user_id) DO UPDATE SET watched_seconds=excluded.watched_seconds, watch_progress=excluded.watch_progress, completed=excluded.completed, updated_at=excluded.updated_at", (item["id"], user_id, item["watchedSeconds"], item["watchProgress"], int(item["completed"]), now))
        for item in notices or []:
            db.execute("INSERT INTO notices(id, external_id, course_id, title, content, published_at, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, content=excluded.content, published_at=excluded.published_at, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item.get("courseId"), item["title"], item.get("content", ""), item["publishedAt"], item.get("source", "tls"), now))
        for item in resources or []:
            db.execute("INSERT INTO resources(id, external_id, course_id, title, file_name, extension, mime_type, remote_path, local_path, downloaded_at, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, file_name=excluded.file_name, extension=excluded.extension, mime_type=excluded.mime_type, remote_path=excluded.remote_path, local_path=excluded.local_path, downloaded_at=excluded.downloaded_at, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item["courseId"], item["title"], item["fileName"], item["extension"], item.get("mimeType"), item["remotePath"], item.get("localPath"), item.get("downloadedAt"), item.get("source", "tls"), now))
        db.commit()

    def get_courses(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT c.* FROM courses c JOIN enrollments e ON e.course_id=c.id WHERE e.user_id=? ORDER BY c.name", (user_id,))
        return [dict(row) for row in rows]

    def get_assignments(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT a.id, a.course_id AS courseId, a.title, a.description, a.due_at AS dueAt, s.submission_status AS submissionStatus, s.submitted_at AS submittedAt, a.source FROM assignments a JOIN assignment_submissions s ON s.assignment_id=a.id AND s.user_id=? JOIN enrollments e ON e.course_id=a.course_id AND e.user_id=? ORDER BY a.due_at", (user_id, user_id))
        return [dict(row) for row in rows]

    def get_lectures(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT l.id, l.course_id AS courseId, l.title, l.duration_seconds AS durationSeconds, l.available_from AS availableFrom, l.available_until AS availableUntil, p.watched_seconds AS watchedSeconds, p.watch_progress AS watchProgress, p.completed, l.source FROM lectures l JOIN lecture_progress p ON p.lecture_id=l.id AND p.user_id=? JOIN enrollments e ON e.course_id=l.course_id AND e.user_id=? ORDER BY l.title", (user_id, user_id))
        return [dict(row, completed=bool(row["completed"])) for row in rows]

    def get_todos(self, user_id: str) -> list[dict[str, Any]]:
        courses = {course["id"]: {"courseId": course["id"], "courseName": course["name"], "items": []} for course in self.get_courses(user_id)}
        for item in self.get_assignments(user_id):
            if item["submissionStatus"] != "SUBMITTED":
                courses[item["courseId"]]["items"].append({"type": "ASSIGNMENT", "id": item["id"], "title": item["title"], "dueAt": item["dueAt"], "status": item["submissionStatus"]})
        for item in self.get_lectures(user_id):
            if not item["completed"]:
                courses[item["courseId"]]["items"].append({"type": "LECTURE", "id": item["id"], "title": item["title"], "dueAt": item["availableUntil"], "availableFrom": item["availableFrom"], "watchProgress": item["watchProgress"]})
        for course in courses.values():
            course["items"].sort(key=lambda item: (item["dueAt"] is None, item["dueAt"] or ""))
        return list(courses.values())

    def get_notices(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT n.id, n.course_id AS courseId, n.title, n.content, n.published_at AS publishedAt, n.source FROM notices n JOIN enrollments e ON e.course_id=n.course_id AND e.user_id=? ORDER BY n.published_at DESC", (user_id,))
        return [dict(row) for row in rows]

    def get_resources(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT r.id, r.course_id AS courseId, r.title, r.file_name AS fileName, r.extension, r.mime_type AS mimeType, r.remote_path AS remotePath, r.local_path AS localPath, r.downloaded_at AS downloadedAt, r.source FROM resources r JOIN enrollments e ON e.course_id=r.course_id AND e.user_id=? ORDER BY r.title", (user_id,))
        return [dict(row) for row in rows]

    def list_bookmarks(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id, user_id AS userId, target_type AS targetType, target_id AS targetId, note, created_at AS createdAt FROM bookmarks WHERE user_id=? ORDER BY created_at", (user_id,))
        return [dict(row) for row in rows]

    def add_bookmark(self, user_id: str, target_type: str, target_id: str, note: str) -> dict[str, Any]:
        item = {"id": f"bookmark-{uuid4().hex}", "userId": user_id, "targetType": target_type, "targetId": target_id, "note": note, "createdAt": datetime.now(timezone.utc).isoformat()}
        self.connection.execute("INSERT INTO bookmarks(id, user_id, target_type, target_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?)", (item["id"], user_id, target_type, target_id, note, item["createdAt"]))
        self.connection.commit()
        return item

    def delete_bookmark(self, user_id: str, target_id: str) -> dict[str, Any]:
        cursor = self.connection.execute("DELETE FROM bookmarks WHERE user_id=? AND target_id=?", (user_id, target_id))
        self.connection.commit()
        return {"deleted": cursor.rowcount, "targetId": target_id}
