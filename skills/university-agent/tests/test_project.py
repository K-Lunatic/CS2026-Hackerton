"""Project-wide offline integration tests. Never access real accounts or Keychain."""
from contextlib import redirect_stdout, redirect_stderr
from email.message import Message
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from storage.local_db import LocalDatabase
from sync_tls import _cached_resources
from providers import credentials
from providers.forms import collect_local, FormUnavailable, redact, requested_schema, TLS_CREDENTIALS_FORM
from providers.moodle_session import MoodleSession, LoginError, DownloadRestricted, check_download_url, _DownloadRedirectHandler
from providers.moodle_provider import MoodleTLSProvider, _plain_text
from features.context import get_current_context
from features.context_commands import parse_context_command


def snapshot(suffix='1'):
    return {
        'courses': [{'id': 'course-' + suffix, 'name': '테스트 과목', 'source': 'tls'}],
        'assignments': [{'id': 'assignment-' + suffix, 'courseId': 'course-' + suffix, 'title': '테스트 과제', 'dueAt': '2026-10-05T23:59:00+09:00', 'submissionStatus': 'NOT_SUBMITTED', 'source': 'tls'}],
        'lectures': [{'id': 'lecture-' + suffix, 'courseId': 'course-' + suffix, 'title': '테스트 강의', 'durationSeconds': 100, 'watchedSeconds': 50, 'watchProgress': 50, 'completed': False, 'source': 'tls'}],
        'notices': [{'id': 'notice-' + suffix, 'courseId': 'course-' + suffix, 'title': '테스트 공지', 'publishedAt': '2026-10-01T12:00:00+09:00', 'source': 'tls'}],
        'resources': [{'id': 'resource-' + suffix, 'courseId': 'course-' + suffix, 'title': '테스트 자료', 'fileName': 'test.pdf', 'extension': 'pdf', 'remotePath': '/test.pdf', 'source': 'tls'}],
    }


def upsert(db, user, data):
    db.upsert_tls_snapshot(user, data['courses'], data['assignments'], data['lectures'], user, '테스트학과', notices=data['notices'], resources=data['resources'])


class FakeTLSSession:
    """Small explicit HTML fixtures; not fetched from actual TLS."""
    def __init__(self):
        self.pages = {
            '/local/ubion/user/': '<a href="/course/view.php?id=1">테스트 과목</a>',
            '/course/view.php?id=1': ''.join(f'<li class="activity"><a href="/mod/{kind}/view.php?id={id}">{title}</a></li>' for kind, id, title in [('assign', 2, '테스트 과제'), ('vod', 3, '테스트 강의'), ('ubboard', 10, '공지사항'), ('resource', 5, '테스트 자료')]) + '<li class="activity"><a href="/mod/resource/view.php?id=6">보충자료.pdf</a><span>다운로드 금지</span></li><li class="activity"><a href="/mod/resource/view.php?id=7">2주차 보충자료.pdf</a></li><li class="activity"><a href="/mod/resource/view.php?id=8">서버제한.pdf</a></li><li class="activity"><a href="/mod/ubfile/view.php?id=9">뷰어 자료</a></li>',
            '/mod/assign/view.php?id=2': '<p>종료 일시: 2026-10-05 23:59</p><p>제출 완료</p>',
            '/mod/vod/viewer.php?id=3': '<span class="playtime">10:00</span><script>var is_progress = 50; var is_complete = 0;</script>',
            '/mod/ubboard/view.php?id=10': '<a href="/mod/ubboard/article.php?id=10&amp;bwid=11">공지</a>',
            '/mod/ubboard/article.php?id=10&bwid=11': '<div class="content">강의실 메뉴</div><div class="subject"><h3>테스트 공지</h3></div><div class="content"><div class="text_to_html"><p>실제 공지 내용</p></div></div><p>작성일: 2026-10-01 12:00</p>',
            '/mod/ubfile/view.php?id=9': '<a href="/mod/ubfile/viewer.php?id=9">open</a>',
        }
        self.byte_requests = []
        self.requests = []
    def get(self, path):
        self.requests.append(path)
        return self.pages[path]
    def get_bytes(self, path):
        self.byte_requests.append(path)
        if path.endswith('id=8'):
            from urllib.error import HTTPError
            raise HTTPError(path, 403, 'Forbidden', None, BytesIO(b'blocked'))
        if path.endswith('id=9'):
            headers = Message(); headers['Content-Type'] = 'text/html'
            return b'<html><body><a href="/mod/ubfile/viewer.php?id=9">open</a></body></html>', SimpleNamespace(headers=headers, geturl=lambda: 'https://fixture.invalid/mod/ubfile/view.php?id=9')
        headers = Message(); headers['Content-Type'] = 'application/pdf'
        return b'%PDF-test-fixture', SimpleNamespace(headers=headers, geturl=lambda: 'https://fixture.invalid/test.pdf')


class ProjectTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / 'test.db'
        self.env = dict(os.environ, UNIVERSITY_AGENT_DB=str(self.db_path), UNIVERSITY_AGENT_USER_ID='fixture-user')

    def seed(self):
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data['assignments'].append(dict(data['assignments'][0], id='assignment-2'))
        upsert(db, 'fixture-user', data)
        db.close()

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/run_agent.py'), *args], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

class ProjectTests(ProjectTestBase):
    def test_sync_state_round_trip(self):
        db = LocalDatabase(self.db_path)
        self.addCleanup(db.close)
        upsert(db, 'fixture-user', snapshot())
        db.save_sync_state('fixture-user', {'course_ids': '["course-1"]', 'course_fingerprints': '{"course-1":"x"}'})
        self.assertEqual(db.get_sync_state('fixture-user')['course_fingerprints'], '{"course-1":"x"}')
        readonly = LocalDatabase(self.db_path, read_only=True)
        self.addCleanup(readonly.close)
        self.assertEqual(readonly.get_sync_state('fixture-user')['course_ids'], '["course-1"]')

    def test_legacy_migration_releases_write_lock(self):
        schema = (ROOT / 'database/schema.sql').read_text(encoding='utf-8')
        legacy_schema = '\n'.join(line for line in schema.splitlines()
                                  if 'download_status' not in line and 'download_reason' not in line)
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(legacy_schema)
        db = LocalDatabase(self.db_path)
        self.addCleanup(db.close)
        self.assertFalse(db.connection.in_transaction)
        with sqlite3.connect(self.db_path, timeout=0.1) as connection:
            connection.execute('CREATE TABLE migration_lock_probe (id INTEGER)')

    def test_new_database_has_no_invented_data(self):
        db = LocalDatabase(self.db_path)
        self.addCleanup(db.close)
        self.assertIsNone(db.get_user('fixture-user'))
        self.assertEqual(db.get_courses('fixture-user'), [])
        self.assertIsNone(get_current_context(db, 'fixture-user', lambda: [])['user'])

    def test_runner_requires_real_sync(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/run_agent.py'), 'assignments'], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('동기화', result.stderr)
        self.assertEqual(self.cli('ask', '--text', '과제 불러오기')['data'], None)
        self.assertEqual(self.cli('ask', '--text', '과제 목록 불러오기')['data'], [])
        self.assertFalse(self.db_path.exists())

    def test_read_commands_open_database_without_schema_writes(self):
        self.seed()
        connection = sqlite3.connect(self.db_path)
        connection.execute('DROP TABLE manual_assignments')
        connection.commit()
        connection.close()
        db = LocalDatabase(self.db_path, read_only=True)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.OperationalError):
            db.connection.execute('CREATE TABLE should_not_exist (id INTEGER)')
        self.assertEqual(len(self.cli('context')['data']['activeCourses']), 1)
        self.assertEqual(len(self.cli('assignments')['data']), 2)

    def test_all_read_commands_and_json(self):
        self.seed()
        for command in ('context', 'assignments', 'lectures', 'bookmarks', 'notices', 'resources'):
            with self.subTest(command=command):
                data = self.cli(command)
                self.assertIn('toolCalls', data); self.assertIn('data', data)
        self.assertEqual(len(self.cli('assignments', '--unsubmitted')['data']), 2)
        self.assertEqual(len(self.cli('lectures', '--unfinished')['data']), 1)
        self.assertEqual(self.cli('ask', '--text', '미제출 과제 알려줘')['toolCalls'], ['get_unsubmitted_assignments'])
        self.assertEqual(self.cli('ask', '--text', '미시청 강의 보여줘')['toolCalls'], ['get_unwatched_lectures'])
        self.assertEqual(self.cli('ask', '--text', '공지 알려줘')['toolCalls'], ['get_notices'])
        self.assertEqual(self.cli('ask', '--text', 'PDF 자료 보여줘')['toolCalls'], ['get_resources'])

    def test_manual_assignment_lifecycle_and_tls_resync(self):
        self.seed()
        added = self.cli('assignment-add', '--title', '개인 독서 과제', '--due-at', '2026-10-10')['data']
        course_item = self.cli('assignment-add', '--title', '추가 실습', '--course-id', 'course-1', '--description', '강의실 밖에서 받은 과제')['data']
        self.assertEqual(added['source'], 'manual')
        self.assertIsNone(added['courseId'])
        self.assertIn(added['id'], [item['id'] for item in self.cli('assignments', '--unsubmitted')['data']])
        todos = self.cli('todos')['data']
        self.assertIn(added['id'], [item['id'] for group in todos for item in group['items']])
        self.assertIn(course_item['id'], [item['id'] for group in todos if group['courseId'] == 'course-1' for item in group['items']])
        self.assertEqual(self.cli('ask', '--text', '개인 과제 등록해줘')['needsInput'], True)
        db = LocalDatabase(self.db_path); self.addCleanup(db.close)
        upsert(db, 'fixture-user', snapshot('1'))
        upsert(db, 'other-user', snapshot('2'))
        self.assertEqual([item for item in db.get_assignments('other-user') if item['source'] == 'manual'], [])
        self.assertIn(added['id'], [item['id'] for item in db.get_assignments('fixture-user')])
        self.assertFalse(db.complete_manual_assignment('other-user', added['id'], submission_answer='예'))
        self.assertEqual(self.cli('assignment-complete', '--id', added['id'], '--submission-answer', '예')['data']['completed'], True)
        self.assertNotIn(added['id'], [item['id'] for item in self.cli('assignments', '--unsubmitted')['data']])
        self.assertEqual(self.cli('assignment-delete', '--id', course_item['id'])['data']['deleted'], True)
        self.assertFalse(self.cli('assignment-complete', '--id', 'assignment-1', '--submission-answer', '예')['data']['completed'])

    def test_manual_assignment_rejects_invalid_input_without_writing(self):
        self.seed()
        for options in (('--title', '  '), ('--title', '과제', '--course-id', 'wrong'), ('--title', '과제', '--due-at', '2026-99-99'), ('--title', '과제', '--due-at', '2026-10-10T12:00:00')):
            with self.subTest(options=options):
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/run_agent.py'), 'assignment-add', *options], env=self.env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual([item for item in self.cli('assignments')['data'] if item['source'] == 'manual'], [])

    def test_bookmark_persistence_and_delete(self):
        self.seed()
        created = self.cli('bookmark-add', '--target-type', 'ASSIGNMENT', '--target-id', 'assignment-1', '--note', '테스트 메모')['data']
        self.assertEqual(self.cli('bookmarks')['data'][0]['id'], created['id'])
        self.assertEqual(self.cli('bookmark-delete', '--target-id', 'assignment-1')['data']['deleted'], 1)
        self.assertEqual(self.cli('bookmarks')['data'], [])

    def test_checkpoint_gates_persistence_and_history(self):
        gated = self.cli('ask', '--text', '지금까지 진행 상황 저장해줘')
        self.assertFalse(gated['data']['performed'])
        self.assertFalse(self.db_path.exists())
        self.seed()
        payload = dict(progress='자료 2개 정리', blocker='없음', nextAction='초안 작성', completedItems=['자료 수집'])
        for progress in ('자료 2개 정리', '초안 완성'):
            payload['progress'] = progress
            result = self.cli('ask', '--text', '과제 저장 --과제ID assignment-1', '--checkpoint-json', json.dumps(payload))
            self.assertEqual(result['data']['progress'], progress)
        self.assertEqual(self.cli('ask', '--text', '과제 불러오기')['data']['progress'], '초안 완성')
        malformed = self.cli('ask', '--text', '과제 저장 --과제ID assignment-1', '--checkpoint-json', '{}')
        self.assertFalse(malformed['data']['performed'])
        unknown = self.cli('ask', '--text', '과제 저장 --과제ID missing', '--checkpoint-json', json.dumps(payload))
        self.assertFalse(unknown['data']['performed'])
        db = LocalDatabase(self.db_path)
        self.addCleanup(db.close)
        self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM context_bookmarks').fetchone()[0], 2)
        self.assertEqual(db.connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(db.connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_conversation_bookmark_requests_prompt_without_database(self):
        for text, operation in (
            ('지금 이 대화를 북마크로 저장해줘', 'save'),
            ('이 대화 북마크해줘', 'save'),
            ('채팅을 즐겨찾기에 추가해줘', 'save'),
            ('대화 요약 저장해줘', 'save'),
            ('bookmark this conversation', 'save'),
            ('save this chat', 'save'),
            ('저장된 대화 북마크 보여줘', 'load'),
            ('이 대화 저장하고 불러와줘', 'ambiguous'),
        ):
            with self.subTest(text=text):
                result = self.cli('ask', '--text', text)
                self.assertEqual(result['toolCalls'], ['prompt_context_command'])
                self.assertEqual(result['data']['operation'], operation)
                self.assertFalse(result['data']['performed'])
                if operation == 'save':
                    self.assertTrue(result['needsAssignmentQuery'])
                self.assertFalse(self.db_path.exists())

    def test_custom_bookmark_bypass_is_rejected(self):
        args = ('bookmark-add', '--target-type', 'CUSTOM', '--target-id', 'conversation-note-test', '--note', '대화 요약')
        def rejected():
            process = subprocess.run([sys.executable, str(ROOT / 'scripts/run_agent.py'), *args], env=self.env, capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 1, process.stderr)
            return json.loads(process.stdout)

        result = rejected()
        self.assertFalse(result['data']['performed'])
        self.assertEqual(result['error']['code'], 'INVALID_BOOKMARK')
        self.assertFalse(self.db_path.exists())
        self.seed()
        self.assertFalse(rejected()['data']['performed'])
        self.assertEqual(self.cli('bookmarks')['data'], [])
        db = LocalDatabase(self.db_path, read_only=True)
        self.addCleanup(db.close)
        self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM context_bookmarks').fetchone()[0], 0)

    def test_regular_bookmark_listing_remains_separate(self):
        self.seed()
        created = self.cli('bookmark-add', '--target-type', 'ASSIGNMENT', '--target-id', 'assignment-1')['data']
        result = self.cli('ask', '--text', '북마크 보여줘')
        self.assertEqual(result['toolCalls'], ['get_bookmarks'])
        self.assertEqual(result['data'][0]['id'], created['id'])

    def test_checkpoint_invalid_command_shapes(self):
        for text in ('과제 저장 --과제ID', '과제 저장 --과제ID a --과제ID b', '과제 저장 --과제ID a extra', '과제 불러오기 --unknown x', '과제 저장 --과제ID "broken'):
            with self.subTest(text=text): self.assertIn('error', parse_context_command(text))

    def test_snapshot_import_upsert_and_cleanup(self):
        data = snapshot()
        input_path = Path(self.temp.name) / 'snapshot.json'
        input_path.write_text(json.dumps(data), encoding='utf-8')
        for _ in range(2):
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/ingest_tls.py'), '--input', str(input_path), '--user-id', 'u1'], env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        db = LocalDatabase(self.db_path); self.addCleanup(db.close)
        self.assertEqual(len(db.get_assignments('u1')), 1)
        upsert(db, 'u1', data)
        self.assertEqual(len(db.get_notices('u1')), 1); self.assertEqual(len(db.get_resources('u1')), 1)
        data['assignments'][0]['submissionStatus'] = 'SUBMITTED'
        upsert(db, 'u1', data)
        self.assertEqual(db.get_assignments('u1')[0]['submissionStatus'], 'SUBMITTED')
        upsert(db, 'u1', {field: [] for field in data})
        self.assertEqual(db.get_assignments('u1'), []); self.assertEqual(db.get_resources('u1'), [])
        self.assertEqual(db.connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_moodle_adapter_with_explicit_html_fixtures(self):
        session = FakeTLSSession()
        provider = MoodleTLSProvider(session)
        self.assertEqual(provider.get_courses('u')[0]['name'], '테스트 과목')
        self.assertEqual(provider.get_assignments('u')[0]['submissionStatus'], 'SUBMITTED')
        self.assertEqual(provider.get_lectures('u')[0]['watchedSeconds'], 300)
        self.assertEqual(provider.get_notices('u')[0]['title'], '테스트 공지')
        self.assertEqual(provider.get_notices('u')[0]['content'], '실제 공지 내용')
        resources = provider.get_resources('u', notices=[{'courseId': 'tls-course-1', 'title': '2주차 파일 안내', 'content': '2주차 보충자료.pdf는 다운로드 금지입니다.'}])
        self.assertEqual(resources[0]['_content'], b'%PDF-test-fixture')
        prohibited = [item for item in resources if item['downloadStatus'] == 'PROHIBITED']
        self.assertEqual(len(prohibited), 3)
        self.assertTrue(all('_content' not in item for item in prohibited))
        self.assertIn('다운로드 제한', prohibited[0]['downloadReason'])
        self.assertIn('과목 공지', prohibited[1]['downloadReason'])
        self.assertIn('서버가 파일 다운로드를 거부', prohibited[2]['downloadReason'])
        self.assertEqual(session.byte_requests, ['/mod/resource/view.php?id=5', '/mod/resource/view.php?id=8'])

    def test_allowed_ubfile_viewer_redirects_to_observed_download(self):
        session = FakeTLSSession()
        session.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/ubfile/view.php?id=11">허용 자료.pdf</a></li>'
        session.pages['/mod/ubfile/view.php?id=11'] = '<a href="/local/ubdoc/?id=12345&amp;tp=m&amp;pg=ubfile">Down</a>'
        item = MoodleTLSProvider(session).get_resources('u')[0]
        self.assertEqual(item['_content'], b'%PDF-test-fixture')
        self.assertEqual(session.byte_requests, ['https://tls.kku.ac.kr/local/ubdoc/download.php?id=12345&tp=m&pg=ubfile'])

    def test_moodle_provider_reuses_course_pages_during_one_sync(self):
        session = FakeTLSSession()
        provider = MoodleTLSProvider(session)
        provider.get_assignments('u')
        provider.get_assignments('u')
        self.assertEqual(session.requests.count('/local/ubion/user/'), 1)
        self.assertEqual(session.requests.count('/course/view.php?id=1'), 1)
        self.assertEqual(session.requests.count('/mod/assign/view.php?id=2'), 2)

    def test_moodle_activity_parsing_is_reused_but_details_stay_fresh(self):
        session = FakeTLSSession()
        provider = MoodleTLSProvider(session)
        with patch.object(provider, '_links', wraps=provider._links) as links:
            self.assertEqual(provider.get_assignments('u')[0]['submissionStatus'], 'SUBMITTED')
            provider.get_lectures('u')
            provider.get_notices('u')
            session.pages['/mod/assign/view.php?id=2'] = '<p>종료 일시: 2026-10-05 23:59</p><p>제출 안 함</p>'
            self.assertEqual(provider.get_assignments('u')[0]['submissionStatus'], 'NOT_SUBMITTED')
            activity_calls = [call for call in links.call_args_list if call.kwargs.get('only_activities')]
            self.assertEqual(len(activity_calls), 1)
        session.pages['/course/view.php?id=1'] = ''
        self.assertEqual(MoodleTLSProvider(session).get_assignments('u'), [])

    def test_moodle_provider_reuses_existing_local_file(self):
        session = FakeTLSSession()
        session.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/resource/view.php?id=5">테스트 자료</a></li>'
        cached_path = Path(self.temp.name) / 'files' / 'test.pdf'
        cached_path.parent.mkdir()
        cached_path.write_bytes(b'%PDF-cached')
        resource = MoodleTLSProvider(session).get_resources('u', existing_resources={
            '5': {'remotePath': '/mod/resource/view.php?id=5', 'localPath': str(cached_path), 'fileName': 'test.pdf', 'extension': 'pdf', 'downloadedAt': '2026-10-02T00:00:00+00:00'}
        })[0]
        self.assertEqual(resource['downloadStatus'], 'DOWNLOADED')
        self.assertNotIn('/mod/resource/view.php?id=5', session.byte_requests)

    def test_real_database_cache_keeps_tls_identifier(self):
        data = snapshot()
        root = Path(self.temp.name) / 'cached-files'
        root.mkdir()
        path = root / 'test.pdf'
        path.write_bytes(b'%PDF-cached')
        data['resources'][0].update(externalId='5', localPath=str(path), mimeType='application/pdf', downloadStatus='DOWNLOADED')
        db = LocalDatabase(self.db_path)
        upsert(db, 'fixture-user', data)
        db.close()
        cached = _cached_resources(self.db_path, 'fixture-user', root)
        self.assertEqual(cached['5']['localPath'], str(path))
        data['resources'].append(dict(data['resources'][0], id='another-resource', externalId='6'))
        db = LocalDatabase(self.db_path)
        upsert(db, 'fixture-user', data)
        db.close()
        self.assertEqual(_cached_resources(self.db_path, 'fixture-user', root), {})
        self.assertEqual(path.read_bytes(), b'%PDF-cached')

    def test_moodle_requests_have_timeout(self):
        session = MoodleSession()
        session.logged_in = True
        with patch.object(session.opener, 'open', side_effect=TimeoutError) as request:
            for fetch in (session.get, session.get_bytes):
                with self.assertRaises(TimeoutError):
                    fetch('/course/view.php?id=1')
                self.assertEqual(request.call_args.kwargs['timeout'], 30)

    def test_html_probe_does_not_transfer_redirected_pdf(self):
        session = MoodleSession(); session.logged_in = True
        response = Mock(headers={'Content-Type': 'application/pdf'}, geturl=lambda: 'https://tls.kku.ac.kr/file.pdf')
        with patch.object(session.opener, 'open', return_value=response):
            self.assertEqual(session.get('/mod/ubfile/view.php?id=9'), '')
        response.read.assert_not_called()
        response.close.assert_called_once()
        response = Mock(headers={'Content-Type': 'text/html'}, geturl=lambda: 'https://tls.kku.ac.kr/view.php')
        response.read.return_value = '다운로드 금지'.encode()
        with patch.object(session.opener, 'open', return_value=response):
            self.assertIn('다운로드 금지', session.get('/view.php'))
        response.read.assert_called_once()

    def test_download_url_and_viewer_restrictions_before_file_read(self):
        check_download_url('https://fixture.invalid/test.pdf?forcedownload=0')
        for url in ('https://fixture.invalid/test.pdf?allowDownload=false',
                    'https://fixture.invalid/test.pdf?disableDownload=1'):
            with self.assertRaises(DownloadRestricted):
                check_download_url(url)
            response = Mock(geturl=lambda: url)
            session = MoodleSession(); session.logged_in = True
            with patch.object(session.opener, 'open', return_value=response):
                with self.assertRaises(DownloadRestricted): session.get_bytes('/test.pdf')
            response.read.assert_not_called()
            response.close.assert_called_once()
            redirect_body = Mock()
            with self.assertRaises(DownloadRestricted):
                _DownloadRedirectHandler().redirect_request(None, redirect_body, 302, '', {}, url)
            redirect_body.close.assert_called_once()
        session = FakeTLSSession()
        session.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/ubfile/view.php?id=9">뷰어 자료</a></li>'
        session.pages['/mod/ubfile/view.php?id=9'] = '<a href="/mod/ubfile/viewer.php?id=9">열기</a>'
        item = MoodleTLSProvider(session).get_resources('u')[0]
        self.assertEqual(item['downloadStatus'], 'NOT_DOWNLOADED')
        self.assertIn('원본 다운로드 주소를 확인하지 못했어요', item['downloadReason'])
        self.assertEqual(session.byte_requests, [])
        session = FakeTLSSession()
        session.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/ubfile/view.php?id=9">뷰어 자료</a></li>'
        session.pages['/mod/ubfile/view.php?id=9'] = '<p>다운로드 금지</p>'
        item = MoodleTLSProvider(session).get_resources('u')[0]
        self.assertEqual(item['downloadStatus'], 'PROHIBITED')
        self.assertIn('강의실 자료 항목', item['downloadReason'])
        self.assertEqual(session.byte_requests, [])

    def test_visible_download_text_ignores_hidden_and_malformed_markup(self):
        html = '<span style="display:none">다운로드 금지</span><script>다운로드 금지</script><p>자료 설명</p><![if gte IE 9]><p>보이는 안내</p><![endif]>'
        self.assertEqual(_plain_text(html), '자료 설명 보이는 안내')

    def test_metadata_refresh_never_downloads_and_still_checks_restrictions(self):
        session = FakeTLSSession()
        session.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/ubfile/view.php?id=9">수업자료.pdf</a></li>'
        session.pages['/mod/ubfile/view.php?id=9'] = '<p>수업자료</p>'
        progress = []
        item = MoodleTLSProvider(session).get_resources('u', download_new_files=False, progress=progress.append)[0]
        self.assertEqual(item['downloadStatus'], 'NOT_DOWNLOADED')
        self.assertEqual(session.byte_requests, [])
        self.assertTrue(progress)
        cached_path = Path(self.temp.name) / 'cached.pdf'
        cached_path.write_bytes(b'%PDF-cached')
        cached = {'9': {**item, 'localPath': str(cached_path), 'mimeType': 'application/pdf'}}
        reused = MoodleTLSProvider(session).get_resources('u', download_new_files=False, existing_resources=cached)[0]
        self.assertEqual(reused['downloadStatus'], 'DOWNLOADED')
        self.assertEqual(reused['localPath'], str(cached_path))
        session.pages['/mod/ubfile/view.php?id=9'] = '<p>다운로드 금지</p>'
        item = MoodleTLSProvider(session).get_resources('u', download_new_files=False, existing_resources=cached)[0]
        self.assertEqual(item['downloadStatus'], 'PROHIBITED')
        self.assertEqual(session.byte_requests, [])

    def test_malformed_viewer_markup_keeps_download_restriction(self):
        session = FakeTLSSession()
        session.pages['/course/view.php?id=1'] = '<li class="activity"><a href="/mod/ubfile/view.php?id=9">뷰어 자료</a></li>'
        session.pages['/mod/ubfile/view.php?id=9'] = '<p>안내</p><![broken]><p>다운로드 금지</p>'
        item = MoodleTLSProvider(session).get_resources('u')[0]
        self.assertEqual(item['downloadStatus'], 'PROHIBITED')
        self.assertEqual(session.byte_requests, [])

    def test_expired_session_does_not_return_login_page_as_empty_records(self):
        session = MoodleSession()
        session.logged_in = True
        response = Mock(headers={'Content-Type': 'text/html'}, geturl=lambda: 'https://fixture.invalid/login/index.php')
        with patch.object(session.opener, 'open', return_value=response), patch.object(session, '_decode', return_value='<input name="username">'):
            for fetch in (session.get, session.get_bytes):
                with self.assertRaises(LoginError):
                    fetch('/course/view.php?id=1')

    def test_moodle_login_failures_do_not_fetch_data(self):
        session = MoodleSession()
        with self.assertRaises(LoginError): session.get('/my/')
        with self.assertRaises(LoginError): session.get_bytes('/my/')
        response = SimpleNamespace(geturl=lambda: 'https://fixture.invalid/login/index.php')
        with patch.object(session, '_request', return_value=('<input name="username">', response)):
            with self.assertRaises(LoginError): session.login('fixture-user', 'fixture-password')
        self.assertFalse(session.logged_in)

    def test_secure_form_and_redaction(self):
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(FormUnavailable): collect_local(TLS_CREDENTIALS_FORM)
        output = StringIO()
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stderr.isatty', return_value=True), redirect_stdout(output):
            values = collect_local(TLS_CREDENTIALS_FORM, input_fn=lambda _: 'fixture-user', secret_input_fn=lambda _: 'fixture-password')
        self.assertNotIn('fixture-password', output.getvalue())
        self.assertEqual(redact(TLS_CREDENTIALS_FORM, values)['password'], '[REDACTED]')
        self.assertTrue(requested_schema(TLS_CREDENTIALS_FORM)['properties']['password']['writeOnly'])

    def test_credentials_storage_using_mock_keychain_only(self):
        config = Path(self.temp.name) / 'account.json'
        with patch.object(credentials, 'CONFIG_PATH', config), patch.object(credentials.subprocess, 'run') as keychain:
            credentials.save('fixture-user', 'fixture-password')
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('fixture-password', config.read_text())
            keychain.return_value = SimpleNamespace(stdout='fixture-password\n')
            self.assertEqual(credentials.load(), ('fixture-user', 'fixture-password'))
        with patch.object(credentials, 'CONFIG_PATH', Path(self.temp.name) / 'missing.json'), patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(FormUnavailable): credentials.resolve()

    def test_windows_credentials_use_user_bound_protection(self):
        config = Path(self.temp.name) / 'windows-account.json'
        with patch.object(credentials, 'CONFIG_PATH', config), patch.object(credentials.os, 'name', 'nt'), patch.object(credentials, '_windows_protect', return_value=b'protected-password') as protect:
            credentials.save('fixture-user', 'fixture-password')
            self.assertNotIn('fixture-password', config.read_text())
            protect.assert_called_once_with(b'fixture-password')
            with patch.object(credentials, '_windows_unprotect', return_value=b'fixture-password') as unprotect:
                self.assertEqual(credentials.load(), ('fixture-user', 'fixture-password'))
                unprotect.assert_called_once()

    def test_tls_scripts_stop_when_local_form_is_unavailable(self):
        import sync_tls
        import tls_fetch
        for module in (sync_tls, tls_fetch):
            with self.subTest(script=module.__name__), patch('sys.argv', [module.__name__]), patch.object(module, 'resolve', side_effect=FormUnavailable('local terminal required')), patch.object(module, 'MoodleSession') as session, redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit): module.main()
                session.assert_not_called()



class KnownIntegrationIssues(ProjectTestBase):
    """Regression checks for previously reproduced integration defects."""
    def test_current_context_uses_real_user_identity(self):
        db = LocalDatabase(self.db_path); self.addCleanup(db.close)
        upsert(db, 'student-a', snapshot())
        context = get_current_context(db, 'student-a', lambda: [])
        self.assertEqual(context['user']['name'], 'student-a')
        self.assertEqual(context['user']['department'], '테스트학과')

    def test_other_users_notices_survive_sync(self):
        db = LocalDatabase(self.db_path); self.addCleanup(db.close)
        upsert(db, 'student-a', snapshot('1'))
        upsert(db, 'student-b', snapshot('2'))
        self.assertEqual(len(db.get_notices('student-a')), 1)
        self.assertEqual(len(db.get_resources('student-a')), 1)

if __name__ == '__main__': unittest.main()
