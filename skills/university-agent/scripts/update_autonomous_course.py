#!/usr/bin/env python3
"""Update observed, safe TLS autonomous-course settings through its own form."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.autonomous_course_policy import course_external_id_from_url
from providers.credentials import CredentialInputRequired, resolve, save
from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError, MoodleSession


class CourseUpdateError(ValueError):
    pass


_ALLOWED = {
    "course_visible": {"0", "1"},
    "numsections": {str(value) for value in range(0, 41)},
    "hiddensections": {"0", "1"},
    "coursedisplay": {"0", "1"},
    "course_progress": {"0", "1"},
    "course_notification": {"0", "1"},
}


class _SettingsFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.form: dict[str, object] | None = None
        self.select: str | None = None
        self.fields: dict[str, str] = {}
        self.action = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and self.form is None:
            self.form = values
            self.action = values.get("action") or ""
        elif self.form is not None and tag == "input":
            name = values.get("name") or ""
            kind = (values.get("type") or "text").lower()
            if name and kind not in {"submit", "button", "reset"} and (kind not in {"radio", "checkbox"} or "checked" in values):
                self.fields[name] = values.get("value") or ""
        elif self.form is not None and tag == "select":
            self.select = values.get("name") or ""
        elif self.select and tag == "option" and "selected" in values:
            self.fields[self.select] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = None
        elif tag == "form":
            self.form = None


def _parse_form(html: str) -> tuple[str, dict[str, str]]:
    parser = _SettingsFormParser()
    parser.feed(html)
    if not parser.action or "type" not in parser.fields or parser.fields.get("type") != "course_setting":
        raise CourseUpdateError("TLS 강좌 설정 저장 양식을 확인하지 못했어요.")
    return parser.action, parser.fields


def _validate_updates(updates: dict[str, str]) -> None:
    unknown = sorted(set(updates) - set(_ALLOWED))
    if unknown:
        raise CourseUpdateError(f"이 설정은 자동 변경 대상이 아니에요: {', '.join(unknown)}")
    for name, value in updates.items():
        if value not in _ALLOWED[name]:
            raise CourseUpdateError(f"{name} 값이 허용 범위를 벗어났어요.")


def update_course_settings(course_url: str, updates: dict[str, str]) -> dict[str, object]:
    _validate_updates(updates)
    course_id = course_external_id_from_url(course_url)
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise CourseUpdateError(str(error)) from error
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    session.login(username, password)
    save(username, password)
    course = next((item for item in MoodleTLSProvider(session).get_autonomous_courses(username)
                   if str(item.get("externalId")) == course_id), None)
    if not course or course.get("role") not in {"운영자", "교수"}:
        raise CourseUpdateError("내 운영 권한이 확인된 자율강좌가 아니어서 수정하지 않았어요.")
    html, page_url = session.get_page(f"/local/ubion/setting/course.php?id={course_id}")
    action, fields = _parse_form(html)
    endpoint = urljoin(page_url, action)
    parsed = urlsplit(endpoint)
    base = urlsplit(session.base_url)
    if (parsed.scheme, parsed.hostname, parsed.path) != (base.scheme, base.hostname, "/local/ubion/setting/action.php"):
        raise CourseUpdateError("예상하지 못한 TLS 강좌 설정 저장 주소라 중단했어요.")
    if fields.get("id") != course_id:
        raise CourseUpdateError("강좌 설정 양식의 대상 ID가 요청과 달라요.")
    fields.update(updates)
    response_html, response = session._request(parsed.path, data=urlencode(fields).encode(), referer=page_url)
    if "/login" in urlsplit(response.geturl()).path or re.search(r'name=["\']username["\']', response_html, re.I):
        raise LoginError("TLS session expired; sign in again")
    refreshed, _ = session.get_page(f"/local/ubion/setting/course.php?id={course_id}")
    _, current = _parse_form(refreshed)
    if any(current.get(name) != value for name, value in updates.items()):
        raise CourseUpdateError("TLS가 저장 결과를 다시 확인해 주지 않았어요.")
    return {"courseId": course_id, "courseName": course.get("name"), "updated": updates,
            "endpoint": "/local/ubion/setting/action.php", "verified": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Update safe settings of one KKU TLS autonomous course")
    parser.add_argument("--course-url", required=True)
    parser.add_argument("--set", dest="settings", action="append", default=[], metavar="FIELD=VALUE")
    args = parser.parse_args()
    updates = {}
    for item in args.settings:
        if "=" not in item:
            raise SystemExit("--set은 FIELD=VALUE 형식이어야 해요.")
        name, value = item.split("=", 1)
        updates[name] = value
    try:
        print(json.dumps(update_course_settings(args.course_url, updates), ensure_ascii=False))
    except (CourseUpdateError, LoginError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
