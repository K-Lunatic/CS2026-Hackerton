"""Small Moodle session client; credentials and cookies stay in memory."""
from __future__ import annotations

import gzip
import json
import re
import ssl
import sys
import zlib
from copy import copy
from urllib.error import HTTPError
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPCookieProcessor, HTTPSHandler


class LoginError(RuntimeError):
    pass


class DownloadRestricted(RuntimeError):
    pass


def _default_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    has_trust_store = ((paths.cafile and Path(paths.cafile).is_file()) or
                       (paths.capath and Path(paths.capath).is_dir()))
    if not has_trust_store and sys.platform == "darwin":
        for candidate in ("/private/etc/ssl/cert.pem", "/etc/ssl/cert.pem"):
            if Path(candidate).is_file():
                context.load_verify_locations(cafile=candidate)
                break
    return context


def check_download_url(url: str) -> None:
    query = {key.lower(): values for key, values in parse_qs(urlsplit(url).query).items()}
    denied = any(value.lower() in {"1", "true", "yes"} for key in ("nodownload", "disable_download", "disabledownload") for value in query.get(key, []))
    denied |= any(value.lower() in {"0", "false", "no"} for key in ("allowdownload", "allow_download") for value in query.get(key, []))
    if denied:
        raise DownloadRestricted("파일 주소에 다운로드 금지가 표시되어 파일을 가져오지 않았습니다.")
    # A viewer URL alone is not a prohibition. The course page's visible text
    # is checked by the Moodle adapter; forcedownload=0 also only means inline display.


def check_download_origin(url, origin):
    if not origin:
        return
    try:
        parts = urlsplit(url)
        allowed = (parts.scheme == 'https' and parts.hostname == origin and
                   parts.port in (None, 443) and not parts.username and not parts.password)
    except ValueError:
        allowed = False
    if not allowed:
        raise DownloadRestricted('학교 자료 주소가 달라져 가져오기를 멈췄어요.')


class _DownloadRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            check_download_url(newurl)
            check_download_origin(newurl, getattr(req, 'allowed_origin', None))
        except DownloadRestricted:
            fp.close()
            raise
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.allowed_origin = getattr(req, 'allowed_origin', None)
        return redirected


class _HiddenInputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}
        self.action: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and self.action is None:
            self.action = values.get("action")
        if tag == "input" and values.get("type", "").lower() == "hidden" and values.get("name"):
            self.values[values["name"]] = values.get("value") or ""


