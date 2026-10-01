"""Small Moodle session client; credentials and cookies stay in memory."""
from __future__ import annotations

import gzip
import re
import ssl
import zlib
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPCookieProcessor, HTTPSHandler


class LoginError(RuntimeError):
    pass


class DownloadRestricted(RuntimeError):
    pass


def check_download_url(url: str) -> None:
    query = {key.lower(): values for key, values in parse_qs(urlsplit(url).query).items()}
    denied = any(value.lower() in {"1", "true", "yes"} for key in ("nodownload", "disable_download", "disabledownload") for value in query.get(key, []))
    denied |= any(value.lower() in {"0", "false", "no"} for key in ("allowdownload", "allow_download") for value in query.get(key, []))
    if denied:
        raise DownloadRestricted("파일 주소에 다운로드 금지가 표시되어 파일을 가져오지 않았습니다.")
    # A viewer URL alone is not a prohibition. The course page's visible text
    # is checked by the Moodle adapter; forcedownload=0 also only means inline display.


class _DownloadRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            check_download_url(newurl)
        except DownloadRestricted:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), _DownloadRedirectHandler(), HTTPSHandler(context=ssl.create_default_context()))
        self.logged_in = False

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

    def _request(self, path: str, *, data: bytes | None = None, referer: str | None = None) -> tuple[str, Any]:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        headers = {
            "User-Agent": "UniversityAgent/0.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        }
        if data is not None:
            headers.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": self.base_url, "Referer": referer or url})
        response = self.opener.open(Request(url, data=data, headers=headers, method="POST" if data is not None else "GET"), timeout=30)
        html = self._decode(response)
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
        if not self.logged_in:
            raise LoginError("Call login() before get()")
        html, _ = self._request(path)
        return html

    def get_bytes(self, path: str) -> tuple[bytes, Any]:
        if not self.logged_in:
            raise LoginError("Call login() before get_bytes()")
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        check_download_url(url)
        response = self.opener.open(Request(url, headers={"User-Agent": "UniversityAgent/0.1", "Accept-Encoding": "gzip, deflate"}), timeout=30)
        try:
            check_download_url(response.geturl())
            if "/login" in urlsplit(response.geturl()).path:
                raise LoginError("TLS session expired; sign in again")
            return self._decode_bytes(response.read(), response), response
        finally:
            response.close()
