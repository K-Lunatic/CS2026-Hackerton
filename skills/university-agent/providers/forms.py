"""Reusable secure form contract with a local terminal adapter."""
from __future__ import annotations

import sys
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from getpass import getpass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from typing import Callable, Mapping


class FormUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    secret: bool = False
    required: bool = True


@dataclass(frozen=True)
class FormDefinition:
    form_id: str
    title: str
    fields: tuple[FormField, ...]


TLS_CREDENTIALS_FORM = FormDefinition(
    form_id="tls_credentials",
    title="KKU TLS 로그인",
    fields=(
        FormField("username", "아이디"),
        FormField("password", "비밀번호", secret=True),
    ),
)


def collect_local(
    form: FormDefinition,
    *,
    initial: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
    secret_input_fn: Callable[[str], str] = getpass,
) -> dict[str, str]:
    """Collect a form without returning secret values to stdout or JSON."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise FormUnavailable(f"{form.title} 연결이 아직 필요해요. 비밀번호는 채팅에 보내지 말고 보안 입력 화면에서 입력해 주세요.")
    values = dict(initial or {})
    for field in form.fields:
        if values.get(field.name):
            continue
        reader = secret_input_fn if field.secret else input_fn
        value = reader(f"{field.label}: ")
        if field.required and not value:
            raise ValueError(f"{field.label}은(는) 필수입니다.")
        values[field.name] = value
    return values


def collect_browser(
    form: FormDefinition,
    *,
    initial: Mapping[str, str] | None = None,
    timeout: int = 600,
) -> dict[str, str]:
    """Collect secrets in a one-use localhost page, never in the model PTY."""
    token = secrets.token_urlsafe(32)
    initial_values = dict(initial or {})
    result: dict[str, str] = {}
    finished = threading.Event()

    def page(message: str = "", values: Mapping[str, str] | None = None, success: bool = False) -> str:
        values = values or {}
        fields = []
        for field in form.fields:
            value = "" if field.secret else escape(values.get(field.name, ""))
            autocomplete = "current-password" if field.secret else "username"
            fields.append(
                f'<label>{escape(field.label)}<input name="{escape(field.name)}" '
                f'type="{"password" if field.secret else "text"}" value="{value}" '
                f'autocomplete="{autocomplete}" required></label>'
            )
        body = (
            '<div class="mark">T</div><h1>터틀넥에 학교 연결하기</h1>'
            '<p>이 화면은 이 컴퓨터에서만 열려요. 입력한 비밀번호는 터틀넥 대화나 파일에 남기지 않아요.</p>'
            f'<form method="post"><input type="hidden" name="token" value="{token}">' + ''.join(fields) +
            '<button>안전하게 연결하기</button></form>'
        )
        if message:
            body = f'<div class="message {"success" if success else "error"}">{escape(message)}</div>' + body
        if success:
            body = '<div class="message success">입력을 받았어요. 학교 연결을 확인하고 있어요. 이 창을 닫아도 괜찮아요.</div><script>setTimeout(()=>window.close(),1200)</script>'
        return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(form.title)}</title><style>
        :root{{color-scheme:light}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#edf8ef;color:#17231a;font:16px system-ui,-apple-system,sans-serif}}main{{width:min(420px,calc(100% - 40px));padding:32px;border:1px solid #c8dfcc;border-radius:24px;background:#fff;box-shadow:0 16px 50px #17391c1a}}.mark{{display:grid;place-items:center;width:44px;height:44px;border-radius:50%;background:#bdebc4;color:#17391c;font-weight:800;font-size:24px}}h1{{margin:20px 0 8px;font-size:25px}}p{{margin:0 0 24px;line-height:1.6;color:#5b6c5e}}label{{display:block;margin:16px 0 6px;font-weight:650}}input{{box-sizing:border-box;width:100%;margin-top:8px;padding:13px 14px;border:1px solid #c9d8cb;border-radius:12px;font-size:16px}}button{{width:100%;margin-top:24px;padding:14px;border:0;border-radius:12px;background:#226b35;color:white;font-size:16px;font-weight:700;cursor:pointer}}.message{{margin-bottom:18px;padding:12px;border-radius:10px;line-height:1.5}}.error{{background:#fff0ed;color:#9b3424}}.success{{background:#e7f7e9;color:#226b35}}</style><main>{body}</main></html>'''

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_page(self, content: str, status: int = 200) -> None:
            payload = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/" or parse_qs(parsed.query).get("token", [""])[0] != token:
                self.send_page("<h1>잘못된 연결 화면이에요.</h1>", 404)
                return
            self.send_page(page(values=initial_values))

        def do_POST(self):
            if urlsplit(self.path).path != "/":
                self.send_page("<h1>잘못된 요청이에요.</h1>", 404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 8192:
                self.send_page(page("입력 내용이 너무 길어요."), 413)
                return
            values = {
                key: entries[-1]
                for key, entries in parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True).items()
            }
            if not secrets.compare_digest(values.pop("token", ""), token):
                self.send_page("<h1>만료된 연결 화면이에요.</h1>", 403)
                return
            missing = next((field for field in form.fields if field.required and not values.get(field.name)), None)
            if missing:
                self.send_page(page(f"{missing.label}을(를) 입력해 주세요.", values=values), 400)
                return
            result.update({field.name: values.get(field.name, "") for field in form.fields})
            self.send_page(page(success=True))
            finished.set()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.timeout = 1
    url = f"http://127.0.0.1:{httpd.server_port}/?token={token}"
    if not webbrowser.open(url):
        httpd.server_close()
        raise FormUnavailable("학교 연결 화면을 자동으로 열지 못했어요. 기본 브라우저를 확인해 주세요.")
    deadline = time.monotonic() + timeout
    try:
        while not finished.is_set() and time.monotonic() < deadline:
            httpd.handle_request()
    finally:
        httpd.server_close()
    if not result:
        raise FormUnavailable("학교 연결 입력 시간이 지나 다시 시도해 주세요.")
    return result


def collect_secure(form: FormDefinition, **kwargs) -> dict[str, str]:
    """Use the native browser when no private terminal is available."""
    if sys.stdin.isatty() and sys.stderr.isatty():
        return collect_local(form, **kwargs)
    return collect_browser(form, **kwargs)


def redact(form: FormDefinition, values: Mapping[str, str]) -> dict[str, str]:
    """Return a model/log-safe view; secret fields are never copied."""
    secret_names = {field.name for field in form.fields if field.secret}
    return {name: "[REDACTED]" if name in secret_names and value else value for name, value in values.items()}


def requested_schema(form: FormDefinition) -> dict[str, object]:
    """Return a host-neutral JSON Schema for a future ChatGPT form adapter."""
    properties = {
        field.name: {"type": "string", "title": field.label, "writeOnly": field.secret}
        for field in form.fields
    }
    return {"type": "object", "title": form.title, "properties": properties, "required": [field.name for field in form.fields if field.required]}
