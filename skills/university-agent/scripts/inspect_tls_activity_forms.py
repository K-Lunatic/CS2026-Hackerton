#!/usr/bin/env python3
"""Inspect observed TLS activity-add forms without submitting them."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.credentials import CredentialInputRequired, load, resolve, save
from providers.moodle_session import LoginError, MoodleSession


MODULES = (
    "ubfile", "vod", "assign", "ubboard", "url", "label", "feedback",
    "page", "wiki", "econtents", "silverstream", "estream", "ubchat",
    "quiz", "forum", "choice", "ubpeer", "resource", "folder", "zoom",
)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action = ""
        self.method = "GET"
        self.fields: dict[str, dict[str, str]] = {}
        self.field_values: dict[str, list[str]] = {}
        self.in_form = False
        self.select: str | None = None
        self.select_kind = "select"
        self.textarea: str | None = None
        self.text: list[str] = []

    def _record(self, name: str, kind: str, value: str) -> None:
        self.fields[name] = {"kind": kind, "value": value}
        self.field_values.setdefault(name, []).append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and not self.in_form:
            self.in_form = True
            self.action = values.get("action") or ""
            self.method = (values.get("method") or "GET").upper()
            return
        if not self.in_form:
            return
        if tag == "input":
            name = values.get("name") or ""
            kind = (values.get("type") or "text").lower()
            if name and (kind not in {"radio", "checkbox"} or "checked" in values):
                self._record(name, kind, values.get("value") or "")
        elif tag == "select":
            self.select = values.get("name") or None
            if self.select:
                self.select_kind = "select-multiple" if "multiple" in values else "select"
                self.fields[self.select] = {"kind": self.select_kind, "value": ""}
                self.field_values[self.select] = []
        elif tag == "option" and self.select and "selected" in values:
            value = values.get("value") or ""
            self.fields[self.select]["value"] = value
            self.field_values.setdefault(self.select, []).append(value)
        elif tag == "textarea":
            name = values.get("name") or ""
            if name:
                self._record(name, "textarea", "")
                self.textarea = name
                self.text = []

    def handle_data(self, data: str) -> None:
        if self.textarea:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = None
        elif tag == "textarea" and self.textarea:
            value = "".join(self.text)
            self.fields[self.textarea]["value"] = value
            self.field_values[self.textarea] = [value]
            self.textarea = None
        elif tag == "form":
            self.in_form = False


def authenticated_session() -> MoodleSession:
    username, password = resolve(os.environ.get("TLS_USERNAME"))
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    session.login(username, password)
    save(username, password)
    return session


def inspect_forms(course_id: str) -> list[dict[str, object]]:
    if not re.fullmatch(r"[1-9][0-9]*", course_id):
        raise ValueError("course id must be numeric")
    session = authenticated_session()
    base = urlsplit(session.base_url)
    result: list[dict[str, object]] = []
    for module in MODULES:
        try:
            html, page_url = session.get_page(
                f"/course/modedit.php?course={course_id}&section=1&add={module}"
            )
        except HTTPError as error:
            result.append({"module": module, "available": False, "status": error.code})
            error.close()
            continue
        parser = _FormParser()
        parser.feed(html)
        action = urlsplit(urljoin(page_url, parser.action))
        if (action.scheme, action.hostname) != (base.scheme, base.hostname):
            raise ValueError(f"unexpected form origin for {module}")
        result.append({
            "module": module,
            "available": True,
            "method": parser.method,
            "action": action.path,
            "fieldNames": sorted(parser.fields),
            "fileFields": sorted(name for name, item in parser.fields.items() if item["kind"] == "file"),
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect TLS activity forms without creating activities")
    parser.add_argument("--course-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_forms(args.course_id), ensure_ascii=False, indent=2))
    except (CredentialInputRequired, LoginError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
