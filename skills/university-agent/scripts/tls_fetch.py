#!/usr/bin/env python3
"""Login to TLS and fetch one page for endpoint discovery."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.error import URLError
from socket import timeout as SocketTimeout

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.credentials import CredentialInputRequired, resolve, save
from providers.moodle_session import LoginError, MoodleSession


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a TLS page with an in-memory Moodle session")
    parser.add_argument("--path", default="/my/", help="TLS path to fetch after login")
    parser.add_argument("--output", type=Path, help="Save fetched HTML locally instead of printing it")
    args = parser.parse_args()
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise SystemExit(str(error)) from error
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    try:
        session.login(username, password)
        save(username, password)
        html = session.get(args.path)
    except SocketTimeout as error:
        raise SystemExit("TLS 서버 응답이 30초 동안 없어 중단했습니다. 잠시 후 다시 실행해 주세요.") from error
    except (LoginError, URLError) as error:
        raise SystemExit(str(error)) from error
    if args.output:
        args.output.write_text(html, encoding="utf-8")
        print(args.output)
    else:
        print(html)


if __name__ == "__main__":
    main()
