"""One private SQLite database per chat device."""
from __future__ import annotations

import sqlite3
import re
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA = Path(__file__).resolve().parents[1] / "database" / "schema.sql"


def permission_source_key(resource, course):
    return json.dumps([resource.get(k) for k in ('courseId', 'title', 'fileName', 'remotePath',
        'downloadStatus', 'downloadReason', 'source')] + [course.get('professor'), course.get('semester')],
        ensure_ascii=False, separators=(',', ':'))


class LocalDatabase:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.read_only = read_only
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) if read_only else sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            self.connection.executescript(SCHEMA.read_text(encoding="utf-8"))
            lecture_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(lectures)")}
            for column in ("available_from", "available_until"):
                if column not in lecture_columns:
                    self.connection.execute(f"ALTER TABLE lectures ADD COLUMN {column} TEXT")
            resource_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(resources)")}
            if "download_status" not in resource_columns:
                self.connection.execute("ALTER TABLE resources ADD COLUMN download_status TEXT NOT NULL DEFAULT 'NOT_DOWNLOADED'")
                self.connection.execute("UPDATE resources SET download_status='DOWNLOADED' WHERE local_path IS NOT NULL")
            if "download_reason" not in resource_columns:
                self.connection.execute("ALTER TABLE resources ADD COLUMN download_reason TEXT")
            self.connection.commit()
            columns = self.connection.execute('PRAGMA table_info(assignments)').fetchall()
            if any(row[1] == 'due_at' and row[3] for row in columns):
                self._allow_undated_assignments()

    def _allow_undated_assignments(self):
        """SQLite needs a table copy to remove NOT NULL; preserve rows and dependents."""
        db = self.connection
        sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='assignments'").fetchone()[0]
        create = re.sub(r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?assignments\b', 'CREATE TABLE assignments_nullable', sql, count=1, flags=re.I)
        create = re.sub(r'(\bdue_at\s+TEXT)\s+NOT\s+NULL', r'\1', create, flags=re.I)
        if create == sql or 'assignments_nullable' not in create:
            raise RuntimeError('과제 마감일 저장 형식을 안전하게 변경하지 못했습니다.')
        extras = db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='assignments' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute(create)
                db.execute('INSERT INTO assignments_nullable SELECT * FROM assignments')
                db.execute('DROP TABLE assignments')
                db.execute('ALTER TABLE assignments_nullable RENAME TO assignments')
                for row in extras:
                    db.execute(row[0])
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('과제 저장 형식 변경 시 참조 오류가 있어 변경을 취소했습니다.')
        finally:
            db.execute('PRAGMA foreign_keys=ON')

    def close(self) -> None:
        self.connection.close()

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT id, name, department, updated_at AS lastSyncedAt FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row, department=row["department"] or None) if row else None

    def get_sync_state(self, user_id: str) -> dict[str, str]:
        try:
            rows = self.connection.execute(
                "SELECT state_key, state_value FROM sync_state WHERE user_id=?", (user_id,))
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error).lower():
                raise
            return {}
        return {row["state_key"]: row["state_value"] for row in rows}

    def save_sync_state(self, user_id: str, state: dict[str, str]) -> None:
        if self.read_only:
            raise sqlite3.OperationalError("readonly database")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute("DELETE FROM sync_state WHERE user_id=?", (user_id,))
            self.connection.executemany(
                "INSERT INTO sync_state(user_id, state_key, state_value, updated_at) VALUES (?, ?, ?, ?)",
                [(user_id, key, value, now) for key, value in state.items()])

    def merge_sync_state(self, user_id: str, state: dict[str, str]) -> None:
        """Update one sync feature without erasing another feature's state."""
        merged = self.get_sync_state(user_id)
        merged.update(state)
        self.save_sync_state(user_id, merged)

    def upsert_tls_snapshot(self, user_id: str, courses: list[dict[str, Any]], assignments: list[dict[str, Any]], lectures: list[dict[str, Any]], user_name: str, department: str | None, now: str | None = None, notices: list[dict[str, Any]] | None = None, resources: list[dict[str, Any]] | None = None) -> None:
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
        if notices is not None and course_ids:
            notice_ids = [item["id"] for item in notices]
            course_marks = ",".join("?" for _ in course_ids)
            if notice_ids:
                marks = ",".join("?" for _ in notice_ids)
                db.execute(f"DELETE FROM notices WHERE source='tls' AND course_id IN ({course_marks}) AND id NOT IN ({marks})", [*course_ids, *notice_ids])
            else:
                db.execute(f"DELETE FROM notices WHERE source='tls' AND course_id IN ({course_marks})", course_ids)
        if resources is not None and course_ids:
            resource_ids = [item["id"] for item in resources]
            course_marks = ",".join("?" for _ in course_ids)
            if resource_ids:
                marks = ",".join("?" for _ in resource_ids)
                db.execute(f"DELETE FROM resources WHERE source='tls' AND course_id IN ({course_marks}) AND id NOT IN ({marks})", [*course_ids, *resource_ids])
            else:
                db.execute(f"DELETE FROM resources WHERE source='tls' AND course_id IN ({course_marks})", course_ids)
        for course in courses:
            db.execute("INSERT INTO courses(id, external_id, name, professor, semester, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, professor=excluded.professor, semester=excluded.semester, updated_at=excluded.updated_at", (course["id"], course.get("externalId", course["id"]), course["name"], course.get("professor"), course.get("semester"), course.get("source", "tls"), now))
            db.execute("INSERT OR IGNORE INTO enrollments(user_id, course_id) VALUES (?, ?)", (user_id, course["id"]))
        for item in assignments:
            db.execute("INSERT INTO assignments(id, external_id, course_id, title, description, due_at, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, description=excluded.description, due_at=excluded.due_at, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item["courseId"], item["title"], item.get("description"), item["dueAt"], item.get("source", "tls"), now))
            db.execute("INSERT INTO assignment_submissions(assignment_id, user_id, submission_status, submitted_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(assignment_id, user_id) DO UPDATE SET submission_status=excluded.submission_status, submitted_at=excluded.submitted_at, updated_at=excluded.updated_at", (item["id"], user_id, item.get("submissionStatus", "UNKNOWN"), item.get("submittedAt"), now))
            if item.get("source", "tls") == "tls" and item.get("submissionStatus") in {"SUBMITTED", "LATE"}:
                db.execute("DELETE FROM context_bookmarks WHERE user_id=? AND assignment_id=?", (user_id, item["id"]))
        for item in lectures:
            db.execute("INSERT INTO lectures(id, external_id, course_id, title, duration_seconds, available_from, available_until, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, duration_seconds=excluded.duration_seconds, available_from=excluded.available_from, available_until=excluded.available_until, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item["courseId"], item["title"], item["durationSeconds"], item.get("availableFrom"), item.get("availableUntil"), item.get("source", "tls"), now))
            db.execute("INSERT INTO lecture_progress(lecture_id, user_id, watched_seconds, watch_progress, completed, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(lecture_id, user_id) DO UPDATE SET watched_seconds=excluded.watched_seconds, watch_progress=excluded.watch_progress, completed=excluded.completed, updated_at=excluded.updated_at", (item["id"], user_id, item["watchedSeconds"], item["watchProgress"], int(item["completed"]), now))
        for item in notices or []:
            db.execute("INSERT INTO notices(id, external_id, course_id, title, content, published_at, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, content=excluded.content, published_at=excluded.published_at, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item.get("courseId"), item["title"], item.get("content", ""), item["publishedAt"], item.get("source", "tls"), now))
        for item in resources or []:
            db.execute("INSERT INTO resources(id, external_id, course_id, title, file_name, extension, mime_type, remote_path, local_path, downloaded_at, download_status, download_reason, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, title=excluded.title, file_name=excluded.file_name, extension=excluded.extension, mime_type=excluded.mime_type, remote_path=excluded.remote_path, local_path=excluded.local_path, downloaded_at=excluded.downloaded_at, download_status=excluded.download_status, download_reason=excluded.download_reason, updated_at=excluded.updated_at", (item["id"], item.get("externalId", item["id"]), item["courseId"], item["title"], item["fileName"], item["extension"], item.get("mimeType"), item["remotePath"], item.get("localPath"), item.get("downloadedAt"), item.get("downloadStatus", "DOWNLOADED" if item.get("localPath") else "NOT_DOWNLOADED"), item.get("downloadReason"), item.get("source", "tls"), now))
        db.commit()

    def get_courses(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT c.* FROM courses c JOIN enrollments e ON e.course_id=c.id WHERE e.user_id=? ORDER BY c.name", (user_id,))
        return [dict(row) for row in rows]

    def merge_courses_resources(self, user_id: str, courses: list[dict[str, Any]], resources: list[dict[str, Any]]) -> None:
        """Add pack references without deleting unrelated TLS data."""
        if self.read_only:
            raise sqlite3.OperationalError("readonly database")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT INTO users(id, external_id, name, department, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
            (user_id, user_id, user_id, None, now, now),
        )
        for course in courses:
            self.connection.execute(
                "INSERT INTO courses(id, external_id, name, professor, semester, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, professor=excluded.professor, semester=excluded.semester, updated_at=excluded.updated_at",
                (course["id"], course.get("externalId", course["id"]), course["name"], course.get("professor"), course.get("semester"), course.get("source") or "pack", now),
            )
            self.connection.execute("INSERT OR IGNORE INTO enrollments(user_id, course_id) VALUES (?, ?)", (user_id, course["id"]))
        for resource in resources:
            self.connection.execute(
                "INSERT INTO resources(id, external_id, course_id, title, file_name, extension, mime_type, remote_path, local_path, downloaded_at, download_status, download_reason, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, file_name=excluded.file_name, extension=excluded.extension, mime_type=excluded.mime_type, remote_path=excluded.remote_path, updated_at=excluded.updated_at",
                (resource["id"], resource.get("externalId", resource["id"]), resource["courseId"], resource["title"], resource.get("fileName", resource["title"]), resource.get("extension", ""), resource.get("mimeType"), resource.get("remotePath", ""), None, None, "NOT_DOWNLOADED", resource.get("downloadReason"), resource.get("source") or "pack", now),
            )
        self.connection.commit()

    def get_assignments(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT a.id, a.external_id AS externalId, a.course_id AS courseId, a.title, a.description, a.due_at AS dueAt, s.submission_status AS submissionStatus, s.submitted_at AS submittedAt, a.source FROM assignments a JOIN assignment_submissions s ON s.assignment_id=a.id AND s.user_id=? JOIN enrollments e ON e.course_id=a.course_id AND e.user_id=? ORDER BY a.due_at", (user_id, user_id))
        result = [dict(row) for row in rows]
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_assignments'").fetchone():
            manual = self.connection.execute("SELECT id, course_id AS courseId, title, description, due_at AS dueAt, completed_at AS submittedAt FROM manual_assignments WHERE user_id=?", (user_id,))
            result.extend(dict(row, submissionStatus="SUBMITTED" if row["submittedAt"] else "NOT_SUBMITTED", source="manual") for row in manual)
        return sorted(result, key=lambda item: (item["dueAt"] is None, item["dueAt"] or ""))

    def add_manual_assignment(self, user_id: str, title: str, *, course_id: str | None = None, due_at: str | None = None, description: str | None = None) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("과제 제목이 필요합니다.")
        if due_at:
            try:
                if len(due_at) == 10:
                    date.fromisoformat(due_at)
                elif datetime.fromisoformat(due_at).utcoffset() is None:
                    raise ValueError
            except ValueError:
                raise ValueError("마감일은 YYYY-MM-DD 또는 시간대가 포함된 ISO-8601 날짜·시간이어야 합니다.") from None
        if course_id and not self.connection.execute("SELECT 1 FROM enrollments WHERE user_id=? AND course_id=?", (user_id, course_id)).fetchone():
            raise ValueError("수강 중인 과목 ID를 확인해 주세요.")
        now = datetime.now(timezone.utc).isoformat()
        assignment_id = f"manual-{uuid4().hex}"
        self.connection.execute("INSERT INTO manual_assignments(id, user_id, course_id, title, description, due_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (assignment_id, user_id, course_id, title, description, due_at, now, now))
        self.connection.commit()
        return {"id": assignment_id, "courseId": course_id, "title": title, "description": description, "dueAt": due_at, "submissionStatus": "NOT_SUBMITTED", "submittedAt": None, "source": "manual"}

    def complete_manual_assignment(self, user_id: str, assignment_id: str, *, submission_answer: str) -> bool:
        """Complete a manual assignment only after an exact submission confirmation."""
        if submission_answer != "예":
            return False
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute("UPDATE manual_assignments SET completed_at=?, updated_at=? WHERE id=? AND user_id=?", (now, now, assignment_id, user_id))
        if cursor.rowcount:
            self.connection.execute("DELETE FROM context_bookmarks WHERE user_id=? AND assignment_id=?", (user_id, assignment_id))
        self.connection.commit()
        return cursor.rowcount > 0

    def delete_manual_assignment(self, user_id: str, assignment_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM manual_assignments WHERE id=? AND user_id=?", (assignment_id, user_id))
        if cursor.rowcount:
            self.connection.execute("DELETE FROM context_bookmarks WHERE user_id=? AND assignment_id=?", (user_id, assignment_id))
        self.connection.commit()
        return cursor.rowcount > 0

    def get_lectures(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT l.id, l.external_id AS externalId, l.course_id AS courseId, l.title, l.duration_seconds AS durationSeconds, l.available_from AS availableFrom, l.available_until AS availableUntil, p.watched_seconds AS watchedSeconds, p.watch_progress AS watchProgress, p.completed, l.source FROM lectures l JOIN lecture_progress p ON p.lecture_id=l.id AND p.user_id=? JOIN enrollments e ON e.course_id=l.course_id AND e.user_id=? ORDER BY l.title", (user_id, user_id))
        return [dict(row, completed=bool(row["completed"])) for row in rows]

    def get_todos(self, user_id: str) -> list[dict[str, Any]]:
        courses = {course["id"]: {"courseId": course["id"], "courseName": course["name"], "items": []} for course in self.get_courses(user_id)}
        for item in self.get_assignments(user_id):
            if item["submissionStatus"] in {"NOT_SUBMITTED", "UNKNOWN"}:
                course = courses.get(item["courseId"])
                if course is None:
                    course = courses.setdefault("manual-other", {"courseId": None, "courseName": "기타 과제", "items": []})
                course["items"].append({"type": "ASSIGNMENT", "id": item["id"], "title": item["title"], "dueAt": item["dueAt"], "status": item["submissionStatus"]})
        for item in self.get_lectures(user_id):
            if not item["completed"]:
                courses[item["courseId"]]["items"].append({"type": "LECTURE", "id": item["id"], "title": item["title"], "dueAt": item["availableUntil"], "availableFrom": item["availableFrom"], "watchProgress": item["watchProgress"]})
        for course in courses.values():
            course["items"].sort(key=lambda item: (item["dueAt"] is None, item["dueAt"] or ""))
        return list(courses.values())

    def get_notices(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT n.id, n.external_id AS externalId, n.course_id AS courseId, n.title, n.content, n.published_at AS publishedAt, n.source FROM notices n JOIN enrollments e ON e.course_id=n.course_id AND e.user_id=? ORDER BY n.published_at DESC", (user_id,))
        return [dict(row) for row in rows]

    def get_resources(self, user_id: str, *, include_permissions=True) -> list[dict[str, Any]]:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(resources)")}
        status = "r.download_status" if "download_status" in columns else "CASE WHEN r.local_path IS NOT NULL THEN 'DOWNLOADED' ELSE 'NOT_DOWNLOADED' END"
        reason = "r.download_reason" if "download_reason" in columns else "NULL"
        rows = self.connection.execute(f"SELECT r.id, r.external_id AS externalId, r.course_id AS courseId, r.title, r.file_name AS fileName, r.extension, r.mime_type AS mimeType, r.remote_path AS remotePath, r.local_path AS localPath, r.downloaded_at AS downloadedAt, {status} AS downloadStatus, {reason} AS downloadReason, r.source FROM resources r JOIN enrollments e ON e.course_id=r.course_id AND e.user_id=? ORDER BY r.title", (user_id,))
        resources = [dict(row) for row in rows]
        if include_permissions and self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='permitted_resources'").fetchone():
            approved = {row['resource_id']: row for row in self.connection.execute(
                'SELECT * FROM permitted_resources WHERE user_id=? AND revoked_at IS NULL', (user_id,))}
            if approved:
                courses = {c['id']: c for c in self.get_courses(user_id)}
                for item in resources:
                    row = approved.get(item['id'])
                    if row and row['source_key'] == permission_source_key(item, courses[item['courseId']]):
                        saved = json.loads(row['resource_json'])
                        item.update({k: saved[k] for k in ('fileName', 'extension', 'mimeType', 'localPath', 'downloadedAt')})
                        item.update(restrictionStatus=item['downloadStatus'], restrictionReason=item['downloadReason'],
                            downloadStatus='DOWNLOADED', downloadReason=None, permissionGrantedAt=row['granted_at'])
        return resources

    def save_permitted_resource(self, user_id, original, saved, permission):
        from providers.moodle_provider import validate_permission
        validate_permission(permission, user_id, original['id'])
        current = next((r for r in self.get_resources(user_id, include_permissions=False) if r['id'] == original['id']), None)
        course = next((c for c in self.get_courses(user_id) if c['id'] == original['courseId']), None)
        if (not current or not course or original.get('downloadStatus') != 'PROHIBITED' or
            permission_source_key(current, course) != permission_source_key(original, course) or
            (course.get('professor') and course['professor'].strip() != permission['instructor'].strip()) or
            saved.get('id') != original['id'] or saved.get('downloadStatus') != 'DOWNLOADED'):
            raise ValueError('자료 상태가 달라져 저장하지 않았어요. 선택한 자료를 다시 확인해 주세요.')
        with self.connection:
            self.connection.execute('INSERT INTO permitted_resources VALUES (?,?,?,?,?,?,NULL) '
                'ON CONFLICT(user_id,resource_id) DO UPDATE SET source_key=excluded.source_key, '
                'resource_json=excluded.resource_json, permission_json=excluded.permission_json, '
                'granted_at=excluded.granted_at, revoked_at=NULL',
                (user_id, original['id'], permission_source_key(original, course),
                 json.dumps(saved, ensure_ascii=False), json.dumps(permission, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat()))

    def revoke_resource_permission(self, user_id, resource_id):
        with self.connection:
            self.connection.execute('UPDATE permitted_resources SET revoked_at=? WHERE user_id=? AND resource_id=?',
                (datetime.now(timezone.utc).isoformat(), user_id, resource_id))

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
