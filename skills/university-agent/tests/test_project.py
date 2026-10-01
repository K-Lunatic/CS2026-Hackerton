"""Project-wide offline integration tests. Never access real accounts or Keychain."""
from contextlib import redirect_stdout, redirect_stderr
from email.message import Message
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from storage.local_db import LocalDatabase
from providers import credentials
from providers.forms import collect_local, FormUnavailable, redact, requested_schema, TLS_CREDENTIALS_FORM
from providers.moodle_session import MoodleSession, LoginError
from providers.moodle_provider import MoodleTLSProvider
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
            '/course/view.php?id=1': ''.join(f'<li class="activity"><a href="/mod/{kind}/view.php?id={id}">{title}</a></li>' for kind, id, title in [('assign', 2, '테스트 과제'), ('vod', 3, '테스트 강의'), ('ubboard', 10, '공지사항'), ('resource', 5, '테스트 자료')]),
            '/mod/assign/view.php?id=2': '<p>종료 일시: 2026-10-05 23:59</p><p>제출 완료</p>',
            '/mod/vod/viewer.php?id=3': '<span class="playtime">10:00</span><script>var is_progress = 50; var is_complete = 0;</script>',
            '/mod/ubboard/view.php?id=10': '<a href="/mod/ubboard/article.php?id=10&amp;bwid=11">공지</a>',
            '/mod/ubboard/article.php?id=10&bwid=11': '<div class="subject"><h3>테스트 공지</h3></div><div class="content">공지 내용</div></div><p>작성일: 2026-10-01 12:00</p>',
        }
    def get(self, path):
        return self.pages[path]
    def get_bytes(self, path):
        headers = Message(); headers['Content-Type'] = 'application/pdf'
        return b'%PDF-test-fixture', SimpleNamespace(headers=headers, geturl=lambda: 'https://fixture.invalid/test.pdf')


class ProjectTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / 'test.db'
        self.env = dict(os.environ, UNIVERSITY_AGENT_DB=str(self.db_path), UNIVERSITY_AGENT_USER_ID='fixture-user', TEAM_HANDOVER_API_URL='', TEAM_HANDOVER_MODEL='')

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

    def test_checkpoint_invalid_command_shapes(self):
        for text in ('과제 저장', '과제 저장 --과제ID', '과제 저장 --과제ID a --과제ID b', '과제 저장 --과제ID a extra', '과제 불러오기 --unknown x', '과제 저장 --과제ID "broken'):
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
        provider = MoodleTLSProvider(FakeTLSSession())
        self.assertEqual(provider.get_courses('u')[0]['name'], '테스트 과목')
        self.assertEqual(provider.get_assignments('u')[0]['submissionStatus'], 'SUBMITTED')
        self.assertEqual(provider.get_lectures('u')[0]['watchedSeconds'], 300)
        self.assertEqual(provider.get_notices('u')[0]['title'], '테스트 공지')
        self.assertEqual(provider.get_resources('u')[0]['_content'], b'%PDF-test-fixture')

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

    def test_tls_scripts_stop_when_local_form_is_unavailable(self):
        import sync_tls
        import tls_fetch
        for module in (sync_tls, tls_fetch):
            with self.subTest(script=module.__name__), patch('sys.argv', [module.__name__]), patch.object(module, 'resolve', side_effect=FormUnavailable('local terminal required')), patch.object(module, 'MoodleSession') as session, redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit): module.main()
                session.assert_not_called()



class KnownIntegrationIssues(ProjectTestBase):
    """Reproduced defects, explicitly expected failures until feature owners fix them."""
    @unittest.expectedFailure
    def test_team_progress_intent_reaches_handover(self):
        result = self.cli('ask', '--text', '팀플 진행 상황 알려줘', '--records', '민수는 테스트 완료.', '--prepare')
        self.assertEqual(result['toolCalls'], ['prepare_handover'])

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
