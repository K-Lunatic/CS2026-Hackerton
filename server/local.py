"""Run the gateway on this Mac and optionally start a temporary HTTPS tunnel."""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
from html import escape
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from server.app import Gateway, ROOT


class LocalServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args):
        pass  # OAuth codes and state must not enter access logs.


def local_config(directory, gpt_id):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / 'config.json'
    if path.exists():
        config = json.loads(path.read_text())
    else:
        config = {'client_id': 'university-agent', 'client_secret': secrets.token_urlsafe(48)}
    config['gpt_id'] = gpt_id
    # Private local file; do not print it or return it through an Action.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        json.dump(config, target)
    os.chmod(path, 0o600)
    return config


def start_tunnel(binary, port):
    process = subprocess.Popen([binary, 'tunnel', '--url', f'http://127.0.0.1:{port}', '--no-autoupdate'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = queue.Queue()
    def pump():
        for line in process.stdout:
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
            if match:
                lines.put(match.group())
        lines.put(None)
    threading.Thread(target=pump, daemon=True).start()
    try:
        url = lines.get(timeout=45)
        if not url:
            raise RuntimeError('HTTPS 터널 시작 실패. 네트워크와 Cloudflare 상태를 확인하세요.')
        return process, url
    except BaseException:
        process.terminate()
        process.wait(timeout=10)
        raise


def write_settings(directory, app):
    path = directory / 'settings.html'
    rows = [('Client ID', app.client_id), ('Client secret', app.client_secret),
            ('Authorization URL', app.base_url + '/oauth/authorize'), ('Token URL', app.base_url + '/oauth/token'), ('Scope', 'academic:read')]
    table = ''.join(f'<tr><th>{escape(name)}</th><td><code>{escape(value)}</code></td></tr>' for name, value in rows)
    page = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>University Agent 연결 설정</title><style>body{{font:18px system-ui;max-width:950px;margin:40px auto;padding:20px}}td,th{{text-align:left;padding:12px;overflow-wrap:anywhere}}code{{user-select:all}}a{{color:#174f3b}}</style><h1>이 Mac을 ChatGPT에 연결</h1><p>Custom GPT 편집 화면의 Actions에 아래 스키마 주소를 가져오고, 인증을 OAuth로 설정하세요.</p><p><a href="{escape(app.base_url)}/openapi.json">{escape(app.base_url)}/openapi.json</a></p><table>{table}</table><p>Token 교환: POST, application/x-www-form-urlencoded.</p><p>Instructions: 저장소의 server/gpt-instructions.md 내용을 넣으세요.</p><p>이 페이지의 Client secret은 학교 비밀번호가 아닙니다. 채팅에 보내지 마세요. 이 설정 페이지는 Mac 파일로만 저장되며 공개 API에서 제공하지 않습니다.</p><p>Mac이나 터널을 끄면 연결이 끊깁니다. 임시 터널을 다시 실행하면 주소가 바뀌므로 Actions 주소와 OAuth URL도 갱신해야 합니다.</p></html>'''
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        target.write(page)
    os.chmod(path, 0o600)
    return path


def main():
    parser = argparse.ArgumentParser(description='이 Mac에서 University Agent를 실행하고 ChatGPT HTTPS 연결을 준비합니다.')
    parser.add_argument('--gpt-id', help='저장한 Custom GPT의 g-... ID')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--public-url', help='직접 준비한 HTTPS 터널 주소; 생략하면 Quick Tunnel 실행')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    directory = ROOT / '.university-agent/chatgpt'
    saved = json.loads((directory / 'config.json').read_text()) if (directory / 'config.json').exists() else {}
    gpt_id = args.gpt_id or saved.get('gpt_id') or input('저장한 Custom GPT ID (g-...): ').strip()
    if not re.fullmatch(r'g-[A-Za-z0-9_-]+', gpt_id):
        raise SystemExit('Custom GPT를 저장한 뒤 주소에 표시되는 g-... ID를 입력하세요.')
    binary = shutil.which('cloudflared') or str(ROOT / '.university-agent/bin/cloudflared')
    if not args.public_url and not Path(binary).is_file():
        raise SystemExit('cloudflared가 없습니다. Mac에 cloudflared를 설치한 뒤 다시 실행하세요. 설치: brew install cloudflared')
    config = local_config(directory, gpt_id)
    redirects = [f'https://{host}/aip/{gpt_id}/oauth/callback' for host in ('chatgpt.com', 'chat.openai.com')]
    app = Gateway(args.public_url or 'https://localhost.invalid', config['client_id'], config['client_secret'], redirects, directory / 'data')
    httpd = make_server('127.0.0.1', args.port, app, server_class=LocalServer, handler_class=QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    tunnel = None
    try:
        if not args.public_url:
            print('HTTPS 터널 연결 중…', flush=True)
            tunnel, app.base_url = start_tunnel(binary, httpd.server_port)
        settings = write_settings(directory, app)
        print(f'로컬 실행: http://127.0.0.1:{httpd.server_port}/health', flush=True)
        print(f'ChatGPT 스키마: {app.base_url}/openapi.json', flush=True)
        print(f'비공개 설정 페이지: {settings}', flush=True)
        print('Mac을 켜 두세요. 종료: Ctrl+C', flush=True)
        if not args.no_browser:
            subprocess.run(['open', str(settings)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while thread.is_alive():
            thread.join(timeout=1)
            if tunnel and tunnel.poll() is not None:
                raise SystemExit('HTTPS 터널이 종료되어 ChatGPT 연결을 중단했습니다.')
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
            tunnel.wait(timeout=10)
        httpd.shutdown()
        httpd.server_close()


if __name__ == '__main__':
    main()
