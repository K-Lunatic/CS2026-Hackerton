import io
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from providers.moodle_session import LoginError
from providers.forms import TLS_CREDENTIALS_FORM, collect_browser
from scripts import sync_tls


class TlsLoginTests(unittest.TestCase):
    def test_browser_form_returns_values_through_localhost_only(self):
        opened = {}

        def open_form(url):
            opened["url"] = url

            def submit():
                deadline = time.monotonic() + 1
                while "url" not in opened or not opened["url"]:
                    if time.monotonic() > deadline:
                        return
                    time.sleep(0.01)
                query = parse_qs(urlsplit(url).query)
                body = urlencode({
                    "token": query["token"][0],
                    "username": "student",
                    "password": "secret",
                }).encode()
                urlopen(Request(url, data=body), timeout=1).read()

            threading.Thread(target=submit, daemon=True).start()
            return True

        with patch("providers.forms.webbrowser.open", side_effect=open_form):
            try:
                values = collect_browser(TLS_CREDENTIALS_FORM, timeout=2)
            except PermissionError:
                self.skipTest("이 실행 환경에서는 localhost 소켓을 열 수 없음")

        self.assertEqual(values, {"username": "student", "password": "secret"})
        self.assertTrue(urlsplit(opened["url"]).hostname == "127.0.0.1")

    def test_failed_saved_login_reopens_secure_form_and_saves_only_after_success(self):
        class Session:
            total_calls = 0

            def __init__(self, _base_url):
                self.calls = 0

            def login(self, username, password):
                self.calls += 1
                Session.total_calls += 1
                if Session.total_calls == 1:
                    raise LoginError("invalid")

        with patch.object(sync_tls, 'resolve', side_effect=[('student', 'old'), ('student', 'new')]) as resolve, \
             patch.object(sync_tls, 'MoodleSession', Session), patch.object(sync_tls, 'save') as save, \
             redirect_stdout(io.StringIO()) as output:
            username, session = sync_tls._login_with_retries('https://tls.example', 'student')

        self.assertEqual(username, 'student')
        self.assertEqual(session.calls, 1)
        self.assertEqual(resolve.call_args_list[0].kwargs, {'force_input': False})
        self.assertEqual(resolve.call_args_list[1].kwargs, {'force_input': True})
        save.assert_called_once_with('student', 'new')
        self.assertIn('(1/3)', output.getvalue())


if __name__ == '__main__':
    unittest.main()
