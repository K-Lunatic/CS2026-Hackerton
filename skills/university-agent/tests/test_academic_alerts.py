"""Priority cards stay local, useful, and link back to the real TLS activity."""
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.academic_alerts import collect_alerts, fingerprint, format_alerts
from scripts import academic_watch
from storage.local_db import LocalDatabase


class AcademicAlertTests(unittest.TestCase):
    def test_unfinished_work_is_sorted_and_linked(self):
        with tempfile.TemporaryDirectory() as folder:
            db = LocalDatabase(Path(folder) / "school.db")
            db.upsert_tls_snapshot(
                "student",
                [{"id": "course-1", "name": "자료구조", "source": "tls"}],
                [{"id": "assignment-1", "externalId": "12", "courseId": "course-1", "title": "배열 과제",
                  "dueAt": "2026-10-06T23:59:00+09:00", "submissionStatus": "NOT_SUBMITTED", "source": "tls"}],
                [{"id": "lecture-1", "externalId": "34", "courseId": "course-1", "title": "트리 영상",
                  "durationSeconds": 600, "watchedSeconds": 60, "watchProgress": 10, "completed": False,
                  "availableUntil": "2026-10-10T23:59:00+09:00", "source": "tls"}],
                "student",
                None,
            )
            data = collect_alerts(db, "student", now=datetime.fromisoformat("2026-10-07T12:00:00+09:00"))
            self.assertEqual([item["kind"] for item in data["alerts"]], ["assignment", "lecture"])
            self.assertIn("/mod/assign/view.php?id=12", data["alerts"][0]["url"])
            self.assertIn("/mod/vod/view.php?id=34", data["alerts"][1]["url"])
            self.assertIn("자료구조", format_alerts({**data, "newNotices": []}))
            self.assertTrue(fingerprint(data))
            db.close()

    def test_merge_sync_state_preserves_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            db = LocalDatabase(Path(folder) / "school.db")
            db.upsert_tls_snapshot("student", [{"id": "course-1", "name": "자료구조", "source": "tls"}], [], [], "student", None)
            db.save_sync_state("student", {"course_ids": "[\"course-1\"]", "course_fingerprints": "{}"})
            db.merge_sync_state("student", {"watch_checked_at": "now"})
            self.assertEqual(db.get_sync_state("student")["course_ids"], "[\"course-1\"]")
            self.assertEqual(db.get_sync_state("student")["watch_checked_at"], "now")
            db.close()

    def test_watch_report_updates_local_state_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "school.db"
            db = LocalDatabase(db_path)
            db.upsert_tls_snapshot(
                "student",
                [{"id": "course-1", "name": "자료구조", "source": "tls"}],
                [{"id": "assignment-1", "externalId": "12", "courseId": "course-1", "title": "배열 과제",
                  "dueAt": "2026-10-06T23:59:00+09:00", "submissionStatus": "NOT_SUBMITTED", "source": "tls"}],
                [], "student", None,
            )
            db.close()
            with patch.dict(os.environ, {"UNIVERSITY_AGENT_DB": str(db_path), "UNIVERSITY_AGENT_USER_ID": "student"}):
                self.assertEqual(academic_watch.run_once(sync=False, send_notification=False), 0)
            with closing(LocalDatabase(db_path, read_only=True)) as saved:
                self.assertIn("watch_alert_fingerprint", saved.get_sync_state("student"))


if __name__ == "__main__":
    unittest.main()
