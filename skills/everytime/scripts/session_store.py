"""OS-protected storage for the dedicated Everytime HTTP session."""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import subprocess
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

SERVICE = "everytime-skill/session"
STATE_PATH = Path.home() / ".everytime" / "session.json"


def _allowed_domain(domain: str) -> bool:
    host = domain.lstrip(".").lower()
    return host == "everytime.kr" or host.endswith(".everytime.kr")


def _cookie_data(jar: CookieJar) -> list[dict[str, object]]:
    return [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "secure": c.secure,
            "expires": c.expires,
        }
        for c in jar
        if _allowed_domain(c.domain)
    ]


def _cookie_jar(items: list[dict[str, object]]) -> CookieJar:
    jar = CookieJar()
    for item in items:
        if not _allowed_domain(str(item["domain"])):
            continue
        # CDP uses -1 for session cookies; CookieJar treats it as already expired.
        expiry = item.get("expires")
        expiry = int(expiry) if expiry is not None and float(expiry) >= 0 else None
        jar.set_cookie(Cookie(
            version=0,
            name=str(item["name"]),
            value=str(item["value"]),
            port=None,
            port_specified=False,
            domain=str(item["domain"]),
            domain_specified=bool(item["domain"]),
            domain_initial_dot=str(item["domain"]).startswith("."),
            path=str(item.get("path") or "/"),
            path_specified=True,
            secure=bool(item.get("secure")),
            expires=expiry,
            discard=expiry is None,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        ))
    return jar


def save(jar: CookieJar, path: Path = STATE_PATH) -> None:
    items = _cookie_data(jar)
    if not items:
        raise ValueError("인증된 Everytime 세션이 없습니다.")
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    if os.name == "nt":
        protected = base64.b64encode(_windows_protect(payload.encode())).decode("ascii")
        path.write_text(json.dumps({"protected": protected}) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        return
    path.write_text(json.dumps({"account": "default"}) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    try:
        subprocess.run(
            ("/usr/bin/security", "add-generic-password", "-U", "-s", SERVICE, "-a", "default", "-w", payload),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        path.unlink(missing_ok=True)
        raise RuntimeError("운영체제 보안 저장소에 세션을 저장하지 못했습니다.") from error


def save_browser_cookies(items: list[dict[str, object]], path: Path = STATE_PATH) -> None:
    """Save only cookies returned by the dedicated, authenticated browser session."""
    save(browser_cookie_jar(items), path)


def browser_cookie_jar(items: list[dict[str, object]]) -> CookieJar:
    allowed = [item for item in items if _allowed_domain(str(item.get("domain", "")))]
    return _cookie_jar(allowed)


def load(path: Path = STATE_PATH) -> CookieJar:
    state = json.loads(path.read_text(encoding="utf-8"))
    if os.name == "nt":
        payload = _windows_unprotect(base64.b64decode(state["protected"])).decode()
    else:
        result = subprocess.run(
            ("/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", "default", "-w"),
            check=True,
            capture_output=True,
            text=True,
        )
        payload = result.stdout.rstrip("\n")
    return _cookie_jar(json.loads(payload))


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _windows_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows 보호 저장소가 아닙니다.")
    source_buffer = (ctypes.c_byte * len(value)).from_buffer_copy(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _windows_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows 보호 저장소가 아닙니다.")
    source_buffer = (ctypes.c_byte * len(value)).from_buffer_copy(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)
