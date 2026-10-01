"""Offline OAuth and account-isolation checks; never uses real TLS credentials."""
import base64
import hashlib
import io
import json
import re
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from server.app import Gateway

CALLBACK = 'https://chatgpt.com/aip/g-test/oauth/callback'


class FakeSession:
    def login(self, username, password):
        if password != 'fixture-password':
            raise ValueError('secret upstream detail')
        self.username = username


class FakeProvider:
    def __init__(self, session):
        self.user = session.username

    def get_courses(self, user):
        return [{'id': 'course-1', 'name': user + ' course'}]

    def get_assignments(self, user):
        return [{'id': 'assignment-1', 'courseId': 'course-1', 'title': user + ' assignment', 'dueAt': '2026-10-08T23:59:00+09:00', 'submissionStatus': 'NOT_SUBMITTED'}]

    def get_lectures(self, user):
        return []

    def get_notices(self, user):
        return []


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Gateway('https://example.com', 'client', 'x' * 48, [CALLBACK], self.temp.name, FakeSession)
        self.provider = patch('server.app.MoodleTLSProvider', FakeProvider)
        self.provider.start()

    def tearDown(self):
        for _ in range(200):
            if not any(item['running'] for item in self.app.accounts.values()):
                break
            time.sleep(.005)
        self.provider.stop()
        self.temp.cleanup()

    def request(self, path, method='GET', fields=None, bearer=None, query=None, extra=None):
        raw = urlencode(fields or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': urlencode(query or {}), 'CONTENT_TYPE': 'application/x-www-form-urlencoded', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        if bearer:
            env['HTTP_AUTHORIZATION'] = 'Bearer ' + bearer
        env.update(extra or {})
        result = {}
        def start(status, headers):
            result.update(status=int(status.split()[0]), headers=dict(headers))
        body = b''.join(self.app(env, start)).decode()
        result['body'] = json.loads(body) if result['headers']['Content-Type'].startswith('application/json') else body
        return result

    def authorize(self, **extra):
        response = self.request('/oauth/authorize', query={'client_id': 'client', 'redirect_uri': CALLBACK, 'state': 'original-state', 'response_type': 'code', **extra})
        self.assertEqual(response['status'], 200)
        self.assertIn('type="password"', response['body'])
        return re.search(r'name="ticket" value="([^"]+)"', response['body']).group(1)

    def code(self, username='alice', **extra):
        ticket = self.authorize(**extra)
        response = self.request('/oauth/login', 'POST', {'ticket': ticket, 'username': username, 'password': 'fixture-password'})
        self.assertEqual(response['status'], 302)
        query = parse_qs(urlsplit(response['headers']['Location']).query)
        self.assertEqual(query['state'], ['original-state'])
        self.assertNotIn('fixture-password', str(response))
        self.assertEqual(self.request('/oauth/login', 'POST', {'ticket': ticket})['status'], 400)
        return query['code'][0]

    def exchange(self, code, **extra):
        return self.request('/oauth/token', 'POST', {'client_id': 'client', 'client_secret': 'x' * 48, 'grant_type': 'authorization_code', 'redirect_uri': CALLBACK, 'code': code, **extra})

    def connect(self, user):
        response = self.exchange(self.code(user))
        self.assertEqual(response['status'], 200)
        for _ in range(200):
            if not self.app.accounts[user]['running']:
                break
            time.sleep(.005)
        self.assertIsNone(self.app.accounts[user]['error'])
        return response['body']

    def test_oauth_replay_refresh_expiry_and_disconnect(self):
        code = self.code()
        tokens = self.exchange(code)['body']
        self.assertEqual(self.exchange(code)['status'], 400)
        self.assertEqual(self.request('/v1/courses')['status'], 401)
        self.assertEqual(self.request('/v1/courses', bearer=tokens['refresh_token'])['status'], 401)
        fields = {'client_id': 'client', 'client_secret': 'x' * 48, 'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']}
        refreshed = self.request('/oauth/token', 'POST', fields)
        self.assertEqual(refreshed['status'], 200)
        self.assertEqual(self.request('/oauth/token', 'POST', fields)['status'], 400)
        self.app.tokens[hashlib.sha256(tokens['access_token'].encode()).hexdigest()]['expires'] = 0
        self.assertEqual(self.request('/v1/sync', bearer=tokens['access_token'])['status'], 401)
        for _ in range(200):
            if not self.app.accounts['alice']['running']:
                break
            time.sleep(.005)
        new = refreshed['body']['access_token']
        self.assertEqual(self.request('/v1/disconnect', 'POST', bearer=new)['status'], 200)
        self.assertEqual(self.request('/v1/sync', bearer=new)['status'], 401)

    def test_account_isolation_deadlines_pagination_and_schema(self):
        alice, bob = self.connect('alice'), self.connect('bob')
        for user, tokens in [('alice', alice), ('bob', bob)]:
            result = self.request('/v1/assignments', bearer=tokens['access_token'], query={'unsubmitted': 'true'})
            self.assertEqual(result['body']['data'][0]['title'], user + ' assignment')
            self.assertEqual(result['body']['sync']['availableSections'], ['courses', 'assignments', 'lectures', 'notices'])
            self.assertEqual(self.request('/v1/assignments', bearer=tokens['access_token'], query={'due_before': '2026-10-05T00:00:00+09:00'})['body']['data'], [])
            self.assertEqual(self.request('/v1/assignments', bearer=tokens['access_token'], query={'due_before': '2026-10-05'})['status'], 400)
            self.assertEqual(self.request('/v1/assignments', bearer=tokens['access_token'], query={'limit': '0'})['status'], 400)
        schema = self.request('/openapi.json')['body']
        self.assertEqual(schema['servers'], [{'url': 'https://example.com'}])
        self.assertNotIn('/oauth/login', schema['paths'])
        self.assertFalse(schema['paths']['/v1/sync']['post']['x-openai-isConsequential'])

    def test_wrong_redirect_state_secret_and_pkce_are_rejected(self):
        self.assertEqual(self.request('/oauth/authorize', query={'client_id': 'client', 'redirect_uri': 'https://evil.invalid', 'state': 'x', 'response_type': 'code'})['status'], 400)
        self.assertEqual(self.request('/oauth/authorize', query={'client_id': 'client', 'redirect_uri': CALLBACK, 'response_type': 'code'})['status'], 400)
        code = self.code()
        response = self.exchange(code, client_secret='wrong')
        self.assertEqual(response['status'], 401)
        self.assertEqual(self.exchange(code)['status'], 200)
        verifier = 'v' * 43
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        code = self.code(code_challenge=challenge, code_challenge_method='S256')
        self.assertEqual(self.exchange(code, code_verifier='wrong')['status'], 400)
        code = self.code(code_challenge=challenge, code_challenge_method='S256')
        self.assertEqual(self.exchange(code, code_verifier=verifier)['status'], 200)

    def test_partial_sync_keeps_assignments_and_failed_refresh_keeps_old_data(self):
        with patch.object(FakeProvider, 'get_lectures', side_effect=TimeoutError('secret')):
            tokens = self.exchange(self.code())['body']
            for _ in range(200):
                if not self.app.accounts['alice']['running']:
                    break
                time.sleep(.005)
            result = self.request('/v1/assignments', bearer=tokens['access_token'])
            self.assertEqual(result['status'], 200)
            self.assertEqual(len(result['body']['data']), 1)
            self.assertIsNotNone(result['body']['sync']['error'])
            self.assertEqual(self.request('/v1/lectures', bearer=tokens['access_token'])['status'], 503)
            self.assertNotIn('secret', str(result))
        with patch.object(FakeProvider, 'get_assignments', side_effect=TimeoutError):
            self.app.sync('alice')
            for _ in range(200):
                if not self.app.accounts['alice']['running']:
                    break
                time.sleep(.005)
            self.assertEqual(len(self.request('/v1/assignments', bearer=tokens['access_token'])['body']['data']), 1)

    def test_browser_secrets_and_errors_do_not_escape(self):
        ticket = self.authorize()
        result = self.request('/oauth/login', 'POST', {'ticket': ticket, 'username': 'alice', 'password': 'wrong'})
        self.assertEqual(result['status'], 401)
        self.assertNotIn('secret upstream detail', result['body'])
        self.assertNotIn('wrong', result['body'])
        self.assertEqual(result['headers']['Cache-Control'], 'no-store')
        self.assertEqual(result['headers']['Referrer-Policy'], 'no-referrer')
        ticket = self.authorize()
        self.assertEqual(self.request('/oauth/login', 'POST', {'ticket': ticket, 'username': 'alice', 'password': 'fixture-password'}, extra={'HTTP_ORIGIN': 'https://evil.invalid'})['status'], 403)
        self.assertEqual(self.request('/oauth/token', 'POST', extra={'CONTENT_LENGTH': '999999'})['status'], 413)


if __name__ == '__main__':
    unittest.main()
