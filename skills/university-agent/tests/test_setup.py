"""Bundled setup is portable, private, idempotent and independent per service."""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import setup_turtleneck as setup
from scripts import sync_tls
from features.guidance import guidance_request
from everytime_cache import LocalCache


class SetupTests(unittest.TestCase):
    def test_existing_state_is_read_only_and_does_not_load_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            account = root / 'account.json'
            account.write_text(json.dumps({'username': 'student', 'passwordProtected': 'DO_NOT_READ'}))
            db = root / 'school.db'
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE sync_state(user_id TEXT, state_key TEXT, state_value TEXT)')
                connection.executemany('INSERT INTO sync_state VALUES(?,?,?)',
                                       [('student', 'course_ids', '[]'), ('student', 'course_fingerprints', '{}')])
            cache = root / 'eta.db'
            with LocalCache(cache) as store:
                store.put('setup', {'view': 'initial'}, {'status': 'READY', 'courseCount': 8})
            with patch.object(setup, 'CONFIG_PATH', account), patch.dict(os.environ, {
                'UNIVERSITY_AGENT_DB': str(db), 'EVERYTIME_DB': str(cache), 'UNIVERSITY_AGENT_USER_ID': 'student', 'TLS_USERNAME': 'student'
            }), patch('session_store.load', side_effect=AssertionError('secrets must not be loaded')), patch.object(setup, 'open_connection_terminal') as launch:
                with redirect_stdout(io.StringIO()) as out, patch.object(sys, 'argv', ['setup_turtleneck.py']):
                    self.assertEqual(setup.main(), 0)
                launch.assert_not_called()
                self.assertEqual(setup.status()['tls']['status'], 'READY')
                self.assertEqual(setup.status()['everytime']['courseCount'], 8)
                self.assertNotIn('DO_NOT_READ', out.getvalue())
                with patch.dict(os.environ, {'TLS_USERNAME': 'other'}):
                    self.assertEqual(setup.status()['tls']['status'], 'NEEDS_LOGIN')
                with LocalCache(cache) as store:
                    store.put('setup', {'view': 'initial'}, {'status': 'RUNNING'})
                self.assertEqual(setup.status()['everytime']['status'], 'RUNNING')
                with sqlite3.connect(cache) as connection:
                    connection.execute("UPDATE cache_entries SET fetched_at='2000-01-01T00:00:00+00:00'")
                self.assertEqual(setup.status()['everytime']['status'], 'PARTIAL')

    def test_missing_state_launches_once_and_only_mode_survives(self):
        pending = {'tls': {'status': 'READY'}, 'everytime': {'status': 'NEEDS_SETUP'}}
        with patch.object(setup, 'status', return_value=pending), patch.object(setup, 'open_connection_terminal') as launch:
            with redirect_stdout(io.StringIO()), patch.object(sys, 'argv', ['setup', '--only', 'everytime']):
                setup.main()
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(launch.call_args.args[0][-3:], ['--run', '--only', 'everytime'])
            with redirect_stdout(io.StringIO()), patch.object(sys, 'argv', ['setup', '--status']):
                setup.main()
            self.assertEqual(launch.call_count, 1)

    def test_unreadable_or_running_cache_never_triggers_login(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'eta.db'
            db.touch()
            with patch.object(setup, 'default_path', return_value=db), patch.object(setup.sqlite3, 'connect', side_effect=sqlite3.OperationalError('unable to open database file')):
                self.assertEqual(setup.status()['everytime']['status'], 'UNAVAILABLE')
        for state in ('UNAVAILABLE', 'RUNNING'):
            with patch.object(setup, 'status', return_value={'tls': {'status': 'READY'}, 'everytime': {'status': state}}), patch.object(setup, 'open_connection_terminal') as launch, redirect_stdout(io.StringIO()), patch.object(sys, 'argv', ['setup']):
                self.assertEqual(setup.main(), 1)
                launch.assert_not_called()

    def test_tls_failure_does_not_block_browser_and_partial_retry_uses_cache(self):
        pending = {'tls': {'status': 'NEEDS_SETUP'}, 'everytime': {'status': 'PARTIAL'}}
        with patch.object(setup, 'status', return_value=pending), patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), patch('session_store.load', side_effect=FileNotFoundError), patch('connect.connect', return_value='cookies') as login, patch('everytime.Client', return_value='client'), patch('everytime.initialize', return_value={'status': 'READY'}) as initialize:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(setup.prepare(), 1)
            login.assert_called_once_with(300)
            initialize.assert_called_once_with(client='client', max_age=-1)
        pending['tls']['status'] = 'READY'
        with patch.object(setup, 'status', return_value=pending), patch('session_store.load', return_value='cookies'), patch('connect.connect') as login, patch('everytime.Client'), patch('everytime.initialize', return_value={'status': 'READY'}), patch.object(setup.subprocess, 'run') as tls:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(setup.prepare(), 0)
            login.assert_not_called()
            tls.assert_not_called()

    def test_private_console_required_and_mac_windows_commands(self):
        with patch.object(sys, 'argv', ['setup', '--run']), patch.object(sys.stdin, 'isatty', return_value=False), redirect_stdout(io.StringIO()), patch('sys.stderr', new=io.StringIO()):
            with self.assertRaises(SystemExit):
                setup.main()
        command = [sys.executable, str(ROOT / 'scripts/setup_turtleneck.py'), '--run']
        with patch.object(sync_tls.sys, 'platform', 'darwin'), patch.dict(os.environ, {'EVERYTIME_DB': '/tmp/eta space/cache.db'}), patch.object(sync_tls.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
            sync_tls.open_connection_terminal(command)
        self.assertIn('EVERYTIME_DB=', ' '.join(run.call_args.args[0]))
        self.assertIn('setup_turtleneck.py', ' '.join(run.call_args.args[0]))
        with patch.object(sync_tls.sys, 'platform', 'win32'), patch.object(sync_tls.os, 'name', 'nt'), patch.object(sync_tls.subprocess, 'CREATE_NEW_CONSOLE', 16, create=True), patch.object(sync_tls.subprocess, 'Popen') as launch, redirect_stdout(io.StringIO()):
            sync_tls.open_connection_terminal(command)
        launch.assert_called_once_with(command, creationflags=16)
        with patch.object(sys, 'argv', ['setup', '--run']), patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stderr, 'isatty', return_value=True), patch.object(setup.os, 'name', 'nt'), patch.object(setup, 'prepare', return_value=0), patch('builtins.input', return_value='') as close:
            self.assertEqual(setup.main(), 0)
            close.assert_called_once()

    def test_single_folder_install_contains_all_runners(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'installed'
            shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            env = {**os.environ, 'UNIVERSITY_AGENT_DB': str(Path(folder) / 'absent.db'), 'EVERYTIME_DB': str(Path(folder) / 'absent-eta.db')}
            for script, args in [('scripts/setup_turtleneck.py', ['--status']), ('integrations/everytime/scripts/everytime.py', ['--help']), ('integrations/everytime/scripts/everytime_actions.py', ['--list-actions'])]:
                result = subprocess.run([sys.executable, str(target / script), *args], cwd=folder, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([p.relative_to(target).as_posix() for p in target.rglob('SKILL.md')], ['SKILL.md'])
            self.assertFalse((Path(folder) / 'absent.db').exists())
            self.assertFalse((Path(folder) / 'absent-eta.db').exists())
        self.assertEqual(guidance_request('터틀넥 연결해줘')['nextCommands'], ['python3 scripts/setup_turtleneck.py'])


if __name__ == '__main__':
    unittest.main()
