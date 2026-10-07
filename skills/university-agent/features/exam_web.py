"""Loopback-only exam UI over the existing StudySession. AI work stays in Codex."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets


def create_exam_server(session, port=0, view='exam'):
    if view not in ('exam', 'match', 'sets'):
        raise ValueError('지원하지 않는 학습 화면입니다.')
    session.call({'action': {'exam': 'web_status', 'match': 'match_status', 'sets': 'sets_status'}[view]})
    token = secrets.token_urlsafe(32)
    assets = Path(__file__).resolve().parents[1] / 'assets'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type='application/json; charset=utf-8'):
            payload = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, dict) else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin', f'http://{host}') != f'http://{host}':
                self.send(403, {'error': '이 컴퓨터에서 연 시험 화면을 사용해주세요.'})
                return False
            if self.path.startswith('/api/') and not secrets.compare_digest(self.headers.get('X-Exam-Token', ''), token):
                self.send(403, {'error': '시험 링크를 다시 열어주세요.'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            html = {'exam': 'exam.html', 'match': 'match.html', 'sets': 'sets.html'}[view]
            files = {'/': (html, 'text/html; charset=utf-8'), '/exam.css': ('exam.css', 'text/css; charset=utf-8'),
                     '/exam.js': ('exam.js', 'text/javascript; charset=utf-8'),
                     '/match.js': ('match.js', 'text/javascript; charset=utf-8'),
                     '/sets.js': ('sets.js', 'text/javascript; charset=utf-8'),
                     '/icon.png': ('turtleneck.png', 'image/png')}
            if view == 'match': files['/match'] = ('match.html', 'text/html; charset=utf-8')
            if view == 'sets': files['/exam'] = ('exam.html', 'text/html; charset=utf-8')
            if self.path in files:
                name, content_type = files[self.path]
                self.send(200, (assets / name).read_bytes(), content_type)
            elif self.path == '/api/exam':
                try:
                    self.send(200, session.call({'action': 'web_status'}))
                except ValueError as exc:
                    self.send(409, {'error': str(exc)})
            elif self.path == '/api/match' and view == 'match':
                try:
                    self.send(200, session.call({'action': 'match_status'}))
                except ValueError as exc:
                    self.send(409, {'error': str(exc)})
            elif self.path == '/api/sets' and view == 'sets':
                try:
                    self.send(200, session.call({'action': 'sets_status'}))
                except ValueError as exc:
                    self.send(409, {'error': str(exc)})
            else:
                self.send(404, {'error': '화면을 찾을 수 없어요.'})

        def do_POST(self):
            if not self.authorized():
                return
            allowed_paths = {
                'exam': ('/api/draft', '/api/submit', '/api/confusion', '/api/focus'),
                'match': ('/api/match',),
                'sets': ('/api/sets/select',),
            }[view]
            if self.path not in allowed_paths:
                self.send(404, {'error': '지원하지 않는 요청입니다.'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2_000_000 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('답안 형식이나 크기를 확인해주세요.')
                data = json.loads(self.rfile.read(size))
                if view == 'exam':
                    if self.path == '/api/confusion':
                        if not isinstance(data, dict) or set(data) != {'examId', 'questionId', 'confused'}:
                            raise ValueError('헷갈린 문항 표시 형식을 확인해주세요.')
                        action = 'confusion_toggle'
                    elif self.path == '/api/focus':
                        if not isinstance(data, dict) or set(data) != {'examId', 'questionId'}:
                            raise ValueError('문항 시간 기록 형식을 확인해주세요.')
                        action = 'question_focus'
                    else:
                        if not isinstance(data, dict) or set(data) != {'examId', 'answers', 'revision'}:
                            raise ValueError('답안 입력 형식을 확인해주세요.')
                        action = 'draft' if self.path == '/api/draft' else 'web_submit'
                elif view == 'match':
                    if not isinstance(data, dict) or set(data) != {'matchId', 'leftId', 'rightId'}:
                        raise ValueError('매칭 입력 형식을 확인해주세요.')
                    action = 'match_pick'
                else:
                    if not isinstance(data, dict) or set(data) != {'collectionId', 'setId'}:
                        raise ValueError('문제 세트 선택 내용을 확인해주세요.')
                    action = 'set_select'
                # HTTP clients cannot call generate/grade or choose a DB/user/session.
                result = session.call({**data, 'action': action})
                self.send(200, result)
            except (ValueError, UnicodeError) as exc:
                self.send(409, {'error': str(exc)})

    # Browsers preconnect: an idle socket must not block CSS/JS/API requests.
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.timeout = 1
    return server, f'http://127.0.0.1:{server.server_port}/#{token}'
