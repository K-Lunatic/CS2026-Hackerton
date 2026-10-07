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
import tempfile
from email.parser import BytesParser
from email.policy import default as email_default
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
from features.assignment_selection import find_assignments, public_checkpoint, normalize, selection_command
from features.context_bookmarks import get_context_bookmark, list_unfinished_context_bookmarks, save_context_bookmark
from features.context_commands import parse_context_command, next_commands
from server import study_bridge


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
        self.flows, self.codes, self.tokens, self.accounts, self.attempts, self.study_links = {}, {}, {}, {}, {}, {}
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
                   ('Content-Security-Policy', "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")]
        try:
            status, content, kind, extra = self.route(env)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            status, content, kind, extra = 400, {'error': 'invalid_request'}, 'application/json', []
        except Exception:
            status, content, kind, extra = 500, {'error': 'internal_error'}, 'application/json', []
        payload = json.dumps(content, ensure_ascii=False).encode() if kind == 'application/json' else content if isinstance(content, bytes) else content.encode()
        start_response(f'{status} {HTTPStatus(status).phrase}', headers + [('Content-Type', kind + '; charset=utf-8'), ('Content-Length', str(len(payload)))] + extra)
        return [payload]

    def route(self, env):
        method, path = env['REQUEST_METHOD'], env.get('PATH_INFO', '/')
        query = {key: values[-1] for key, values in parse_qs(env.get('QUERY_STRING', ''), keep_blank_values=True).items()}
        def reply(data, status=200, extra=None):
            if path.startswith('/v1/') and path != '/v1/disconnect' and 200 <= status < 300 and isinstance(data, dict):
                calls = ['list_context_bookmarks'] if path == '/v1/checkpoints' else []
                data = {**data, 'nextCommands': next_commands({'toolCalls': calls, 'data': data.get('data')})}
            return status, data, 'application/json', extra or []
        mobile = re.fullmatch(r'/study/([A-Za-z0-9_-]{43})(?:/(.*))?', path)
        if mobile:
            return self.mobile_study_route(env, mobile.group(1), mobile.group(2) or '')
        body = {}
        if method == 'POST':
            length = int(env.get('CONTENT_LENGTH') or 0)
            limit = 2_000_000 if path in {'/v1/study/exam', '/v1/study/grade'} else 16384
            if length < 0 or length > limit:
                return reply({'error': 'request_too_large'}, 413)
            raw = env['wsgi.input'].read(length).decode()
            if env.get('CONTENT_TYPE', '').split(';')[0] == 'application/json':
                body = json.loads(raw)
                structured = path in {'/v1/checkpoint', '/v1/study/exam', '/v1/study/grade'}
                if not isinstance(body, dict) or (not structured and any(not isinstance(value, str) for value in body.values())):
                    raise ValueError('Expected object')
            else:
                body = {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}
        if path == '/health' and method == 'GET':
            return reply({'status': 'ok'})
        if path == '/privacy' and method == 'GET':
            return 200, '<h1>University Agent 개인정보 안내</h1><p>TLS 아이디·비밀번호는 로그인 확인을 위해 학교 TLS로 전송됩니다. 비밀번호는 저장하지 않습니다. 로그인 쿠키는 서버 메모리에만 보관합니다. 과목·과제·강의 진도·공지와 사용자가 저장한 과제 진행 기록은 사용자별 서버 DB에 저장되며, 요청한 데이터가 ChatGPT로 전달됩니다. 과제 완료 시 해당 진행 기록은 삭제됩니다. 연결 해제는 토큰을 폐기합니다. 다른 저장 데이터 삭제는 서비스 운영자에게 요청하세요.</p>', 'text/html', []
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
                self.study_links = {key: item for key, item in self.study_links.items() if item['user'] != user}
                self.accounts.pop(user, None)
            return reply({'status': 'disconnected'})
        if path == '/v1/study-pack/link' and method == 'POST':
            ticket = self.issue(self.study_links, {'user': user}, 1800)
            return reply({'url': self.base_url + '/study/' + ticket, 'expiresIn': 1800,
                          'message': '모바일에서 이 링크를 열고 .tpack 파일을 선택하세요.'})
        if path == '/v1/study/analysis' and method == 'GET':
            return reply({'data': study_bridge.analysis_catalog(self.data_dir, self.db_path, user)})
        if path == '/v1/study/exam' and method == 'POST':
            conversation = body.get('conversation', '').strip()
            if not conversation:
                raise ValueError('대화별 세션 키가 필요합니다.')
            return reply({'data': study_bridge.save_generated_exam(self.data_dir, self.db_path, user, conversation, body)})
        if path == '/v1/study/status' and method == 'POST':
            conversation = body.get('conversation', '').strip()
            current = study_bridge.session(self.data_dir, self.db_path, user, conversation)
            try:
                return reply({'data': current.call({'action': 'status'})})
            finally:
                study_bridge.close_session(current)
        if path == '/v1/study/grade' and method == 'POST':
            conversation = body.get('conversation', '').strip()
            return reply({'data': study_bridge.grade(self.data_dir, self.db_path, user, conversation, body)})
        if path in {'/v1/checkpoints', '/v1/checkpoint', '/v1/assignments/complete'}:
            if 'assignments' not in status['availableSections']:
                return reply({'error': 'sync_pending' if status['running'] else 'sync_failed', 'sync': status}, 503, [('Retry-After', '5')])
            return self.checkpoint_route(path, method, query, body, user, reply)
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

    def checkpoint_route(self, path, method, query, body, user, reply):
        if path == '/v1/checkpoints' and method == 'GET':
            if query not in ({'userMessage': 'list'}, {'userMessage': '과제 목록 불러오기'}):
                return reply({'error': 'exact_command_required', 'requiredMessage': 'list'}, 400)
            db = LocalDatabase(self.db_path(user), read_only=True)
            try:
                records = list_unfinished_context_bookmarks(db, user_id=user, db_path=self.db_path(user))
                return reply({'data': [public_checkpoint(record) for record in records], 'total': len(records)})
            finally:
                db.close()
        if path == '/v1/assignments/complete' and method == 'POST':
            if set(body) - {'assignmentId', 'submissionAnswer'} or not isinstance(body.get('assignmentId'), str):
                return reply({'error': 'invalid_request'}, 400)
            if body.get('submissionAnswer') not in ('예', '아니요'):
                return reply({'error': 'exact_submission_answer_required', 'message': '예 또는 아니요로만 답해 주세요.'}, 400)
            if body['submissionAnswer'] == '아니요':
                return reply({'completed': False, 'message': '아직 제출하지 않은 과제로 기록을 유지했습니다.'})
            db = LocalDatabase(self.db_path(user))
            try:
                done = db.complete_manual_assignment(user, body['assignmentId'], submission_answer='예')
                return reply({'completed': done, 'message': '완료 처리했고 저장 목록에서 제거했습니다.' if done else '직접 등록한 과제를 찾지 못했습니다.'}, 200 if done else 404)
            finally:
                db.close()
        if path != '/v1/checkpoint' or method not in {'GET', 'POST'}:
            return reply({'error': 'not_found'}, 404)
        command_text = query.get('command') if method == 'GET' else body.get('command')
        if not isinstance(command_text, str):
            return reply({'error': 'exact_command_required'}, 400)
        command = parse_context_command(command_text)
        expected = 'load' if method == 'GET' else 'save'
        if not command or command.get('operation') != expected or 'error' in command:
            return reply({'error': 'exact_command_required'}, 400)
        db = LocalDatabase(self.db_path(user), read_only=method == 'GET')
        try:
            values = command['values']
            assignment = None
            if 'newTitle' in values:
                title = values['newTitle'].strip()
                if not title or len(title) > 200:
                    return reply({'error': 'invalid_title'}, 400)
                manual = [item for item in find_assignments(db, user, {'title': title, 'source': 'manual'})
                          if normalize(item['title']) == normalize(title)]
                if len(manual) > 1:
                    return reply({'error': 'ambiguous_assignment',
                                  'candidates': [{'courseName': item['courseName'], 'title': item['title'], 'dueAt': item.get('dueAt'),
                                                  'command': selection_command(item, 'save')} for item in manual]}, 409)
                if manual:
                    assignment = manual[0]
                elif find_assignments(db, user, {'title': title, 'source': 'tls'}):
                    return reply({'error': 'assignment_exists', 'message': 'TLS 과제는 save "과제명"으로 선택해 주세요.'}, 409)
            elif values:
                matches = find_assignments(db, user, values, prefer_exact_title=True)
                if len(matches) != 1:
                    return reply({'error': 'ambiguous_assignment' if matches else 'assignment_not_found',
                                  'candidates': [{'courseName': item['courseName'], 'title': item['title'], 'dueAt': item.get('dueAt'),
                                                  'command': selection_command(item, expected)}
                                                 for item in matches]}, 409)
                assignment = matches[0]
                if assignment['submissionStatus'] in {'SUBMITTED', 'LATE'}:
                    return reply({'data': None}) if method == 'GET' else reply({'error': 'assignment_completed'}, 409)
            if method == 'GET':
                if set(query) != {'command'}:
                    return reply({'error': 'invalid_request'}, 400)
                record = (get_context_bookmark(user_id=user, assignment_id=assignment['id'], db_path=self.db_path(user))
                          if assignment else next(iter(list_unfinished_context_bookmarks(db, user_id=user, db_path=self.db_path(user))), None))
                return reply({'data': public_checkpoint(record)})
            if set(body) - {'command', 'progress', 'blocker', 'nextAction', 'completedItems'}:
                return reply({'error': 'invalid_request'}, 400)
            for field in ('progress', 'blocker', 'nextAction'):
                if not isinstance(body.get(field), str) or not body[field].strip() or len(body[field]) > 4000:
                    return reply({'error': 'invalid_summary'}, 400)
            items = body.get('completedItems', [])
            if not isinstance(items, list) or len(items) > 50 or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in items):
                return reply({'error': 'invalid_summary'}, 400)
            if assignment is None:
                if 'newTitle' not in values:
                    return reply({'error': 'assignment_required'}, 400)
                assignment = db.add_manual_assignment(user, values['newTitle'])
                assignment['courseName'] = '기타 과제'
            record = save_context_bookmark(assignment, user_id=user, progress=body['progress'],
                blocker=body['blocker'], next_action=body['nextAction'], completed_items=items,
                db_path=self.db_path(user))
            return reply({'data': public_checkpoint(record), 'message': '진행 기록을 저장했습니다.'}, 201)
        finally:
            db.close()

    @staticmethod
    def _mobile_conversation(ticket):
        return 'mobile-' + digest(ticket)[:32]

    def mobile_study_route(self, env, ticket, tail):
        item = self.take(self.study_links, ticket)
        if not item:
            return 404, '이 학습 팩 연결은 만료됐어요. ChatGPT에서 새 링크를 받아 주세요.', 'text/html', []
        user, method = item['user'], env['REQUEST_METHOD']
        base = '/study/' + ticket
        if not tail or tail == 'upload':
            if method == 'GET':
                try:
                    exams = study_bridge.history(self.data_dir, self.db_path, user, self._mobile_conversation(ticket)).get('exams', [])
                except (OSError, ValueError):
                    exams = []
                return 200, self.mobile_portal(ticket, exams), 'text/html', []
            if method != 'POST':
                return 405, '지원하지 않는 요청입니다.', 'text/plain', []
            content_type = env.get('CONTENT_TYPE', '')
            if not content_type.startswith('multipart/form-data;'):
                return 400, '학습 팩 파일을 선택해 주세요.', 'text/plain', []
            length = int(env.get('CONTENT_LENGTH') or 0)
            if length <= 0 or length > study_pack.MAX_PACK_BYTES + 1_000_000:
                return 413, '학습 팩이 너무 커요.', 'text/plain', []
            raw = env['wsgi.input'].read(length)
            message = BytesParser(policy=email_default).parsebytes(
                b'Content-Type: ' + content_type.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + raw)
            upload = next((part for part in message.walk()
                           if part.get_content_disposition() == 'form-data'
                           and part.get_param('name', header='content-disposition') == 'pack'), None)
            if not upload or not upload.get_filename():
                return 400, '학습 팩 파일을 선택해 주세요.', 'text/plain', []
            root = study_bridge.root_for(self.data_dir, user)
            descriptor, name = tempfile.mkstemp(prefix='.upload-', suffix='.tpack', dir=root)
            try:
                with os.fdopen(descriptor, 'wb') as target:
                    target.write(upload.get_payload(decode=True) or b'')
                study_bridge.import_pack(self.data_dir, self.db_path, user, Path(name))
            finally:
                Path(name).unlink(missing_ok=True)
            return 303, '', 'text/html', [('Location', base)]
        if tail == 'open' and method == 'POST':
            length = int(env.get('CONTENT_LENGTH') or 0)
            if length <= 0 or length > 4096:
                return 413, '시험 선택을 확인해 주세요.', 'text/plain', []
            fields = {key: values[-1] for key, values in parse_qs(env['wsgi.input'].read(length).decode(), keep_blank_values=True).items()}
            exam_id = fields.get('examId', '')
            current = study_bridge.session(self.data_dir, self.db_path, user, self._mobile_conversation(ticket))
            try:
                current.call({'action': 'shuffle_exam', 'examId': exam_id})
            finally:
                study_bridge.close_session(current)
            return 303, '', 'text/html', [('Location', base + '/exam#' + ticket)]
        if tail in {'exam.css', 'exam.js', 'icon.png'}:
            return self.mobile_exam_route(env, ticket, tail)
        if tail == 'exam' or tail.startswith('exam/'):
            return self.mobile_exam_route(env, ticket, tail[5:])
        return 404, '화면을 찾을 수 없어요.', 'text/plain', []

    @staticmethod
    def mobile_portal(ticket, exams):
        rows = ''.join(
            f'<li><strong>{escape(item["title"])}</strong> · {item["questionCount"]}문항 '
            f'<form method="post" action="/study/{ticket}/open"><input type="hidden" name="examId" value="{escape(item["examId"])}"><button>이 문제로 새로 풀기</button></form></li>'
            for item in exams)
        return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>터틀넥 학습 팩</title><style>body{{font:16px system-ui;max-width:720px;margin:32px auto;padding:20px;background:#e4eee8;color:#1b1d22}}main{{background:#fcfdfb;padding:24px;border:1px solid #d3ddd7;border-radius:10px}}button{{padding:12px 16px;border:0;border-radius:6px;background:#1b1d22;color:white;font:inherit}}li{{margin:14px 0;list-style:none;border-bottom:1px solid #d3ddd7;padding-bottom:14px}}ul{{padding:0}}input{{margin:12px 0}}</style><main><h1>터틀넥 학습 팩</h1><p>이 기기에만 학습 자료와 풀이 기록을 저장해요. 원본 강의 파일과 학교 로그인 정보는 들어오지 않습니다.</p><form method="post" action="/study/{ticket}/upload" enctype="multipart/form-data"><input type="file" name="pack" accept=".tpack,application/zip" required><br><button>학습 팩 가져오기</button></form><h2>저장된 문제</h2><ul>{rows or '<li>아직 문제 세트가 없어요. 학습 팩을 먼저 가져와 주세요.</li>'}</ul></main></html>'''

    def mobile_exam_route(self, env, ticket, tail):
        if tail.startswith('api/') and not hmac.compare_digest(env.get('HTTP_X_EXAM_TOKEN', ''), ticket):
            return 403, {'error': '시험 링크를 다시 열어주세요.'}, 'application/json', []
        base = '/study/' + ticket + '/exam'
        if not tail:
            html = (ROOT / 'skills/university-agent/assets/exam.html').read_text(encoding='utf-8')
            html = html.replace('<html lang="ko">', f'<html lang="ko" data-api-root="{base}" data-app-root="{base}">')
            return 200, html, 'text/html', []
        assets = {'exam.css': ('exam.css', 'text/css; charset=utf-8'), 'exam.js': ('exam.js', 'text/javascript; charset=utf-8'), 'icon.png': ('turtleneck.png', 'image/png')}
        if tail in assets:
            name, content_type = assets[tail]
            return 200, (ROOT / 'skills/university-agent/assets' / name).read_bytes(), content_type, []
        if not tail.startswith('api/'):
            return 404, {'error': '화면을 찾을 수 없어요.'}, 'application/json', []
        conversation = self._mobile_conversation(ticket)
        current = study_bridge.session(self.data_dir, self.db_path, self.take(self.study_links, ticket)['user'], conversation)
        try:
            action = {'/api/exam': 'web_status', '/api/draft': 'draft', '/api/submit': 'web_submit', '/api/confusion': 'confusion_toggle', '/api/focus': 'question_focus'}.get('/' + tail)
            if not action:
                return 404, {'error': '지원하지 않는 요청입니다.'}, 'application/json', []
            if env['REQUEST_METHOD'] == 'GET':
                return 200, current.call({'action': action}), 'application/json', []
            length = int(env.get('CONTENT_LENGTH') or 0)
            if length <= 0 or length > 2_000_000 or env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
                return 400, {'error': '답안 형식을 확인해 주세요.'}, 'application/json', []
            data = json.loads(env['wsgi.input'].read(length).decode())
            if not isinstance(data, dict):
                raise ValueError('답안 형식을 확인해 주세요.')
            return 200, current.call({**data, 'action': action}), 'application/json', []
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            return 409, {'error': str(exc)}, 'application/json', []
        finally:
            study_bridge.close_session(current)

    @staticmethod
    def login_form(ticket, error=''):
        return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>University Agent 로그인</title><style>body{{font:18px system-ui;max-width:420px;margin:40px auto;padding:20px}}input,button{{box-sizing:border-box;width:100%;padding:14px;margin:8px 0 20px;font:inherit}}button{{background:#173f35;color:white;border:0;border-radius:8px}}</style><h1>University Agent</h1><p>학교 TLS에 로그인하고, 과목·과제·강의 진도·공지를 ChatGPT에서 조회하며 과제 진행 기록을 저장하도록 허용합니다. 비밀번호는 저장하지 않습니다.</p><p role="alert">{escape(error)}</p><form method="post" action="/oauth/login"><input type="hidden" name="ticket" value="{escape(ticket)}"><label for="username">TLS 아이디</label><input id="username" name="username" autocomplete="username" maxlength="128" required><label for="password">비밀번호</label><input id="password" name="password" type="password" autocomplete="current-password" maxlength="1024" required><button type="submit">로그인하고 ChatGPT 연결</button></form><p>연결 후 첫 조회까지 동기화 시간이 필요할 수 있습니다.</p><a href="/privacy">개인정보 안내</a></html>'''


def create_app():
    return Gateway(os.environ['PUBLIC_URL'], os.environ['CLIENT_ID'], os.environ['CLIENT_SECRET'], [uri.strip() for uri in os.environ['REDIRECT_URIS'].split(',')], os.environ.get('DATA_DIR', '/data/university-agent'))
