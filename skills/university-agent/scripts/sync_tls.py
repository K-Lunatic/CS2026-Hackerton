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
        session.login(username, password)
        provider = MoodleTLSProvider(session)
        courses = provider.get_courses(user_id)
        assignments = provider.get_assignments(user_id)
        lectures = provider.get_lectures(user_id)
        notices = provider.get_notices(user_id)
        resources = provider.get_resources(user_id)
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
