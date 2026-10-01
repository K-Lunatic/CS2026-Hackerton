#!/usr/bin/env python3
"""Login to KKU TLS, fetch normalized records, and replace local DB data."""
from __future__ import annotations

import os
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from socket import timeout as SocketTimeout

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError, MoodleSession
from providers.credentials import CredentialInputRequired, resolve, save
from storage.local_db import LocalDatabase


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync KKU TLS data into the local SQLite database")
    parser.parse_args()
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise SystemExit(str(error)) from error
    user_id = os.environ.get("UNIVERSITY_AGENT_USER_ID", username)
    db_path = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db"))
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    try:
        print("TLS 로그인 중…", flush=True)
        session.login(username, password)
        print("로그인 성공", flush=True)
        provider = MoodleTLSProvider(session)
        print("과목 목록 조회 중…", flush=True)
        courses = provider.get_courses(user_id)
        print(f"완료: {len(courses)}개", flush=True)
        print("과제 조회 중…", flush=True)
        assignments = provider.get_assignments(user_id)
        print(f"완료: {len(assignments)}개", flush=True)
        print("강의 진도 조회 중…", flush=True)
        lectures = provider.get_lectures(user_id)
        print(f"완료: {len(lectures)}개", flush=True)
        print("공지 조회 중…", flush=True)
        notices = provider.get_notices(user_id)
        print(f"완료: {len(notices)}개", flush=True)
        print("강의자료 다운로드 중…", flush=True)
        resources = provider.get_resources(user_id)
        print(f"완료: {len(resources)}개", flush=True)
    except SocketTimeout as error:
        raise SystemExit("TLS 서버 응답이 30초 동안 없어 중단했습니다. 잠시 후 다시 실행해 주세요.") from error
    except (LoginError, URLError) as error:
        raise SystemExit(str(error)) from error
    save(username, password)
    now = datetime.now(timezone.utc).isoformat()
    file_root = db_path.parent / "files"
    for item in resources:
        content = item.pop("_content")
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", Path(item["fileName"]).name)
        target = file_root / item["courseId"] / safe_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        item.update(localPath=str(target), downloadedAt=now)
    database = LocalDatabase(db_path)
    database.upsert_tls_snapshot(user_id, courses, assignments, lectures, username, None, now, notices, resources)
    database.close()
    print(f"synced courses={len(courses)} assignments={len(assignments)} lectures={len(lectures)} notices={len(notices)} resources={len(resources)}")


if __name__ == "__main__":
    main()
