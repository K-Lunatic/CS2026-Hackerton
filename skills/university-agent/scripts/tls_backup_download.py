#!/usr/bin/env python3
"""Download one visible Moodle backup from an authenticated TLS account."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from socket import timeout as SocketTimeout
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.credentials import CredentialInputRequired, resolve, save
from providers.moodle_session import LoginError, MoodleSession, check_download_origin


class _CourseContextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.context_id: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "body":
            return
        classes = (dict(attrs).get("class") or "").split()
        for token in classes:
            match = re.fullmatch(r"context-([1-9][0-9]*)", token)
            if match:
                self.context_id = match.group(1)
                return


class _BackupLinkParser(HTMLParser):
    def __init__(self, course_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.course_id = course_id
        self.links: list[tuple[str, str]] = []
        self.row: dict[str, object] | None = None
        self.cell_index = -1
        self.cell_text: list[str] = []
        self.anchor: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = {"filename": "", "links": []}
            self.cell_index = -1
        elif tag == "td" and self.row is not None:
            self.cell_index += 1
            self.cell_text = []
        elif tag == "a" and self.row is not None and self.anchor is None:
            self.anchor = {"href": dict(attrs).get("href") or "", "text": []}

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor is not None and self.row is not None:
            self.row["links"].append((self.anchor["href"], " ".join("".join(self.anchor["text"]).split())))
            self.anchor = None
        elif tag == "td" and self.row is not None and self.cell_index == 0:
            self.row["filename"] = " ".join("".join(self.cell_text).split())
        elif tag == "tr" and self.row is not None:
            filename = re.fullmatch(rf"backup-moodle2-course-{re.escape(self.course_id)}-[A-Za-z0-9-]+\.mbz", self.row["filename"])
            if filename:
                for href, title in self.row["links"]:
                    if title == "다운로드":
                        self.links.append((href, filename.group(0)))
                        break
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.row is None:
            return
        if self.anchor is not None:
            self.anchor["text"].append(data)
        elif self.cell_index == 0:
            self.cell_text.append(data)


def _course_context_id(session: MoodleSession, course_id: str) -> str:
    parser = _CourseContextParser()
    parser.feed(session.get(f"/course/view.php?id={course_id}"))
    if not parser.context_id:
        raise RuntimeError("TLS 강좌 화면에서 백업 컨텍스트를 확인하지 못했어요.")
    return parser.context_id


def _visible_backup(session: MoodleSession, course_id: str) -> tuple[str, str]:
    context_id = _course_context_id(session, course_id)
    html = session.get(f"/backup/restorefile.php?contextid={context_id}")
    parser = _BackupLinkParser(course_id)
    parser.feed(html)
    if not parser.links:
        raise RuntimeError("TLS 복구 화면에서 해당 강좌의 백업 다운로드 링크를 찾지 못했어요.")
    href, filename = parser.links[-1]
    url = urljoin(session.base_url + "/", href)
    check_download_origin(url, "tls.kku.ac.kr")
    return url, filename


def _looks_like_moodle_backup(payload: bytes) -> bool:
    # Moodle may return an mbz as a zip or as its gzip-based archive format.
    return payload.startswith((b"PK\x03\x04", b"\x1f\x8b", b".ARCHIVE"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a visible KKU TLS course backup")
    parser.add_argument("--course-id", required=True, help="Course ID whose visible backup should be downloaded")
    parser.add_argument("--output", type=Path, help="Local output directory; defaults to ~/Downloads")
    args = parser.parse_args()
    if not re.fullmatch(r"[1-9][0-9]*", args.course_id):
        raise SystemExit("course id는 숫자여야 합니다.")
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise SystemExit(str(error)) from error
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    try:
        session.login(username, password)
        save(username, password)
        url, filename = _visible_backup(session, args.course_id)
        payload, response = session.get_bytes(url, allowed_origin="tls.kku.ac.kr")
    except SocketTimeout as error:
        raise SystemExit("TLS 백업 파일을 30초 안에 받지 못했어요. 잠시 후 다시 확인해 주세요.") from error
    except (LoginError, URLError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
    if not payload or not _looks_like_moodle_backup(payload):
        raise SystemExit("TLS 응답이 Moodle 백업 파일 형식이 아니어서 저장하지 않았어요.")
    target_dir = (args.output or Path.home() / "Downloads").expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(payload)
    print(json.dumps({"path": str(target), "filename": filename, "bytes": len(payload),
                      "contentType": response.headers.get("Content-Type", "")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
