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
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError, MoodleSession
from providers.credentials import CredentialInputRequired, resolve, save
from storage.local_db import LocalDatabase


def _cached_resources(db_path: Path, user_id: str, file_root: Path) -> dict[str, dict[str, object]]:
    """Reuse files already fetched in this device's private storage.

    # ponytail: cache by TLS resource URL; add server version/ETag checks only if TLS exposes them.
    """
    if not db_path.exists():
        return {}
    database = None
    try:
        database = LocalDatabase(db_path, read_only=True)
        root = file_root.resolve()
        cached = {}
        for item in database.get_resources(user_id):
            path_value = item.get("localPath")
            if not path_value:
                continue
            path = Path(path_value).expanduser()
            try:
                inside_root = path.resolve().is_relative_to(root)
            except (OSError, ValueError):
                inside_root = False
            if inside_root and path.is_file():
                cached[str(item.get("externalId", ""))] = item
        return cached
    except (OSError, RuntimeError):
        return {}
    finally:
        if database is not None:
            database.close()


def _progress(step: int, total: int, message: str) -> None:
    print(f"[{step}/{total}] {message}", flush=True)


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
    file_root = db_path.parent / "files"
    cached_resources = _cached_resources(db_path, user_id, file_root)
    started = monotonic()
    try:
        print("터틀넥이 학교 자료를 살펴볼 준비를 하고 있어요…", flush=True)
        session.login(username, password)
        print("연결됐어요. 이제 필요한 내용만 차근차근 가져올게요.", flush=True)
        provider = MoodleTLSProvider(session)
        _progress(1, 5, "수강 과목을 확인하는 중이에요…")
        courses = provider.get_courses(user_id)
        print(f"과목 {len(courses)}개를 찾았어요.", flush=True)
        _progress(2, 5, "과제와 마감일을 살펴보는 중이에요…")
        assignments = provider.get_assignments(user_id)
        print(f"과제 {len(assignments)}개를 확인했어요.", flush=True)
        _progress(3, 5, "강의 시청 상태를 확인하는 중이에요…")
        lectures = provider.get_lectures(user_id)
        print(f"강의 {len(lectures)}개를 확인했어요.", flush=True)
        _progress(4, 5, "새 공지를 살펴보는 중이에요…")
        notices = provider.get_notices(user_id)
        print(f"공지 {len(notices)}개를 확인했어요.", flush=True)
        _progress(5, 5, "강의 자료를 확인하는 중이에요…")
        resources = provider.get_resources(user_id, notices=notices, existing_resources=cached_resources)
        reused = sum(item.get("downloadStatus") == "DOWNLOADED" for item in resources)
        print(f"자료 {len(resources)}개를 확인했어요. 기존 파일 {reused}개는 다시 받지 않았어요.", flush=True)
    except SocketTimeout as error:
        raise SystemExit("TLS 서버 응답이 30초 동안 없어 중단했습니다. 잠시 후 다시 실행해 주세요.") from error
    except (LoginError, URLError) as error:
        raise SystemExit(str(error)) from error
    save(username, password)
    now = datetime.now(timezone.utc).isoformat()
    prohibited = 0
    course_names = {course["id"]: course["name"] for course in courses}
    for item in resources:
        if item.get("downloadStatus") == "PROHIBITED":
            prohibited += 1
            print(f"다운로드 제한으로 제외: {course_names.get(item['courseId'], '과목')} / {item['title']}", flush=True)
            continue
        if item.get("downloadStatus") == "DOWNLOADED" and item.get("localPath"):
            continue
        if "_content" not in item:
            print(f"원본 파일을 확인하지 못해 건너뜀: {course_names.get(item['courseId'], '과목')} / {item['title']} · {item.get('downloadReason', '파일을 저장하지 않았습니다.')}", flush=True)
            continue
        content = item.pop("_content")
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", Path(item["fileName"]).name)
        target = file_root / item["courseId"] / safe_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        item.update(localPath=str(target), downloadedAt=now, downloadStatus="DOWNLOADED")
    database = LocalDatabase(db_path)
    database.upsert_tls_snapshot(user_id, courses, assignments, lectures, username, None, now, notices, resources)
    database.close()
    elapsed = round(monotonic() - started, 1)
    print(f"터틀넥 준비 완료 · 과목 {len(courses)}개 · 과제 {len(assignments)}개 · 강의 {len(lectures)}개 · 공지 {len(notices)}개 · 자료 {len(resources)}개 · 제한으로 건너뜀 {prohibited}개 · {elapsed}초", flush=True)
    print("이제 ‘이번 주에 뭐부터 해야 해?’라고 물어보면 우선순위를 정리해 드릴게요.", flush=True)


if __name__ == "__main__":
    main()
