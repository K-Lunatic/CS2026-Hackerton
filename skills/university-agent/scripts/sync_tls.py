#!/usr/bin/env python3
"""Login to KKU TLS, fetch normalized records, and replace local DB data."""
from __future__ import annotations

import os
import argparse
import re
import sys
import json
import shlex
import subprocess
import hashlib
import tempfile
from contextlib import closing
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
    """Reuse checked originals, retaining HTTP validators when TLS supplies them."""
    if not db_path.exists():
        return {}
    database = None
    try:
        database = LocalDatabase(db_path, read_only=True)
        root = file_root.resolve()
        cached = {}
        counts = {}
        records = database.get_resources(user_id)
        for item in records:
            value = item.get('localPath')
            if value:
                counts[str(Path(value).resolve())] = counts.get(str(Path(value).resolve()), 0) + 1
        for item in records:
            path_value = item.get("localPath")
            if not path_value:
                continue
            path = Path(path_value).expanduser()
            try:
                inside_root = path.resolve().is_relative_to(root)
            except (OSError, ValueError):
                inside_root = False
            cached_mime = str(item.get("mimeType") or "").lower()
            cached_extension = str(item.get("extension") or "").lower()
            looks_like_html = False
            if inside_root and path.is_file() and ("html" in cached_mime or cached_extension in {"html", "htm", "php", "unknown"}):
                try:
                    with path.open('rb') as source:
                        sample = source.read(4096).lstrip().lower()
                    looks_like_html = b"<html" in sample or b"<!doctype" in sample or b"<a " in sample or b"<script" in sample or b"<body" in sample
                except OSError:
                    looks_like_html = True
            if inside_root and path.is_file() and not looks_like_html and counts[str(path.resolve())] == 1:
                try:
                    validators = json.loads(path.with_name(path.name + '.http.json').read_text(encoding='utf-8'))
                    item.update({k: v for k, v in validators.items() if k in ('etag', 'lastModified') and isinstance(v, str)})
                except (OSError, ValueError, AttributeError):
                    pass
                cached[str(item.get("externalId", ""))] = item
        return cached
    except (OSError, RuntimeError):
        return {}
    finally:
        if database is not None:
            database.close()


