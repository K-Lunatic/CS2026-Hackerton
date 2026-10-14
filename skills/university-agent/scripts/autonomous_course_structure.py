#!/usr/bin/env python3
"""Read one owned autonomous course as a local, non-publishing blueprint."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from socket import timeout as SocketTimeout
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.credentials import CredentialInputRequired, resolve, save
from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError, MoodleSession


def main() -> None:
    parser = argparse.ArgumentParser(description="Read one KKU TLS autonomous course structure")
    parser.add_argument("--course-id", required=True, help="ID from autonomous_courses.py")
    args = parser.parse_args()
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise SystemExit(str(error)) from error
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    try:
        session.login(username, password)
        save(username, password)
        provider = MoodleTLSProvider(session)
        course = next((item for item in provider.get_autonomous_courses(username)
                       if item.get("externalId") == args.course_id), None)
        if course is None:
            raise SystemExit("현재 계정의 자율강좌 목록에서 해당 강좌를 찾지 못했어요.")
        blueprint = provider.get_course_structure(args.course_id)
    except SocketTimeout as error:
        raise SystemExit("강좌 구조를 30초 안에 받지 못했어요. 잠시 후 다시 확인해 주세요.") from error
    except (LoginError, URLError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"course": course, "blueprint": blueprint}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
