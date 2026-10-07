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
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import sync_tls
from providers.moodle_provider import MoodleTLSProvider, _download_restriction, permitted_download_url
from providers.moodle_session import MoodleSession, DownloadRestricted, _DownloadRedirectHandler
from urllib.request import Request
from storage.local_db import LocalDatabase
from features.study_materials import _read_sections
from features.study_materials import original_files, study_materials
from features.guidance import academic_list_answer
import download_permitted
from test_project import FakeTLSSession


class SyncSafetyTests(unittest.TestCase):
    def test_preflight_fingerprint_reuses_unchanged_activity_lists(self):
        session = FakeTLSSession()
        provider = MoodleTLSProvider(session)
        first = provider.preflight('u')
        state = {
            'course_ids': json.dumps(first['courseIds']),
            'course_fingerprints': json.dumps(first['fingerprints']),
        }
        self.assertFalse(sync_tls._preflight_changed(state, first, needs_data=False))
        changed = dict(first, fingerprints={**first['fingerprints'], 'tls-course-1': 'changed'})
        self.assertTrue(sync_tls._preflight_changed(state, changed, needs_data=False))
        self.assertTrue(sync_tls._preflight_changed(state, first, needs_data=True))

    def permitted_fixture(self):
        session = FakeTLSSession()
        session.base_url = 'https://tls.kku.ac.kr'
        viewer = 'https://tls.kku.ac.kr/local/ubdoc/?id=12345&tp=m&pg=ubfile'
        session.get_page = lambda url, **kwargs: ('<iframe src="' + viewer + '"></iframe>', url)
        headers = Message()
        headers['Content-Type'] = 'application/pdf'
        headers.add_header('Content-Disposition', 'attachment', filename=('utf-8', '', '보충자료.pdf'))
        def get_bytes(url, **kwargs):
            session.byte_requests.append(url)
            return b'%PDF-permitted-fixture', SimpleNamespace(headers=headers, geturl=lambda: url)
        session.get_bytes = get_bytes
        provider = MoodleTLSProvider(session)
        original = provider.get_resources('u', download_new_files=False)[1]
        self.assertEqual(original['id'], 'tls-resource-6')
        self.assertEqual(session.byte_requests, [])
        permission = {'userId': 'u', 'resourceId': original['id'], 'instructor': '담당 교수',
                      'statement': '교수님께 보충자료.pdf를 내려받아 공부해도 된다는 허락을 받았습니다.', 'userConfirmed': True}
        return session, provider, original, permission, viewer

    def test_permission_is_specific_and_never_implicit(self):
        session, provider, original, permission, viewer = self.permitted_fixture()
        for change in ({'userId': 'other'}, {'resourceId': 'tls-resource-7'}, {'userConfirmed': False},
                       {'userConfirmed': 'true'}, {'statement': ''}, {'instructor': ''}, {'extra': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                provider.download_permitted_resource('u', original, {**permission, **change})
        self.assertEqual(session.byte_requests, [])
        provider._courses[0]['professor'] = '다른 교수'
        with self.assertRaises(ValueError):
            provider.download_permitted_resource('u', original, permission)
        provider._courses[0].pop('professor')
        saved = provider.download_permitted_resource('u', original, permission)
        self.assertEqual(session.byte_requests, ['https://tls.kku.ac.kr/local/ubdoc/download.php?id=12345&tp=m&pg=ubfile'])
        self.assertEqual(saved['fileName'], '보충자료.pdf')
        self.assertEqual(original['downloadStatus'], 'PROHIBITED')

    def test_observed_viewer_only_and_strict_url_scope(self):
        session, provider, original, permission, viewer = self.permitted_fixture()
        for url in ('https://other.invalid/local/ubdoc/?id=12345&tp=m&pg=ubfile',
                    viewer.replace('https:', 'http:'), viewer.replace('tls.kku.ac.kr', 'tls.kku.ac.kr:444'),
                    viewer + '&nodownload=1', viewer + '&id=2', viewer.replace('tp=m', 'tp=x'),
                    viewer.replace('id=12345', 'id=6'), viewer.replace('id=12345', 'id=../6')):
            with self.subTest(url=url), self.assertRaises((ValueError, DownloadRestricted)):
                provider.download_permitted_resource('u', original, permission, url)
        for html in ('<script>let url="' + viewer + '";</script>',
                     '<div hidden><br><a href="' + viewer + '">hidden</a></div>',
                     '<input type="hidden" value="' + viewer + '">'):
            session.get_page = lambda url, **kwargs: (html, url)
            with self.assertRaises(ValueError):
                provider.download_permitted_resource('u', original, permission)
        self.assertEqual(session.byte_requests, [])
        with self.assertRaises((ValueError, DownloadRestricted)):
            permitted_download_url(viewer + '#other')

    def test_server_rejection_and_nonfile_never_save(self):
        session, provider, original, permission, viewer = self.permitted_fixture()
        blocked = {**original, 'downloadReason': 'TLS 서버가 파일 다운로드를 거부했습니다.'}
        with self.assertRaises(DownloadRestricted):
            provider.download_permitted_resource('u', blocked, permission)
        self.assertEqual(session.byte_requests, [])
        def forbidden(*args, **kwargs):
            raise HTTPError(viewer, 403, 'Forbidden', Message(), io.BytesIO())
        session.get_bytes = forbidden
        with self.assertRaises(HTTPError):
            provider.download_permitted_resource('u', original, permission)
        for content, mime in ((b'<html><form>login</form></html>', 'application/pdf'),
                              (b'not a pdf', 'application/pdf'), (b'', 'application/pdf')):
            headers = Message()
            headers['Content-Type'] = mime
            session.get_bytes = lambda *args, **kwargs: (content, SimpleNamespace(headers=headers))
            with self.assertRaises(DownloadRestricted):
                provider.download_permitted_resource('u', original, permission)

    def test_permission_copy_is_user_local_revocable_and_not_sync_input(self):
        session, provider, original, permission, viewer = self.permitted_fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.db'
            db = LocalDatabase(path)
            self.addCleanup(db.close)
            course = provider.get_courses('u')[0]
            for user in ('u', 'other'):
                db.upsert_tls_snapshot(user, [course], [], [], user, None, resources=[original])
            saved = sync_tls.store_resource(path.parent / 'files', provider.download_permitted_resource('u', original, permission))
            db.save_permitted_resource('u', original, saved, permission)
            allowed = db.get_resources('u')[0]
            self.assertEqual(allowed['downloadStatus'], 'DOWNLOADED')
            self.assertEqual(allowed['restrictionStatus'], 'PROHIBITED')
            self.assertEqual(db.get_resources('other')[0]['downloadStatus'], 'PROHIBITED')
            self.assertEqual(db.get_resources('u', include_permissions=False)[0]['downloadStatus'], 'PROHIBITED')
            self.assertEqual(sync_tls._cached_resources(path, 'u', path.parent / 'files'), {})
            result = original_files([course], [allowed], files_root=path.parent / 'files')
            self.assertEqual(Path(result['data']['files'][0]['path']).read_bytes(), b'%PDF-permitted-fixture')
            with patch('features.study_materials._read_sections', return_value=[{'location': 'PDF p.1', 'text': '연습 본문'}]):
                result = study_materials([course], [allowed], files_root=path.parent / 'files')
                self.assertTrue(result['data']['materials'][0]['sections'])
            db.upsert_tls_snapshot('u', [course], [], [], 'u', None, resources=[original])
            self.assertEqual(db.get_resources('u')[0]['localPath'], saved['localPath'])
            for changed in ({'title': '다른 자료'}, {'remotePath': '/mod/resource/view.php?id=7'},
                            {'downloadReason': 'TLS 서버가 파일 다운로드를 거부했습니다.'}):
                db.upsert_tls_snapshot('u', [course], [], [], 'u', None, resources=[{**original, **changed}])
                self.assertEqual(db.get_resources('u')[0]['downloadStatus'], 'PROHIBITED')
            db.upsert_tls_snapshot('u', [{**course, 'semester': '다음 학기'}], [], [], 'u', None, resources=[original])
            self.assertEqual(db.get_resources('u')[0]['downloadStatus'], 'PROHIBITED')
            db.upsert_tls_snapshot('u', [course], [], [], 'u', None, resources=[original])
            db.revoke_resource_permission('u', original['id'])
            with patch('features.study_materials._read_sections', side_effect=AssertionError('must not read')):
                result = study_materials([course], db.get_resources('u'), files_root=path.parent / 'files')
            self.assertFalse(result['data']['materials'][0]['sections'])
            self.assertTrue(Path(saved['localPath']).is_file())
            self.assertIsNotNone(db.connection.execute('SELECT revoked_at FROM permitted_resources').fetchone()[0])

    def test_school_only_redirect_is_rejected_before_following(self):
        request = Request('https://tls.kku.ac.kr/local/ubdoc/download.php?id=1')
        request.allowed_origin = 'tls.kku.ac.kr'
        stream = io.BytesIO()
        with self.assertRaises(DownloadRestricted):
            _DownloadRedirectHandler().redirect_request(request, stream, 302, 'Found', {}, 'https://other.invalid/private.pdf')
        self.assertTrue(stream.closed)
        forwarded = _DownloadRedirectHandler().redirect_request(request, io.BytesIO(), 302, 'Found', {}, 'https://tls.kku.ac.kr/file.pdf')
        self.assertEqual(forwarded.allowed_origin, 'tls.kku.ac.kr')
        with self.assertRaises(DownloadRestricted):
            _DownloadRedirectHandler().redirect_request(forwarded, io.BytesIO(), 302, 'Found', {}, 'https://other.invalid/file.pdf')
        session = MoodleSession()
        session.logged_in = True
        with patch.object(session.opener, 'open') as opener:
            with self.assertRaises(DownloadRestricted):
                session.get_bytes('https://other.invalid/file.pdf', allowed_origin='tls.kku.ac.kr')
        opener.assert_not_called()

    def test_permission_input_fails_before_secret_store(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'permission.json'
            source.write_text('[]', encoding='utf-8')
            with patch.object(sys, 'argv', ['download_permitted.py', '--resource-id', 'r', '--permission-file', str(source)]), \
                 patch.object(download_permitted, 'load') as load:
                with self.assertRaises(SystemExit):
                    download_permitted.main()
            load.assert_not_called()
            session, provider, original, permission, _ = self.permitted_fixture()
            path = Path(directory) / 'academic.db'
            with contextlib.closing(LocalDatabase(path)) as database:
                database.upsert_tls_snapshot('u', provider.get_courses('u'), [], [], 'u', None, resources=[original])
            source.write_text(json.dumps(permission), encoding='utf-8')
            session.login = lambda *args: None
            output = io.StringIO()
            with patch.object(sys, 'argv', ['download_permitted.py', '--resource-id', original['id'], '--permission-file', str(source)]), \
                 patch.dict(os.environ, {'UNIVERSITY_AGENT_DB': str(path), 'UNIVERSITY_AGENT_USER_ID': 'u'}), \
                 patch.object(download_permitted, 'load', return_value=('u', 'fixture-secret')), \
                 patch.object(download_permitted, 'MoodleSession', return_value=session), contextlib.redirect_stdout(output):
                download_permitted.main()
            self.assertNotIn('fixture-secret', output.getvalue())
            self.assertNotIn(permission['statement'], output.getvalue())
            with contextlib.closing(LocalDatabase(path, read_only=True)) as database:
                self.assertEqual(database.get_resources('u')[0]['downloadStatus'], 'DOWNLOADED')
            with patch.object(sys, 'argv', ['download_permitted.py', '--resource-id', original['id'], '--revoke']), \
                 patch.dict(os.environ, {'UNIVERSITY_AGENT_DB': str(path), 'UNIVERSITY_AGENT_USER_ID': 'u'}), \
                 patch.object(download_permitted, 'load') as load, contextlib.redirect_stdout(io.StringIO()):
                download_permitted.main()
            load.assert_not_called()
            with contextlib.closing(LocalDatabase(path, read_only=True)) as database:
                self.assertEqual(database.get_resources('u')[0]['downloadStatus'], 'PROHIBITED')

    def test_progress_and_public_restriction_do_not_expose_internals(self):
        session = FakeTLSSession()
        messages = []
        provider = MoodleTLSProvider(session, messages.append)
        provider.get_assignments('u')
        provider.get_lectures('u')
        provider.get_notices('u')
        self.assertGreaterEqual(len(messages), 4)
        self.assertTrue(all('테스트 과목' in m for m in messages))
        class Local:
            def get_courses(self, user): return [{'id': 'c', 'name': '과목'}]
            def get_user(self, user): return {}
        answer = academic_list_answer(Local(), 'u', 'resources', [{'courseId': 'c', 'title': '자료',
            'downloadStatus': 'PROHIBITED', 'downloadReason': 'SQLITE_READONLY https://secret.invalid?id=33'}])
        for term in ('SQLITE', 'https://', '허락', '승인', 'download.php', '캐시', 'JSON'):
            self.assertNotIn(term, answer)

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
