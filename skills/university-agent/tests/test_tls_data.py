"""Keep video deadlines and course todos grounded in TLS source data."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from providers.moodle_provider import _vod_availability
from storage.local_db import LocalDatabase


class TLSDataTests(unittest.TestCase):
    def test_video_period_and_course_todos(self):
        html = '<li class="activity vod modtype_vod" id="module-42"><span>2026-10-01 09:00:00 ~ 2026-10-08 14:00:00</span></li>'
        start, end = _vod_availability(html)["42"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "university.db"
            legacy = sqlite3.connect(path)
            legacy.execute("CREATE TABLE lectures (id TEXT PRIMARY KEY, external_id TEXT, course_id TEXT, title TEXT, duration_seconds INTEGER, source TEXT, updated_at TEXT)")
            legacy.close()
            db = LocalDatabase(path)
            db.upsert_tls_snapshot(
                "student", [{"id": "course-1", "name": "Course 1"}],
                [{"id": "task-1", "courseId": "course-1", "title": "Assignment", "dueAt": end, "submissionStatus": "NOT_SUBMITTED"}],
                [{"id": "video-42", "courseId": "course-1", "title": "Video", "durationSeconds": 60, "watchedSeconds": 0, "watchProgress": 0, "completed": False, "availableFrom": start, "availableUntil": end}],
                "Student", "",
            )
            lecture = db.get_lectures("student")[0]
            self.assertEqual((lecture["availableFrom"], lecture["availableUntil"]), (start, end))
            self.assertEqual([item["type"] for item in db.get_todos("student")[0]["items"]], ["ASSIGNMENT", "LECTURE"])
            db.close()


if __name__ == "__main__":
    unittest.main()
