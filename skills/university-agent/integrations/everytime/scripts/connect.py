#!/usr/bin/env python3
"""One-time official login handoff to the tabless HTTP session."""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, build_opener

from session_store import browser_cookie_jar, save
from everytime import Client, initialize

LOGIN_URL = "https://account.everytime.kr/login?redirect_uri=https%3A%2F%2Feverytime.kr%2Flecture"


class ConnectError(RuntimeError):
    pass


def find_chrome() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        program = os.environ.get("PROGRAMFILES", "")
        candidates = [
            str(Path(local) / "Google/Chrome/Application/chrome.exe"),
            str(Path(program) / "Google/Chrome/Application/chrome.exe"),
            str(Path(local) / "Microsoft/Edge/Application/msedge.exe"),
        ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise ConnectError("Google Chrome, Microsoft Edge 또는 Chromium을 찾지 못했습니다.")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def targets(port: int) -> list[dict[str, object]]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/json/list", headers={"User-Agent": "EverytimeSkill/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []


class DevTools:
    def __init__(self, websocket_url: str) -> None:
        parsed = urlsplit(websocket_url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ConnectError("로컬 DevTools 연결만 허용됩니다.")
        self.socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.socket.sendall(request)
        response = self._read_until(b"\r\n\r\n")
        if b" 101 " not in response:
            self.close()
            raise ConnectError("전용 브라우저의 로컬 DevTools 연결에 실패했습니다.")
        self.message_id = 0

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass

    def call(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.message_id += 1
        message = json.dumps({"id": self.message_id, "method": method, "params": params or {}}, separators=(",", ":")).encode()
        self._send(message)
        while True:
            opcode, payload = self._frame()
            if opcode == 0x9:
                self._send(payload, opcode=0xA)
                continue
            if opcode == 0x8:
                raise ConnectError("로그인 브라우저 연결이 종료되었습니다.")
            if opcode != 0x1:
                continue
            result = json.loads(payload.decode("utf-8"))
            if result.get("id") == self.message_id:
                return result

    def _read_until(self, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self.socket.recv(4096)
            if not chunk:
                raise ConnectError("DevTools 응답이 끊겼습니다.")
            data.extend(chunk)
        return bytes(data)

    def _send(self, payload: bytes, *, opcode: int = 0x1) -> None:
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header = bytes((0x80 | opcode, 0x80 | size))
        elif size <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + size.to_bytes(2, "big")
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + size.to_bytes(8, "big")
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.socket.sendall(header + mask + masked)

    def _frame(self) -> tuple[int, bytes]:
        first, second = self._read_exact(2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        masked = second & 0x80
        mask = self._read_exact(4) if masked else b""
        payload = bytearray(self._read_exact(length))
        if masked:
            payload = bytearray(value ^ mask[index % 4] for index, value in enumerate(payload))
        return opcode, bytes(payload)

    def _read_exact(self, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            chunk = self.socket.recv(length - len(data))
            if not chunk:
                raise ConnectError("DevTools 프레임이 끊겼습니다.")
            data.extend(chunk)
        return bytes(data)


def wait_for_authenticated_page(port: int, deadline: float) -> dict[str, object]:
    while time.monotonic() < deadline:
        for target in targets(port):
            url = str(target.get("url", ""))
            host = urlsplit(url).hostname
            if host == "everytime.kr" and urlsplit(url).path.startswith("/lecture") and target.get("webSocketDebuggerUrl"):
                return target
        time.sleep(1)
    raise ConnectError("로그인 시간이 끝났습니다. 인증이 완료된 경우 다시 시도해 주세요.")


def connect(timeout: int):
    browser = find_chrome()
    port = free_port()
    profile = Path(tempfile.mkdtemp(prefix="everytime-login-"))
    process = subprocess.Popen(
        [browser, f"--user-data-dir={profile}", f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check", LOGIN_URL],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        print("공식 에브리타임 로그인 화면에서 아이디·비밀번호와 2차 인증을 직접 완료해 주세요.", flush=True)
        target = wait_for_authenticated_page(port, time.monotonic() + timeout)
        devtools = DevTools(str(target["webSocketDebuggerUrl"]))
        try:
            result = devtools.call("Network.getAllCookies")
        finally:
            devtools.close()
        cookies = result.get("result", {}).get("cookies", [])
        jar = browser_cookie_jar(cookies)
        request = urllib.request.Request("https://everytime.kr/lecture", headers={"User-Agent": "Mozilla/5.0 EverytimeSkill/1.0"})
        with build_opener(HTTPCookieProcessor(jar)).open(request, timeout=20) as response:
            final = urlsplit(response.geturl())
        if final.hostname != "everytime.kr" or not final.path.startswith("/lecture"):
            raise ConnectError("로그인 상태가 강의실까지 유지되지 않았습니다. 공식 로그인과 2차 인증을 다시 완료해 주세요.")
        save(jar)
        print("SESSION_STORED: OS 보호 저장소에 저장했습니다. 이후 브라우저 없이 조회합니다.")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)
    return jar


def main() -> int:
    parser = argparse.ArgumentParser(description="에브리타임 공식 로그인 후 로컬 HTTP 세션 저장")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.timeout < 30:
        parser.error("--timeout은 30초 이상이어야 합니다.")
    jar = connect(args.timeout)
    result = initialize(client=Client(jar), max_age=0)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["partial"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