class MoodleSession:
    def __init__(self, base_url: str = "https://tls.kku.ac.kr"):
        self.base_url = base_url.rstrip("/")
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), _DownloadRedirectHandler(), HTTPSHandler(context=_default_ssl_context()))
        self.logged_in = False

    def fork(self):
        """Independent cookie jar/opener per read worker; login remains single-threaded."""
        child = MoodleSession(self.base_url)
        for cookie in self.cookies:
            child.cookies.set_cookie(copy(cookie))
        child.logged_in = self.logged_in
        return child

    def revalidate(self, path, cached):
        headers = {name: cached[key] for name, key in
                   (('If-None-Match', 'etag'), ('If-Modified-Since', 'lastModified')) if cached.get(key)}
        return self.get_bytes(path, conditional=headers, allowed_origin=urlsplit(self.base_url).hostname)

    @staticmethod
    def _decode_bytes(payload: bytes, response: Any) -> bytes:
        encoding = response.headers.get("Content-Encoding", "").lower()
        if "gzip" in encoding:
            return gzip.decompress(payload)
        elif "deflate" in encoding:
            return zlib.decompress(payload)
        return payload

    @classmethod
    def _decode(cls, response: Any) -> str:
        return cls._decode_bytes(response.read(), response).decode("utf-8", errors="replace")

    def _request(self, path: str, *, data: bytes | None = None, referer: str | None = None, allowed_origin=None, page_only=False) -> tuple[str, Any]:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        check_download_origin(url, allowed_origin)
        if data is None:
            check_download_url(url)
        headers = {
            "User-Agent": "UniversityAgent/0.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        }
        if data is not None:
            headers.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": self.base_url, "Referer": referer or url})
        request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        request.allowed_origin = allowed_origin
        response = self.opener.open(request, timeout=30)
        try:
            check_download_origin(response.geturl(), allowed_origin)
            if data is None:
                check_download_url(response.geturl())
            content_type = response.headers.get('Content-Type', '').split(';', 1)[0].lower().strip()
            # File redirects are not HTML notices. Do not transfer an entire
            # cached PDF merely to look for a download prohibition in a page.
            binary_file = content_type.startswith(('application/pdf', 'application/octet-stream',
                'application/msword', 'application/vnd.', 'application/x-hwp', 'image/', 'video/', 'audio/'))
            binary_file |= content_type in ('application/zip', 'application/x-zip-compressed', 'application/x-7z-compressed', 'application/x-rar-compressed')
            html = '' if data is None and (binary_file or (page_only and content_type not in ('text/html', 'application/xhtml+xml'))) else self._decode(response)
        finally:
            response.close()
        if self.logged_in and ("/login" in urlsplit(response.geturl()).path or re.search(r'name=["\']username["\']', html, re.I)):
            raise LoginError("TLS session expired; sign in again")
        return html, response

    def login(self, username: str, password: str) -> None:
        login_path = "/login/index.php"
        login_html, login_response = self._request(login_path)
        parser = _HiddenInputs()
        parser.feed(login_html)
        form_path = parser.action or login_path
        fields = {**parser.values, "username": username, "password": password}
        html, response = self._request(form_path, data=urlencode(fields).encode(), referer=login_response.geturl())
        has_cookie = any(cookie.name == "MoodleSession" for cookie in self.cookies)
        still_login_form = bool(re.search(r'name=["\']username["\']', html, re.I))
        if not has_cookie or still_login_form or "/login" in response.geturl():
            code = parse_qs(urlsplit(response.geturl()).query).get("errorcode", [None])[0]
            detail = f" (Moodle errorcode={code})" if code else ""
            raise LoginError(f"TLS login failed{detail}; check credentials or Moodle login flow")
        self.logged_in = True

    def get(self, path: str) -> str:
        return self.get_page(path)[0]

    def get_page(self, path: str, *, allowed_origin=None) -> tuple[str, str]:
        if not self.logged_in:
            raise LoginError("Call login() before get()")
        html, response = self._request(path, allowed_origin=allowed_origin)
        return html, response.geturl()

    def get_page_info(self, path: str, *, allowed_origin=None):
        if not self.logged_in:
            raise LoginError('Call login() before get()')
        html, response = self._request(path, allowed_origin=allowed_origin, page_only=True)
        return html, response.geturl(), response.headers

    def document_state(self, viewer_url):
        """The viewer's read-only status request; never start file conversion/upload."""
        if not self.logged_in:
            raise LoginError('Call login() before get()')
        check_download_origin(viewer_url, 'tls.kku.ac.kr')
        check_download_url(viewer_url)
        parts = urlsplit(viewer_url)
        query = parse_qs(parts.query, keep_blank_values=True)
        if (parts.path != '/local/ubdoc/' or parts.fragment or set(query) != {'id', 'tp', 'pg'} or
            any(len(values) != 1 for values in query.values()) or not re.fullmatch(r'[1-9][0-9]*', query['id'][0]) or
            query['tp'] != ['m'] or query['pg'] != ['ubfile']):
            raise ValueError('문서 상태를 확인할 주소가 올바르지 않아요.')
        fields = {key: values[0] for key, values in query.items()}
        html, _ = self._request('/local/ubdoc/worker.php',
            data=urlencode({**fields, 'job': 'checkState', 'item': '', 'fid': ''}).encode(),
            referer=viewer_url, allowed_origin='tls.kku.ac.kr')
        try:
            value = json.loads(html)
        except ValueError as error:
            raise ValueError('학교의 문서 상태 응답을 읽지 못했어요.') from error
        if not isinstance(value, dict):
            raise ValueError('학교의 문서 상태 응답 형식이 달라요.')
        return {key: value.get(key) for key in ('state_code', 'file_download')}

    def get_bytes(self, path: str, *, conditional=None, allowed_origin=None) -> tuple[bytes | None, Any]:
        if not self.logged_in:
            raise LoginError("Call login() before get_bytes()")
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        check_download_url(url)
        check_download_origin(url, allowed_origin)
        try:
            request = Request(url, headers={"User-Agent": "UniversityAgent/0.1", "Accept-Encoding": "gzip, deflate", **(conditional or {})})
            request.allowed_origin = allowed_origin
            response = self.opener.open(request, timeout=30)
        except HTTPError as error:
            if error.code != 304:
                error.close()
                raise
            try:
                check_download_url(error.geturl())
                check_download_origin(error.geturl(), allowed_origin)
                return None, error
            finally:
                error.close()
        try:
            check_download_url(response.geturl())
            check_download_origin(response.geturl(), allowed_origin)
            if "/login" in urlsplit(response.geturl()).path:
                raise LoginError("TLS session expired; sign in again")
            return self._decode_bytes(response.read(), response), response
        finally:
            response.close()
