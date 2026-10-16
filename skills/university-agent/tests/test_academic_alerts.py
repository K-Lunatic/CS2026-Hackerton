"""Priority cards stay local, useful, and link back to the real TLS activity."""
from datetime import datetime, timezone
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.academic_alerts import collect_alerts, fingerprint, format_alerts, format_daily_report, resource_fingerprint
from scripts import academic_watch
from storage.local_db import LocalDatabase


class AcademicAlertTests(unittest.TestCase):
    def test_daily_report_groups_all_items_and_keeps_unknown_and_completed_truthful(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(LocalDatabase(Path(folder) / 'school.db')) as db:
                assignments = [{'id': f'a{i}', 'courseId': 'c', 'title': f'과제{i}', 'source': 'tls',
                    'submissionStatus': status, 'dueAt': due}
                    for i, (status, due) in enumerate([
                        ('NOT_SUBMITTED', '2026-10-12T15:00:00Z'),
                        ('UNKNOWN', '2026-10-10'), ('SUBMITTED', '2026-10-11'),
                        ('NOT_SUBMITTED', None), ('SUBMITTED', '2026-11-01')])]
                lectures = [{'id': f'l{i}', 'courseId': 'c', 'title': f'강의{i}', 'source': 'tls',
                    'completed': completed, 'watchProgress': progress, 'durationSeconds': 100,
                    'watchedSeconds': progress, 'availableUntil': '2026-10-13T14:00:00Z'}
                    for i, (completed, progress) in enumerate([(True, 20), (False, 100)])]
                db.upsert_tls_snapshot('student', [{'id': 'c', 'name': '자료구조', 'source': 'tls'}],
                                       assignments, lectures, 'student', None)
                text = format_daily_report(db, 'student', '2026-10-12T01:00:00Z',
                    now=datetime.fromisoformat('2026-10-12T12:00:00+09:00'))
            self.assertIn('2026-10-12 10:00 (Asia/Seoul)', text)
            self.assertIn('2026-10-13 00:00', text)
            self.assertIn('[제출 상태 확인 필요, 이미 마감] 과제1', text)
            self.assertIn('[이미 마감] 과제2', text)
            self.assertNotIn('과제4', text)
            self.assertNotIn('강의0', text)
            self.assertIn('강의1', text)
            self.assertIn('시청: 미완료 · 시청률 100%', text)

    def test_daily_report_requires_this_runs_successful_fresh_stamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'school.db'
            with closing(LocalDatabase(path)) as db:
                db.upsert_tls_snapshot('student', [{'id': 'c', 'name': '자료구조'}],
                    [{'id': 'a', 'courseId': 'c', 'title': '과거 기록', 'dueAt': None, 'source': 'tls', 'submissionStatus': 'NOT_SUBMITTED'}],
                    [], 'student', None)
                db.merge_sync_state('student', {'academic_checked_at': '2026-01-01T00:00:00Z'})
            with patch.object(academic_watch, 'paths', return_value=(path, 'student')):
                for sync_result in ((False, '동기화 실패'), (True, '')):
                    out = io.StringIO()
                    with patch.object(academic_watch, 'run_sync', return_value=sync_result), contextlib.redirect_stdout(out):
                        self.assertEqual(academic_watch.run_once(daily_report=True, send_notification=False), 1)
                    self.assertNotIn('과거 기록', out.getvalue())
                def sync(**kwargs):
                    self.assertTrue(kwargs['academic_only'])
                    with closing(LocalDatabase(path)) as db:
                        db.merge_sync_state('student', {'academic_checked_at': datetime.now(timezone.utc).isoformat()})
                    return True, ''
                out = io.StringIO()
                with patch.object(academic_watch, 'run_sync', side_effect=sync), contextlib.redirect_stdout(out):
                    self.assertEqual(academic_watch.run_once(daily_report=True, send_notification=False), 0)
                self.assertIn('과거 기록', out.getvalue())
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(academic_watch.run_once(daily_report=True, sync=False), 1)

    def test_sync_timeout_is_bounded_and_never_uses_ensure_for_status(self):
        with patch.object(academic_watch.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sync', 120)) as run:
            ok, message = academic_watch.run_sync(academic_only=True)
            self.assertFalse(ok)
            self.assertIn('2분', message)
            self.assertEqual(run.call_args.kwargs['timeout'], 120)
            self.assertIn('--academic-only', run.call_args.args[0])
            self.assertNotIn('--ensure', run.call_args.args[0])
        with patch.object(academic_watch.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(academic_watch.run_sync()[0])
            self.assertIn('--ensure', run.call_args.args[0])

        with patch.object(academic_watch.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='MoodleSession=secret\n학교 연결 실패')):
            ok, message = academic_watch.run_sync()
            self.assertFalse(ok)
            self.assertIn('학교 연결 실패', message)
            self.assertNotIn('secret', message)

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

    def test_resource_changes_are_reported_without_exposing_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(LocalDatabase(Path(folder) / "school.db")) as db:
                db.upsert_tls_snapshot(
                    "student", [{"id": "course-1", "name": "자료구조", "source": "tls"}], [], [], "student", None,
                    resources=[{"id": "resource-1", "courseId": "course-1", "title": "새 자료", "fileName": "week.pdf", "extension": "pdf", "remotePath": "/private/path", "source": "tls"}],
                )
                data = collect_alerts(db, "student")
            current = resource_fingerprint(data)
            data["resourceChanges"] = data["resources"]
            text = format_alerts(data, changed=False)
            self.assertIn("새 자료·자료 업데이트", text)
            self.assertNotIn("/private/path", text)
            self.assertTrue(current)

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
