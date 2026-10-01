#!/usr/bin/env python3
"""Local-only handover screen; no dependencies or SQLite schema changes."""
from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from features.handover import HandoverError, create_handover, is_handover_request
from features.handover_review import local_analysis, make_draft

ASSETS = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}


def create_server(port: int = 8765, *, ai_call=None) -> ThreadingHTTPServer:
    sessions = {}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Never log meeting content, API credentials or session identifiers.
            pass

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def send(self, code, body, content_type='application/json'):
            if content_type == 'application/json':
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.valid_host():
                self.send(403, {'error': '허용되지 않은 호스트입니다.'})
                return
            path = urlsplit(self.path).path
            if path == '/api/config':
                self.send(200, {'aiConfigured': bool(ai_call or (os.environ.get('TEAM_HANDOVER_API_URL') and os.environ.get('TEAM_HANDOVER_MODEL')))})
            elif path in ASSETS:
                name, content_type = ASSETS[path]
                self.send(200, (ROOT / 'web' / name).read_bytes(), content_type)
            else:
                self.send(404, {'error': '찾을 수 없습니다.'})

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
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise HandoverError('요청 형식이 올바르지 않습니다.')
                path = urlsplit(self.path).path
                if path == '/api/start':
                    request = payload.get('request', '')
                    if not isinstance(request, str) or not is_handover_request(request):
                        raise HandoverError('“팀플 정리해줘” 또는 인수인계 요청으로 시작해주세요.')
                    self.send(200, {'toolCalls': ['open_handover'], 'needsInput': True, 'answer': '회의록이나 작업 기록을 붙여넣어주세요. 프로젝트와 담당자는 자료에서 확인합니다.'})
                    return
                if path == '/api/analyze':
                    text = payload.get('text', '')
                    source = payload.get('sourceName', '') or '붙여넣은 자료'
                    if not isinstance(source, str) or len(source) > 100:
                        raise HandoverError('자료 이름은 100자 이내여야 합니다.')
                    mode = payload.get('mode', 'ai')
                    if mode == 'local':
                        result = local_analysis(text)
                    elif mode == 'ai':
                        result = create_handover(text, ai_call=ai_call)
                    else:
                        raise HandoverError('분석 방식을 확인해주세요.')
                    result['sourceName'] = source.strip() or '붙여넣은 자료'
                    analysis_id = secrets.token_urlsafe(24)
                    with lock:
                        # ponytail: local in-memory sessions; add persistent review storage if durable collaboration is needed.
                        if len(sessions) >= 100:
                            raise HandoverError('분석 세션이 100개입니다. 초안을 복사하고 서버를 다시 시작해주세요.')
                        sessions[analysis_id] = deepcopy(result)
                    self.send(200, {'toolCalls': ['create_handover'], 'analysisId': analysis_id, 'data': result})
                elif path == '/api/handover':
                    analysis_id = payload.get('analysisId')
                    if not isinstance(analysis_id, str):
                        raise HandoverError('분석 결과를 먼저 생성해주세요.')
                    with lock:
                        original = sessions.get(analysis_id)
                    if original is None:
                        self.send(404, {'error': '분석 세션이 없습니다. 입력을 다시 분석해주세요.'})
                        return
                    draft = make_draft(original, payload.get('edits'), payload.get('assignee', ''))
                    self.send(200, {'draft': draft})
                else:
                    self.send(404, {'error': '찾을 수 없습니다.'})
            except HandoverError as exc:
                self.send(422, {'error': str(exc)})
            except (ValueError, TypeError):
                self.send(400, {'error': '요청 형식이 올바르지 않습니다.'})
            except Exception:
                self.send(500, {'error': '처리하지 못했습니다. 입력은 화면에 보존됩니다. 다시 시도해주세요.'})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description='팀플 진행 정리·인수인계 화면')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with create_server(args.port) as server:
        print(f'팀플 화면: http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