def store_resource(file_root, item):
    """Keep original names, isolate activities and versions, publish only complete files."""
    content = item.pop('_content')
    course, resource = item['courseId'], item['id']
    if not all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in (course, resource)):
        raise ValueError('자료 저장 식별자가 올바르지 않습니다.')
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', re.split(r'[\\/]', item['fileName'])[-1]).rstrip(' .')
    if not name or name in ('.', '..'):
        name = 'resource.' + item['extension']
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I):
        name = '_' + name
    root = file_root.resolve()
    target = root / course / resource / hashlib.sha256(content).hexdigest() / name
    if not target.resolve().is_relative_to(root):
        raise ValueError('자료 저장 위치가 허용된 폴더 밖입니다.')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not target.resolve().is_relative_to(root):
        raise ValueError('자료 저장 위치가 허용된 폴더 밖입니다.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    validators = item.pop('_validators', {})
    target.with_name(target.name + '.http.json').write_text(json.dumps(validators), encoding='utf-8')
    item.update(localPath=str(target), downloadedAt=datetime.now(timezone.utc).isoformat(), downloadStatus='DOWNLOADED')
    return item


def persist(db_path, user_id, username, courses, *, assignments=None, lectures=None, notices=None, resources=None):
    with closing(LocalDatabase(db_path)) as database:
        database.upsert_tls_snapshot(user_id, courses,
            assignments if assignments is not None else [x for x in database.get_assignments(user_id) if x.get('source') != 'manual'],
            lectures if lectures is not None else database.get_lectures(user_id), username, None,
            notices=notices, resources=resources)


def _progress(step: int, total: int, message: str) -> None:
    print(f"[{step}/{total}] {message}", flush=True)


def open_connection_terminal() -> None:
    """Credentials are typed in a native terminal, never the host's captured PTY."""
    command = [sys.executable, str(Path(__file__).resolve())]
    if sys.platform == 'darwin':
        # Terminal does not inherit the host's configured storage location.
        env = [f'{key}={os.environ[key]}' for key in ('UNIVERSITY_AGENT_DB', 'UNIVERSITY_AGENT_USER_ID', 'TLS_BASE_URL', 'TLS_USERNAME') if key in os.environ]
        shell_command = shlex.join(['env', *env, *command])
        subprocess.run(['osascript', '-e', 'tell application "Terminal"', '-e',
                        'activate', '-e', 'do script ' + json.dumps(shell_command), '-e', 'end tell'],
                       check=True, stdout=subprocess.DEVNULL, timeout=30)
    elif os.name == 'nt':
        subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        raise SystemExit('이 터미널에서 직접 실행해 주세요: ' + shlex.join(command))
    print('연결창을 열었어요. 그 창에서 로그인을 마친 뒤 이 대화로 돌아와 주세요. 아직 연결 완료는 아니에요.', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync KKU TLS data into the local SQLite database")
    parser.add_argument('--connect', action='store_true', help='Open a private native terminal for account connection')
    parser.add_argument('--metadata-only', action='store_true', help='Refresh records without downloading new file contents')
    args = parser.parse_args()
    if args.connect:
        try:
            open_connection_terminal()
        except (OSError, subprocess.SubprocessError) as exc:
            raise SystemExit('연결창을 자동으로 열지 못했어요. 직접 터미널에서 실행해 주세요: ' + subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve())])) from exc
        return
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise SystemExit(str(error) + '\n연결창 열기: ' + subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()), '--connect'])) from error
    user_id = os.environ.get("UNIVERSITY_AGENT_USER_ID", username)
    db_path = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db"))
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    file_root = db_path.parent / "files"
    cached_resources = _cached_resources(db_path, user_id, file_root)
    started = monotonic()
    try:
        print("터틀넥이 학교 자료를 살펴볼 준비를 하고 있어요…", flush=True)
        session.login(username, password)
        save(username, password)
        print("연결됐어요. 이제 필요한 내용만 차근차근 가져올게요.", flush=True)
        provider = MoodleTLSProvider(session)
        _progress(1, 5, "수강 과목을 확인하는 중이에요…")
        stage_started = monotonic()
        courses = provider.get_courses(user_id)
        print(f"과목 {len(courses)}개를 찾았어요. · {monotonic() - stage_started:.1f}초", flush=True)
        _progress(2, 5, "과제와 마감일을 살펴보는 중이에요…")
        stage_started = monotonic()
        assignments = provider.get_assignments(user_id)
        persist(db_path, user_id, username, courses, assignments=assignments)
        print(f"과제 {len(assignments)}개를 확인했어요. · {monotonic() - stage_started:.1f}초", flush=True)
        _progress(3, 5, "강의 시청 상태를 확인하는 중이에요…")
        stage_started = monotonic()
        lectures = provider.get_lectures(user_id)
        persist(db_path, user_id, username, courses, assignments=assignments, lectures=lectures)
        print(f"강의 {len(lectures)}개를 확인했어요. · {monotonic() - stage_started:.1f}초", flush=True)
        _progress(4, 5, "새 공지를 살펴보는 중이에요…")
        stage_started = monotonic()
        notices = provider.get_notices(user_id)
        persist(db_path, user_id, username, courses, assignments=assignments, lectures=lectures, notices=notices)
        print(f"공지 {len(notices)}개를 확인했어요. · {monotonic() - stage_started:.1f}초", flush=True)
        _progress(5, 5, "강의 자료를 확인하는 중이에요…")
        stage_started = monotonic()
        resources = provider.get_resources(user_id, notices=notices, existing_resources=cached_resources,
                                           progress=lambda message: print(message, flush=True),
                                           download_new_files=not args.metadata_only,
                                           save_file=lambda item: store_resource(file_root, item))
        reused = sum(item.get('_reused', False) for item in resources)
        print(f"자료 {len(resources)}개를 확인했어요. 기존 파일 {reused}개는 다시 받지 않았어요. · {monotonic() - stage_started:.1f}초", flush=True)
    except SocketTimeout as error:
        raise SystemExit("TLS 서버 응답이 30초 동안 없어 중단했습니다. 잠시 후 다시 실행해 주세요.") from error
    except (LoginError, URLError) as error:
        raise SystemExit(str(error)) from error
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
        store_resource(file_root, item)
    persist(db_path, user_id, username, courses, assignments=assignments, lectures=lectures, notices=notices, resources=resources)
    elapsed = round(monotonic() - started, 1)
    print(f"터틀넥 준비 완료 · 과목 {len(courses)}개 · 과제 {len(assignments)}개 · 강의 {len(lectures)}개 · 공지 {len(notices)}개 · 자료 {len(resources)}개 · 제한으로 건너뜀 {prohibited}개 · {elapsed}초", flush=True)
    print("이제 ‘이번 주에 뭐부터 해야 해?’라고 물어보면 우선순위를 정리해 드릴게요.", flush=True)


if __name__ == "__main__":
    main()
