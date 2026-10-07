"""Local TLS credential storage using the OS secret store."""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import subprocess
from pathlib import Path

from providers.forms import FormUnavailable, TLS_CREDENTIALS_FORM, collect_secure

SERVICE = "university-agent/tls"
CONFIG_PATH = Path.home() / ".university-agent" / "tls-account.json"


CredentialInputRequired = FormUnavailable


def save(username: str, password: str) -> None:
    if not username or not password:
        raise ValueError("TLS username and password are required")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        protected = base64.b64encode(_windows_protect(password.encode("utf-8"))).decode("ascii")
        CONFIG_PATH.write_text(json.dumps({"username": username, "passwordProtected": protected}, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    CONFIG_PATH.write_text(json.dumps({"username": username}, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)
    try:
        subprocess.run(("/usr/bin/security", "add-generic-password", "-U", "-s", SERVICE, "-a", username, "-w", password), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        CONFIG_PATH.unlink(missing_ok=True)
        raise RuntimeError("운영체제 보안 저장소를 사용할 수 없어 비밀번호를 저장하지 못했어요.") from error


def load() -> tuple[str, str]:
    account = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    username = account["username"]
    if os.name == "nt":
        password = _windows_unprotect(base64.b64decode(account["passwordProtected"])).decode("utf-8")
        return username, password
    result = subprocess.run(("/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", username, "-w"), check=True, capture_output=True, text=True)
    return username, result.stdout.rstrip("\n")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _windows_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows secret storage is unavailable")
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
        raise RuntimeError("Windows secret storage is unavailable")
    source_buffer = (ctypes.c_byte * len(value)).from_buffer_copy(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def resolve(username: str | None = None, *, force_input: bool = False) -> tuple[str, str]:
    if force_input:
        values = collect_secure(TLS_CREDENTIALS_FORM, initial={"username": username or ""})
        return values["username"], values["password"]
    try:
        stored_username, stored_password = load()
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError):
        stored_username, stored_password = "", ""
    if stored_username and stored_password and (not username or username == stored_username):
        return username or stored_username, stored_password
    values = collect_secure(TLS_CREDENTIALS_FORM, initial={"username": username or stored_username})
    return values["username"], values["password"]
