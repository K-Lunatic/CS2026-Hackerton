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

    def request(self, path, method='GET', fields=None, bearer=None, query=None, extra=None, json_body=None):
        raw = json.dumps(json_body, ensure_ascii=False).encode() if json_body is not None else urlencode(fields or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': urlencode(query or {}), 'CONTENT_TYPE': 'application/json' if json_body is not None else 'application/x-www-form-urlencoded', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
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

    def test_new_screenshot_assignment_checkpoint_list_load_and_completion(self):
        token = self.connect('alice')['access_token']
        exact = {'userMessage': '과제 목록 불러오기'}
        self.assertEqual(self.request('/v1/checkpoints', bearer=token, query=exact)['body']['data'], [])
        for command in ('과제 목록 불러와', '과제 목록 불러오기 ', '저장한 과제 목록 보여줘'):
            self.assertEqual(self.request('/v1/checkpoints', bearer=token, query={'userMessage': command})['status'], 400)
        payload = {'command': '과제 저장 --새과제 "캡처 문제 풀이"', 'progress': '절반 풀이 완료',
                   'blocker': '대화에서 확인되지 않음', 'nextAction': 'AI 제안: 남은 문제 풀기', 'completedItems': ['1번 풀이']}
        saved = self.request('/v1/checkpoint', 'POST', bearer=token, json_body=payload)
        self.assertEqual(saved['status'], 201)
        self.assertEqual(saved['body']['data']['assignmentTitle'], '캡처 문제 풀이')
        listed = self.request('/v1/checkpoints', bearer=token, query=exact)
        self.assertEqual(listed['body']['total'], 1)
        self.assertEqual(listed['body']['data'][0]['progress'], '절반 풀이 완료')
        loaded = self.request('/v1/checkpoint', bearer=token, query={'command': '과제 불러오기 캡처 문제'})
        self.assertEqual(loaded['body']['data'], saved['body']['data'])
        self.assertEqual(self.request('/v1/checkpoint', bearer=token, query={'command': '캡처 문제 불러와'})['status'], 400)
        assignment = next(item for item in self.request('/v1/assignments', bearer=token)['body']['data'] if item['title'] == '캡처 문제 풀이')
        for answer in (None, '아니요', '응', '예 '):
            payload = {'assignmentId': assignment['id']}
            if answer is not None:
                payload['submissionAnswer'] = answer
            response = self.request('/v1/assignments/complete', 'POST', bearer=token, json_body=payload)
            self.assertEqual(response['status'], 200 if answer == '아니요' else 400)
            self.assertEqual(self.request('/v1/checkpoints', bearer=token, query=exact)['body']['total'], 1)
        completed = self.request('/v1/assignments/complete', 'POST', bearer=token,
                                 json_body={'assignmentId': assignment['id'], 'submissionAnswer': '예'})
        self.assertTrue(completed['body']['completed'])
        self.assertEqual(self.request('/v1/checkpoints', bearer=token, query=exact)['body']['data'], [])
        self.assertIsNone(self.request('/v1/checkpoint', bearer=token, query={'command': '과제 불러오기 캡처 문제'})['body']['data'])

    def test_checkpoint_user_isolation_and_invalid_save_has_no_assignment(self):
        alice, bob = self.connect('alice')['access_token'], self.connect('bob')['access_token']
        payload = {'command': '과제 저장 --새과제 "개인 과제"', 'progress': '초안 작성',
                   'blocker': '대화에서 확인되지 않음', 'nextAction': 'AI 제안: 검토'}
        self.assertEqual(self.request('/v1/checkpoint', 'POST', bearer=alice, json_body={**payload, 'command': '지금 과제 저장해줘'})['status'], 400)
        self.assertEqual(self.request('/v1/checkpoint', 'POST', bearer=alice, json_body={**payload, 'nextAction': ''})['status'], 400)
        self.assertEqual(self.request('/v1/assignments', bearer=alice)['body']['total'], 1)
        self.assertEqual(self.request('/v1/checkpoint', 'POST', bearer=alice, json_body=payload)['status'], 201)
        self.assertEqual(self.request('/v1/checkpoints', bearer=bob, query={'userMessage': '과제 목록 불러오기'})['body']['data'], [])

    def test_english_checkpoint_commands_and_followup_choices(self):
        token = self.connect('alice')['access_token']
        summary = {'progress': '입력 처리 완료', 'blocker': '대화에서 확인되지 않음', 'nextAction': 'AI 제안: 검토'}
        tls = self.request('/v1/checkpoint', 'POST', bearer=token,
                           json_body={'command': 'save "alice assignment"', **summary})
        self.assertEqual(tls['status'], 201)
        self.assertEqual(tls['body']['data']['assignmentTitle'], 'alice assignment')
        self.assertIn('list', tls['body']['nextCommands'])

        self.assertEqual(self.request('/v1/checkpoint', 'POST', bearer=token,
                                      json_body={'command': 'save "개인 과제"', **summary})['status'], 409)
        manual = self.request('/v1/checkpoint', 'POST', bearer=token,
                              json_body={'command': 'save new "개인 과제"', **summary})
        self.assertEqual(manual['status'], 201)
        again = self.request('/v1/checkpoint', 'POST', bearer=token,
                             json_body={'command': 'save new "개인 과제"', **summary})
        self.assertEqual(again['status'], 201)
        assignments = self.request('/v1/assignments', bearer=token)['body']['data']
        self.assertEqual(len([item for item in assignments if item['title'] == '개인 과제']), 1)

        listed = self.request('/v1/checkpoints', bearer=token, query={'userMessage': 'list'})
        self.assertEqual(listed['body']['total'], 2)
        self.assertIn('load "개인 과제"', listed['body']['nextCommands'])
        self.assertEqual(self.request('/v1/checkpoint', bearer=token,
                                      query={'command': 'load "개인 과제"'})['body']['data'], again['body']['data'])
        self.assertIn('nextCommands', self.request('/v1/lectures', bearer=token)['body'])


if __name__ == '__main__':
    unittest.main()
