#!/usr/bin/env python3
"""Rename an observed section through the exact TLS edit link shown on the course page."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.autonomous_course_policy import course_external_id_from_url, validate_visible_name
from providers.credentials import CredentialInputRequired, resolve, save
from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError, MoodleSession


class SectionUpdateError(ValueError):
    pass


class _EditModeFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action = ""
        self.fields: dict[str, str] = {}
        self.form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and not self.form:
            self.form = True
            self.action = values.get("action") or ""
        elif self.form and tag == "input":
            name = values.get("name") or ""
            if name and (values.get("type") or "text").lower() not in {"submit", "button"}:
                self.fields[name] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.form = False


class _SectionLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section: str | None = None
        self.current_name: str | None = None
        self.anchor: dict[str, str] | None = None
        self.text: list[str] = []
        self.links: dict[str, dict[str, str]] = {}
        self.li_depth = 0
        self.section_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "li":
            self.li_depth += 1
            match = re.search(r"(?:^|\s)section-([1-9][0-9]*)(?:\s|$)", values.get("id") or "")
            if match:
                self.section = match.group(1)
                self.section_depth = self.li_depth
                self.current_name = values.get("aria-label") or ""
        elif self.section and tag == "a":
            href = values.get("href") or ""
            if "/course/editsection.php" in href:
                self.anchor = {"href": href}
                self.text = []

    def handle_data(self, data: str) -> None:
        if self.anchor is not None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor is not None and self.section:
            self.links[self.section] = {"href": self.anchor["href"],
                                        "name": self.current_name or ""}
            self.anchor = None
        elif tag == "li":
            if self.section_depth == self.li_depth:
                self.section = None
                self.current_name = None
                self.section_depth = None
            self.li_depth -= 1


class _SectionFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action = ""
        self.fields: dict[str, str] = {}
        self.form = False
        self.select: str | None = None
        self.textarea: str | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and not self.form:
            self.form = True
            self.action = values.get("action") or ""
        elif self.form and tag == "input":
            name = values.get("name") or ""
            kind = (values.get("type") or "text").lower()
            if name and kind not in {"submit", "button", "reset"} and (kind not in {"radio", "checkbox"} or "checked" in values):
                self.fields[name] = values.get("value") or ""
        elif self.form and tag == "select":
            self.select = values.get("name") or ""
        elif self.select and tag == "option" and "selected" in values:
            self.fields[self.select] = values.get("value") or ""
        elif self.form and tag == "textarea":
            self.textarea = values.get("name") or ""
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.textarea:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self.textarea:
            self.fields[self.textarea] = "".join(self.text)
            self.textarea = None
        elif tag == "select":
            self.select = None
        elif tag == "form":
            self.form = False


def _section_link(html: str, section_number: str) -> tuple[str, str]:
    parser = _SectionLinkParser()
    parser.feed(html)
    item = parser.links.get(section_number)
    if not item:
        raise SectionUpdateError("강좌 화면에서 해당 토픽의 실제 편집 링크를 찾지 못했어요.")
    return item["href"], item["name"]


def _enable_edit_mode(session: MoodleSession, course_id: str, html: str, page_url: str) -> tuple[str, str]:
    parser = _EditModeFormParser()
    parser.feed(html)
    if parser.fields.get("edit") != "on" or parser.fields.get("id") != course_id:
        raise SectionUpdateError("TLS 강좌 화면에서 편집 모드 양식을 찾지 못했어요.")
    action = urljoin(page_url, parser.action)
    parts = urlsplit(action)
    base = urlsplit(session.base_url)
    if (parts.scheme, parts.hostname, parts.path) != (base.scheme, base.hostname, "/course/view.php"):
        raise SectionUpdateError("TLS 편집 모드 주소가 예상과 달라요.")
    fields = dict(parser.fields)
    fields["edit"] = "on"
    response_html, response = session._request(parts.path, data=urlencode(fields).encode(), referer=page_url)
    return response_html, response.geturl()


def update_section_name(course_url: str, section_number: str, name: str) -> dict[str, object]:
    if not re.fullmatch(r"[1-9][0-9]*", str(section_number)):
        raise SectionUpdateError("토픽 번호는 1 이상의 숫자여야 해요.")
    name = validate_visible_name(name, field="토픽 이름")
    course_id = course_external_id_from_url(course_url)
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise SectionUpdateError(str(error)) from error
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    session.login(username, password)
    save(username, password)
    provider = MoodleTLSProvider(session)
    course = next((item for item in provider.get_autonomous_courses(username)
                   if str(item.get("externalId")) == course_id), None)
    if not course or course.get("role") not in {"운영자", "교수"}:
        raise SectionUpdateError("내 운영 권한이 확인된 자율강좌가 아니어서 수정하지 않았어요.")
    course_html, course_page = session.get_page(f"/course/view.php?id={course_id}")
    course_html, course_page = _enable_edit_mode(session, course_id, course_html, course_page)
    href, old_name = _section_link(course_html, str(section_number))
    edit_url = urljoin(course_page, href)
    edit_parts = urlsplit(edit_url)
    base = urlsplit(session.base_url)
    query = parse_qs(edit_parts.query, keep_blank_values=True)
    if (edit_parts.scheme, edit_parts.hostname, edit_parts.path) != (base.scheme, base.hostname, "/course/editsection.php") or "id" not in query:
        raise SectionUpdateError("화면에 표시된 토픽 편집 주소가 예상 형식과 달라요.")
    html, page_url = session.get_page(edit_parts.path + "?" + edit_parts.query)
    parser = _SectionFormParser()
    parser.feed(html)
    action = urljoin(page_url, parser.action)
    action_parts = urlsplit(action)
    if (action_parts.scheme, action_parts.hostname, action_parts.path) != (base.scheme, base.hostname, "/course/editsection.php"):
        raise SectionUpdateError("토픽 편집 저장 주소가 예상과 달라요.")
    if parser.fields.get("id") != query["id"][0]:
        raise SectionUpdateError("토픽 편집 양식의 대상 ID가 화면 링크와 달라요.")
    fields = dict(parser.fields)
    fields["name"] = name
    fields.pop("usedefaultname", None)
    fields["submitbutton"] = "저장"
    response_html, response = session._request(action_parts.path, data=urlencode(fields).encode(), referer=page_url)
    if "/login" in urlsplit(response.geturl()).path or re.search(r'name=["\']username["\']', response_html, re.I):
        raise LoginError("TLS session expired; sign in again")
    refreshed, _ = session.get_page(f"/course/view.php?id={course_id}")
    _href, current_name = _section_link(refreshed, str(section_number))
    if current_name != name:
        raise SectionUpdateError("TLS가 토픽 이름 저장 결과를 다시 확인해 주지 않았어요.")
    return {"courseId": course_id, "section": section_number, "oldName": old_name,
            "name": name, "endpoint": "/course/editsection.php", "verified": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rename one TLS autonomous-course section through its observed edit link")
    parser.add_argument("--course-url", required=True)
    parser.add_argument("--section", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(update_section_name(args.course_url, args.section, args.name), ensure_ascii=False))
    except (SectionUpdateError, LoginError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
