"""Offline regressions for file identity, restricted downloads and partial sync."""
import contextlib
from email.message import Message
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import sync_tls
from providers.moodle_provider import MoodleTLSProvider, _download_restriction
from providers.moodle_session import MoodleSession
from storage.local_db import LocalDatabase
from features.study_materials import _read_sections
from test_project import FakeTLSSession


class SyncSafetyTests(unittest.TestCase):
    def test_old_due_date_schema_migrates_without_cascading_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.db'
            schema = (ROOT / 'database/schema.sql').read_text().replace('due_at TEXT,', 'due_at TEXT NOT NULL,', 1)
            with sqlite3.connect(path) as db:
                db.executescript(schema)
                db.execute("INSERT INTO users VALUES ('u','u','user',NULL,'now','now')")
                db.execute("INSERT INTO courses VALUES ('c','c','course',NULL,NULL,'tls','now')")
                db.execute("INSERT INTO enrollments VALUES ('u','c','STUDENT')")
                db.execute("INSERT INTO assignments VALUES ('a','a','c','task',NULL,'2026-10-10','tls','now')")
                db.execute("INSERT INTO assignment_submissions VALUES ('a','u','NOT_SUBMITTED',NULL,'now')")
                db.execute('CREATE INDEX custom_assignment_title ON assignments(title)')
            db = LocalDatabase(path)
            self.assertEqual(len(db.get_assignments('u')), 1)
            self.assertEqual(db.connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertIsNotNone(db.connection.execute("SELECT 1 FROM sqlite_master WHERE name='custom_assignment_title'").fetchone())
            db.connection.execute('UPDATE assignments SET due_at=NULL')
            db.connection.commit()
            self.assertIsNone(db.get_assignments('u')[0]['dueAt'])
            db.close()
    def test_names_versions_and_failed_publish_preserve_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'files'
            def item(identifier, content):
                return {'id': identifier, 'courseId': 'course', 'fileName': '강의.pdf', 'extension': 'pdf', '_content': content}
            first = sync_tls.store_resource(root, item('r1', b'one'))
            second = sync_tls.store_resource(root, item('r2', b'two'))
            updated = sync_tls.store_resource(root, item('r1', b'new'))
            self.assertEqual(len({x['localPath'] for x in (first, second, updated)}), 3)
            self.assertTrue(all(Path(x['localPath']).name == '강의.pdf' for x in (first, second, updated)))
            self.assertEqual(Path(first['localPath']).read_bytes(), b'one')
            with patch('sync_tls.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    sync_tls.store_resource(root, item('r1', b'one'))
            self.assertEqual(Path(first['localPath']).read_bytes(), b'one')

    def test_mixed_permission_and_undated_assignment(self):
        for text in ('PDF 다운로드 금지. TXT 다운로드 가능.', 'PDF 다운로드 금지 및 TXT 다운로드 가능'):
            self.assertIsNotNone(_download_restriction(text, '자료', []))
        for text in ('다운로드 가능합니다.', '다운로드 금지가 아닙니다.', '다운로드 제한 해제되었습니다.'):
            self.assertIsNone(_download_restriction(text, '자료', []))
        session = FakeTLSSession()
        session.pages['/mod/assign/view.php?id=2'] = '<p>제출 안 함</p>'
        assignment = MoodleTLSProvider(session).get_assignments('u')[0]
        self.assertIsNone(assignment['dueAt'])

    def test_ubfile_403_skipped_and_next_file_survives(self):
        session = FakeTLSSession()
        session.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/ubfile/view.php?id=9">금지.pdf</a></li><li class="activity"><a href="/mod/resource/view.php?id=5">허용.pdf</a></li>'
        original = session.get
        def get(path):
            if 'ubfile' in path:
                raise HTTPError(path, 403, 'Forbidden', Message(), None)
            return original(path)
        session.get = get
        saved = []
        def sink(item):
            saved.append(item.pop('_content'))
            return item
        result = MoodleTLSProvider(session).get_resources('u', save_file=sink)
        self.assertEqual(result[0]['downloadStatus'], 'PROHIBITED')
        self.assertEqual(len(saved), 1)
        self.assertTrue(all('_content' not in r for r in result))

    def test_readonly_special_path_and_korean_encodings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '강의 #1.db'
            database = LocalDatabase(path)
            database.close()
            database = LocalDatabase(path, read_only=True)
            self.assertEqual(Path(database.connection.execute('PRAGMA database_list').fetchone()[2]), path.resolve())
            with self.assertRaises(sqlite3.OperationalError):
                database.connection.execute('CREATE TABLE forbidden(x)')
            database.close()
            for encoding in ('cp949', 'utf-8-sig', 'utf-16'):
                for extension in ('txt', 'java'):
                    source = Path(directory) / ('자료-' + encoding + '.' + extension)
                    source.write_bytes('한글 주석\n과제 안내'.encode(encoding))
                    sections = _read_sections(source, extension, Path(directory) / 'cache')
                    self.assertEqual([s['text'] for s in sections], ['한글 주석', '과제 안내'])

    def test_conditional_request_and_cookie_workers(self):
        session = MoodleSession('https://fixture.invalid')
        session.logged_in = True
        child = session.fork()
        self.assertIsNot(child.cookies, session.cookies)
        self.assertIsNot(child.opener, session.opener)
        headers = Message()
        headers['ETag'] = 'v1'
        def unchanged(request, **kwargs):
            self.assertEqual(request.get_header('If-none-match'), 'v1')
            raise HTTPError(request.full_url, 304, 'Not modified', headers, io.BytesIO())
        with patch.object(session.opener, 'open', side_effect=unchanged):
            content, response = session.revalidate('/file.pdf', {'etag': 'v1'})
        self.assertIsNone(content)
        self.assertEqual(response.code, 304)
        fake = FakeTLSSession()
        fake.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/resource/view.php?id=5">자료.pdf</a></li>'
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'old.pdf'
            source.write_bytes(b'old')
            called = []
            def changed(path, cached):
                called.append(path)
                return fake.get_bytes(path)
            fake.revalidate = changed
            item = MoodleTLSProvider(fake).get_resources('u', existing_resources={'5': {
                'localPath': str(source), 'remotePath': '/mod/resource/view.php?id=5'}})[0]
            self.assertEqual(len(called), 1)
            self.assertNotEqual(item['_content'], b'old')
            fake.revalidate = lambda path, cached: (None, response)
            reused = MoodleTLSProvider(fake).get_resources('u', existing_resources={'5': {
                'localPath': str(source), 'remotePath': '/mod/resource/view.php?id=5'}},
                save_file=lambda item: self.fail('unchanged file must not be written again'))[0]
            self.assertTrue(reused['_reused'])
            self.assertEqual(reused['localPath'], str(source))

    def test_parallel_course_reads_are_bounded(self):
        counters = {'active': 0, 'peak': 0}
        lock = threading.Lock()
        class Session:
            def fork(self): return Session()
            def get(self, path):
                if path == '/local/ubion/user/':
                    return ''.join(f'<a href="/course/view.php?id={i}">과목{i}</a>' for i in range(6))
                if path.startswith('/course/'):
                    number = path.rsplit('=', 1)[1]
                    return f'<li class="activity"><a href="/mod/assign/view.php?id={number}">과제</a></li>'
                with lock:
                    counters['active'] += 1
                    counters['peak'] = max(counters['peak'], counters['active'])
                time.sleep(0.02)
                with lock:
                    counters['active'] -= 1
                return '<p>제출 안 함</p>'
        result = MoodleTLSProvider(Session()).get_assignments('u')
        self.assertEqual(len(result), 6)
        self.assertTrue(1 < counters['peak'] <= 4)

    def test_completed_stages_survive_later_network_failure(self):
        class Provider:
            def __init__(self, session): pass
            def get_courses(self, user): return [{'id': 'c', 'name': '과목'}]
            def get_assignments(self, user): return [{'id': 'a', 'courseId': 'c', 'title': '과제', 'dueAt': None}]
            def get_lectures(self, user): raise URLError('temporary failure')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            with patch.object(sys, 'argv', ['sync_tls.py']), patch.dict(os.environ, {'UNIVERSITY_AGENT_DB': str(path), 'UNIVERSITY_AGENT_USER_ID': 'u'}), \
                 patch.object(sync_tls, 'resolve', return_value=('u', 'fixture')), patch.object(sync_tls, 'save'), \
                 patch.object(MoodleSession, 'login'), patch.object(sync_tls, 'MoodleTLSProvider', Provider), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit): sync_tls.main()
            database = LocalDatabase(path, read_only=True)
            self.assertEqual(database.get_assignments('u')[0]['title'], '과제')
            database.close()
