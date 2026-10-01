#!/usr/bin/env python3
"""Login to TLS and fetch one page for endpoint discovery."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.credentials import resolve, save
from providers.moodle_session import LoginError, MoodleSession


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a TLS page with an in-memory Moodle session")
    parser.add_argument("--path", default="/my/", help="TLS path to fetch after login")
    parser.add_argument("--output", type=Path, help="Save fetched HTML locally instead of printing it")
    parser.add_argument("--remember", action="store_true", help="save username locally and password in macOS Keychain after login")
    args = parser.parse_args()
    username, password = resolve(os.environ.get("TLS_USERNAME"), os.environ.get("TLS_PASSWORD"))
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    try:
        session.login(username, password)
        if args.remember:
            save(username, password)
        html = session.get(args.path)
    except (LoginError, URLError) as error:
        raise SystemExit(str(error)) from error
    if args.output:
        args.output.write_text(html, encoding="utf-8")
        print(args.output)
    else:
        print(html)


if __name__ == "__main__":
    main()
