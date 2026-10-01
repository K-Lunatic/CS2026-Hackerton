#!/usr/bin/env python3
"""Local-only quiz page for one study conversation; no dependencies or schema changes.

The host AI still generates questions and grades short/essay answers through `run_agent.py study`.
This page only shows the questions and sends the student's answers to the same session.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from features.study import StudySession
from storage.local_db import LocalDatabase

ASSETS = {'/': ('web/index.html', 'text/html'), '/app.js': ('web/app.js', 'text/javascript'),
          '/style.css': ('web/style.css', 'text/css'), '/turtleneck.png': ('assets/turtleneck.png', 'image/png')}
# generate/grade events stay with the host AI; the page never receives hostOnly data.
PAGE_ACTIONS = {'hint', 'skip', 'reveal', 'submit', 'stop'}


def create_server(call, port: int = 8765) -> ThreadingHTTPServer:
    """`call(event)` runs one study event for the page's conversation."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Never log course text or answers.
            pass

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def send(self, code, body, content_type='application/json'):
            if content_type == 'application/json':
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type + ('' if content_type == 'image/png' else '; charset=utf-8'))
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def view(self):
            with lock:
                return call({'action': 'view'})

        def do_GET(self):
            if not self.valid_host():
                self.send(403, {'error': '허용되지 않은 호스트입니다.'})
                return
            path = urlsplit(self.path).path
            reply = (404, {'error': '찾을 수 없습니다.'})
            try:
                if path == '/api/quiz':
                    reply = (200, self.view())
                elif path in ASSETS:
                    name, content_type = ASSETS[path]
                    reply = (200, (ROOT / name).read_bytes(), content_type)
            except Exception:
                reply = (500, {'error': '문제를 불러오지 못했어요. 화면을 새로 고쳐 주세요.'})
            self.send(*reply)

        def do_POST(self):
            host = self.headers.get('Host', '')
            if not self.valid_host() or self.headers.get('Origin', f'http://{host}') != f'http://{host}':
                self.send(403, {'error': '같은 화면에서 요청해주세요.'})
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.send(415, {'error': 'JSON 요청이 필요합니다.'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1_000_000:
                    self.send(413, {'error': '입력 크기는 1MB 이내여야 합니다.'})
                    return
                event = json.loads(self.rfile.read(size))
                if urlsplit(self.path).path != '/api/quiz':
                    self.send(404, {'error': '찾을 수 없습니다.'})
                    return
                if not isinstance(event, dict) or event.get('action') not in PAGE_ACTIONS:
                    raise ValueError('이 화면에서 할 수 없는 요청입니다.')
                with lock:
                    call(event)
                self.send(200, self.view())
            except ValueError as exc:
                # Study errors are written for the student; the page keeps typed answers.
                self.send(422, {'error': str(exc)})
            except Exception:
                self.send(500, {'error': '처리하지 못했어요. 입력한 답은 화면에 남아 있어요. 다시 시도해 주세요.'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description='수업자료 연습문제 풀이 화면')
    parser.add_argument('--conversation', required=True, help='문제를 만든 대화의 고유 ID')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-open', action='store_true', help='브라우저를 자동으로 열지 않기')
    args = parser.parse_args()
    from run_agent import DB_PATH, USER_ID
    if not USER_ID or not DB_PATH.exists():
        raise SystemExit('아직 이 컴퓨터에 학사 정보가 없어요. 대화에서 문제를 먼저 만든 뒤 다시 열어 주세요.')

    def call(event):
        # One connection per request: handler threads cannot share a SQLite connection.
        store = LocalDatabase(DB_PATH, read_only=True)
        try:
            return StudySession(DB_PATH.parent / 'study-sessions.db', USER_ID, args.conversation,
                                store, DB_PATH.parent / 'files').call(event)
        finally:
            store.close()

    with create_server(call, args.port) as server:
        url = f'http://127.0.0.1:{server.server_port}/'
        print(f'문제 풀이 화면: {url}', flush=True)
        if not args.no_open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
