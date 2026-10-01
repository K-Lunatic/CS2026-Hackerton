"""ChatGPT Actions gateway. No TLS passwords are persisted or sent to ChatGPT."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from html import escape
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/university-agent'))
from providers.moodle_session import MoodleSession
from providers.moodle_provider import MoodleTLSProvider
from storage.local_db import LocalDatabase
from features.assignments import get_assignments
from features.lectures import get_lectures


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Gateway:
    def __init__(self, base_url, client_id, client_secret, redirects, data_dir, session_factory=MoodleSession):
        if urlsplit(base_url).scheme != 'https' or not urlsplit(base_url).netloc or urlsplit(base_url).path not in ('', '/') or urlsplit(base_url).query or urlsplit(base_url).fragment or urlsplit(base_url).username:
            raise ValueError('PUBLIC_URL must be an HTTPS origin')
        if len(client_secret) < 32 or not client_id or not redirects:
            raise ValueError('Set CLIENT_ID, CLIENT_SECRET (32+ characters), and exact REDIRECT_URIS')
        for uri in redirects:
            if not re.fullmatch(r'https://(?:chatgpt\.com|chat\.openai\.com)/aip/g-[A-Za-z0-9_-]+/oauth/callback', uri):
                raise ValueError('REDIRECT_URIS must contain exact ChatGPT GPT callbacks')
        self.base_url = base_url.rstrip('/')
        self.client_id, self.client_secret, self.redirects = client_id, client_secret, redirects
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        self.session_factory = session_factory
        self.flows, self.codes, self.tokens, self.accounts, self.attempts = {}, {}, {}, {}, {}
        # ponytail: one process and in-memory OAuth; use a shared token store before multiple workers.
        self.lock = threading.RLock()

    def issue(self, store, data, ttl):
        value = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            for key in list(store):
                if store[key]['expires'] <= now:
                    del store[key]
            store[digest(value)] = {**data, 'expires': now + ttl}
        return value

    def take(self, store, value, consume=False):
        with self.lock:
            key = digest(value)
            item = store.pop(key, None) if consume else store.get(key)
            return item.copy() if item and item['expires'] > time.time() else None

    def db_path(self, user):
        return self.data_dir / (digest(user) + '.db')

    def sync(self, user):
        with self.lock:
            account = self.accounts.get(user)
            if account is None or account['running']:
                return
            account['running'] = True
            account['error'] = None
        threading.Thread(target=self.sync_worker, args=(user, account), daemon=True).start()

    def sync_worker(self, user, account):
        db = None
        try:
            provider = MoodleTLSProvider(account['session'])
            records = {'courses': provider.get_courses(user)}
            for field in ('assignments', 'lectures', 'notices'):
                with self.lock:
                    account['stage'] = field
                records[field] = getattr(provider, 'get_' + field)(user)
                db = LocalDatabase(self.db_path(user))
                db.upsert_tls_snapshot(user, records['courses'], records['assignments'],
                    records.get('lectures', db.get_lectures(user)), user, None,
                    notices=records.get('notices'))
                db.close()
                db = None
                os.chmod(self.db_path(user), 0o600)
                with self.lock:
                    account['syncedAt'] = datetime.now(timezone.utc).isoformat()
                    account['availableSections'] = list(records)
        except Exception:
            # Never return exception text: upstream errors can contain URLs or personal data.
            with self.lock:
                account['error'] = 'TLS 동기화 실패. 서버가 응답하지 않거나 로그인이 만료됐을 수 있습니다. 다시 연결해 주세요.'
        finally:
            if db:
                db.close()
            with self.lock:
                account['running'] = False
                account['stage'] = None

    def __call__(self, env, start_response):
        headers = [('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'),
                   ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
                   ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")]
        try:
            status, content, kind, extra = self.route(env)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            status, content, kind, extra = 400, {'error': 'invalid_request'}, 'application/json', []
        except Exception:
            status, content, kind, extra = 500, {'error': 'internal_error'}, 'application/json', []
        payload = json.dumps(content, ensure_ascii=False).encode() if kind == 'application/json' else content.encode()
        start_response(f'{status} {HTTPStatus(status).phrase}', headers + [('Content-Type', kind + '; charset=utf-8'), ('Content-Length', str(len(payload)))] + extra)
        return [payload]

    def route(self, env):
        method, path = env['REQUEST_METHOD'], env.get('PATH_INFO', '/')
        query = {key: values[-1] for key, values in parse_qs(env.get('QUERY_STRING', ''), keep_blank_values=True).items()}
        def reply(data, status=200, extra=None):
            return status, data, 'application/json', extra or []
        body = {}
        if method == 'POST':
            length = int(env.get('CONTENT_LENGTH') or 0)
            if length < 0 or length > 16384:
                return reply({'error': 'request_too_large'}, 413)
            raw = env['wsgi.input'].read(length).decode()
            if env.get('CONTENT_TYPE', '').split(';')[0] == 'application/json':
                body = json.loads(raw)
                if not isinstance(body, dict) or any(not isinstance(v, str) for v in body.values()):
                    raise ValueError('Expected string fields')
            else:
                body = {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}
        if path == '/health' and method == 'GET':
            return reply({'status': 'ok'})
        if path == '/privacy' and method == 'GET':
            return 200, '<h1>University Agent 개인정보 안내</h1><p>TLS 아이디·비밀번호는 로그인 확인을 위해 학교 TLS로 전송됩니다. 비밀번호는 저장하지 않습니다. 로그인 쿠키는 서버 메모리에만 보관합니다. 과목·과제·강의 진도·공지는 사용자별 서버 DB에 저장되며, 요청한 데이터가 ChatGPT로 전달됩니다. 연결 해제는 토큰을 폐기합니다. 저장 데이터 삭제는 서비스 운영자에게 요청하세요.</p>', 'text/html', []
        if path == '/openapi.json' and method == 'GET':
            schema = json.loads((ROOT / 'server/openapi.json').read_text())
            schema['servers'] = [{'url': self.base_url}]
            schema['components']['securitySchemes']['oauth']['flows']['authorizationCode'].update(authorizationUrl=self.base_url + '/oauth/authorize', tokenUrl=self.base_url + '/oauth/token')
            return reply(schema)
        if path == '/oauth/authorize' and method == 'GET':
            if query.get('client_id') != self.client_id or query.get('redirect_uri') not in self.redirects or query.get('response_type') != 'code' or not query.get('state'):
                return reply({'error': 'invalid_request'}, 400)
            if len(query['state']) > 2048 or query.get('scope', 'academic:read') != 'academic:read':
                return reply({'error': 'invalid_scope'}, 400)
            if query.get('code_challenge') and (query.get('code_challenge_method') != 'S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}', query['code_challenge'])):
                return reply({'error': 'invalid_request'}, 400)
            with self.lock:
                if len(self.flows) > 10000:
                    return reply({'error': 'busy'}, 429)
            ticket = self.issue(self.flows, query, 600)
            return 200, self.login_form(ticket), 'text/html', []
        if path == '/oauth/login' and method == 'POST':
            flow = self.take(self.flows, body.get('ticket', ''), consume=True)
            if not flow:
                return reply({'error': 'expired_login', 'message': 'ChatGPT에서 다시 로그인해 주세요.'}, 400)
            origin = env.get('HTTP_ORIGIN')
            if origin and origin != self.base_url:
                return reply({'error': 'invalid_origin'}, 403)
            username, password = body.get('username', '').strip(), body.get('password', '')
            if not username or not password or len(username) > 128 or len(password) > 1024:
                return reply({'error': 'invalid_credentials'}, 400)
            with self.lock:
                now = time.time()
                self.attempts = {key: value for key, value in self.attempts.items() if value[1] > now}
                count, expires = self.attempts.get(digest(username), (0, now + 600))
                if count >= 10 or len(self.attempts) >= 10000:
                    return reply({'error': 'too_many_attempts'}, 429)
                self.attempts[digest(username)] = (count + 1, expires)
            session = self.session_factory()
            try:
                session.login(username, password)
            except Exception:
                ticket = self.issue(self.flows, flow, 600)
                return 401, self.login_form(ticket, '로그인 실패 또는 서버 응답 지연. 입력을 확인하고 다시 시도해 주세요.'), 'text/html', []
            finally:
                password = None
                body.clear()
            with self.lock:
                old = self.accounts.get(username)
                if old and old['running']:
                    # Keep the session used by an in-flight sync; replace it after it finishes on next login.
                    account = old
                else:
                    account = {'session': session, 'running': False, 'syncedAt': old['syncedAt'] if old else None, 'error': None, 'stage': None, 'availableSections': old.get('availableSections', []) if old else []}
                    self.accounts[username] = account
            self.sync(username)
            code = self.issue(self.codes, {'user': username, 'redirect_uri': flow['redirect_uri'], 'challenge': flow.get('code_challenge')}, 120)
            location = flow['redirect_uri'] + '?' + urlencode({'code': code, 'state': flow['state']})
            return 302, '', 'text/html', [('Location', location)]
        if path == '/oauth/token' and method == 'POST':
            client_id, client_secret = body.get('client_id', ''), body.get('client_secret', '')
            auth = env.get('HTTP_AUTHORIZATION', '')
            if auth.startswith('Basic '):
                client_id, client_secret = base64.b64decode(auth[6:], validate=True).decode().split(':', 1)
            if not hmac.compare_digest(client_id, self.client_id) or not hmac.compare_digest(client_secret, self.client_secret):
                return reply({'error': 'invalid_client'}, 401)
            grant = body.get('grant_type')
            if grant == 'authorization_code':
                item = self.take(self.codes, body.get('code', ''), consume=True)
                if not item or item['redirect_uri'] != body.get('redirect_uri'):
                    return reply({'error': 'invalid_grant'}, 400)
                if item.get('challenge'):
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(body.get('code_verifier', '').encode()).digest()).decode().rstrip('=')
                    if not hmac.compare_digest(challenge, item['challenge']):
                        return reply({'error': 'invalid_grant'}, 400)
            elif grant == 'refresh_token':
                item = self.take(self.tokens, body.get('refresh_token', ''), consume=True)
                if not item or item['kind'] != 'refresh':
                    return reply({'error': 'invalid_grant'}, 400)
            else:
                return reply({'error': 'unsupported_grant_type'}, 400)
            access = self.issue(self.tokens, {'user': item['user'], 'kind': 'access'}, 3600)
            refresh = self.issue(self.tokens, {'user': item['user'], 'kind': 'refresh'}, 30 * 86400)
            return reply({'access_token': access, 'token_type': 'Bearer', 'expires_in': 3600, 'refresh_token': refresh, 'scope': 'academic:read'})
        auth = env.get('HTTP_AUTHORIZATION', '')
        token = self.take(self.tokens, auth[7:]) if auth.startswith('Bearer ') else None
        if not token or token['kind'] != 'access':
            return reply({'error': 'unauthorized'}, 401, [('WWW-Authenticate', 'Bearer')])
        user = token['user']
        with self.lock:
            account = self.accounts.get(user)
            if not account:
                return reply({'error': 'reconnect_required'}, 401)
            status = {key: account[key] for key in ('running', 'stage', 'syncedAt', 'error', 'availableSections')}
        if path == '/v1/sync' and method == 'POST':
            self.sync(user)
            return reply({'status': 'sync_started', 'message': '동기화 중입니다. getSyncStatus로 확인해 주세요.'}, 202)
        if path == '/v1/sync' and method == 'GET':
            return reply(status)
        if path == '/v1/disconnect' and method == 'POST':
            with self.lock:
                self.tokens = {key: item for key, item in self.tokens.items() if item['user'] != user}
                self.codes = {key: item for key, item in self.codes.items() if item['user'] != user}
                self.accounts.pop(user, None)
            return reply({'status': 'disconnected'})
        readers = {'/v1/courses': 'get_courses', '/v1/assignments': 'get_assignments', '/v1/lectures': 'get_lectures', '/v1/notices': 'get_notices', '/v1/todos': 'get_todos'}
        if path not in readers or method != 'GET':
            return reply({'error': 'not_found'}, 404)
        required = {'/v1/courses': {'courses'}, '/v1/assignments': {'assignments'}, '/v1/lectures': {'lectures'}, '/v1/notices': {'notices'}, '/v1/todos': {'assignments', 'lectures'}}[path]
        if not required.issubset(status['availableSections']):
            return reply({'error': 'sync_pending' if status['running'] else 'sync_failed', 'sync': status}, 503, [('Retry-After', '5')])
        db = LocalDatabase(self.db_path(user), read_only=True)
        try:
            if path == '/v1/assignments':
                if query.get('unsubmitted', 'false') not in ('true', 'false'):
                    raise ValueError('Invalid boolean')
                data = get_assignments(db, user, unsubmitted=query.get('unsubmitted') == 'true')
                for key, operation in [('due_from', lambda a, b: a >= b), ('due_before', lambda a, b: a < b)]:
                    if key in query:
                        bound = datetime.fromisoformat(query[key].replace('Z', '+00:00'))
                        if bound.tzinfo is None:
                            raise ValueError('Timezone required')
                        data = [row for row in data if row['dueAt'] and operation(datetime.fromisoformat(row['dueAt'].replace('Z', '+00:00')), bound)]
            elif path == '/v1/lectures':
                if query.get('unfinished', 'false') not in ('true', 'false'):
                    raise ValueError('Invalid boolean')
                data = get_lectures(db, user, unfinished=query.get('unfinished') == 'true')
            else:
                data = getattr(db, readers[path])(user)
                if path == '/v1/todos':
                    data = [{**item, 'courseId': course['courseId'], 'courseName': course['courseName']} for course in data for item in course['items']]
            offset, limit = int(query.get('offset', 0)), int(query.get('limit', 20))
            if offset < 0 or not 1 <= limit <= 50:
                raise ValueError('Invalid pagination')
            page = data[offset:offset + limit]
            if path == '/v1/notices':
                page = [{**row, 'content': row['content'][:1000]} for row in page]
            while page and len(json.dumps(page, ensure_ascii=False).encode()) > 80000:
                page.pop()
            if data[offset:offset + limit] and not page:
                return reply({'error': 'item_too_large'}, 503)
            limit = len(page)
            return reply({'data': page, 'total': len(data), 'nextOffset': offset + limit if offset + limit < len(data) else None, 'sync': status})
        finally:
            db.close()

    @staticmethod
    def login_form(ticket, error=''):
        return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>University Agent 로그인</title><style>body{{font:18px system-ui;max-width:420px;margin:40px auto;padding:20px}}input,button{{box-sizing:border-box;width:100%;padding:14px;margin:8px 0 20px;font:inherit}}button{{background:#173f35;color:white;border:0;border-radius:8px}}</style><h1>University Agent</h1><p>학교 TLS에 로그인하고, 과목·과제·강의 진도·공지를 ChatGPT에서 조회하도록 허용합니다. 비밀번호는 저장하지 않습니다.</p><p role="alert">{escape(error)}</p><form method="post" action="/oauth/login"><input type="hidden" name="ticket" value="{escape(ticket)}"><label for="username">TLS 아이디</label><input id="username" name="username" autocomplete="username" maxlength="128" required><label for="password">비밀번호</label><input id="password" name="password" type="password" autocomplete="current-password" maxlength="1024" required><button type="submit">로그인하고 ChatGPT 연결</button></form><p>연결 후 첫 조회까지 동기화 시간이 필요할 수 있습니다.</p><a href="/privacy">개인정보 안내</a></html>'''


def create_app():
    return Gateway(os.environ['PUBLIC_URL'], os.environ['CLIENT_ID'], os.environ['CLIENT_SECRET'], [uri.strip() for uri in os.environ['REDIRECT_URIS'].split(',')], os.environ.get('DATA_DIR', '/data/university-agent'))
