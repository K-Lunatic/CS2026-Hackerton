#!/usr/bin/env python3
"""Enable immediate answer feedback for existing TLS quiz activities."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError
from scripts.inspect_tls_activity_forms import _FormParser, authenticated_session


IMMEDIATE_FIELDS = (
    "attemptimmediately", "correctnessimmediately", "marksimmediately",
    "specificfeedbackimmediately", "generalfeedbackimmediately",
    "rightanswerimmediately",
)


class QuizFeedbackError(ValueError):
    pass


class _EditParser(_FormParser):
    """Keep unchecked review checkboxes visible so the plan can enable them."""

    def handle_starttag(self, tag, attrs):
        if tag == "input" and self.in_form:
            values = dict(attrs)
            name = values.get("name") or ""
            kind = (values.get("type") or "text").lower()
            if name:
                self.fields[name] = {"kind": kind, "value": values.get("value") or ""}
                self.field_values[name] = ([values.get("value") or ""]
                                           if kind not in {"checkbox", "radio"} or "checked" in values else [])
            return
        super().handle_starttag(tag, attrs)


def _error_text(page: str) -> str:
    blocks = re.findall(r'<(?:div|span|p)[^>]+class=["\'][^"\']*(?:alert|error|invalid)[^"\']*["\'][^>]*>(.*?)</(?:div|span|p)>', page, re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", html.unescape(" ".join(blocks)))
    return re.sub(r"\s+", " ", text).strip()[:300]


def _quiz_ids(session, course_id: str) -> list[str]:
    structure = MoodleTLSProvider(session).get_course_structure(course_id)
    ids = [str(activity.get("externalId", ""))
           for section in structure.get("sections", [])
           for activity in section.get("activities", [])
           if activity.get("type") == "quiz" and re.fullmatch(r"[1-9][0-9]*", str(activity.get("externalId", "")))]
    return list(dict.fromkeys(ids))


def _read_form(session, quiz_id: str):
    page, page_url = session.get_page(f"/course/modedit.php?update={quiz_id}")
    parser = _EditParser()
    parser.feed(page)
    action = urlsplit(urljoin(page_url, parser.action or page_url))
    base = urlsplit(session.base_url)
    if (action.scheme, action.hostname, action.path) != (base.scheme, base.hostname, "/course/modedit.php"):
        raise QuizFeedbackError(f"퀴즈 {quiz_id}의 저장 주소가 TLS 양식과 다릅니다.")
    if parser.method != "POST":
        raise QuizFeedbackError(f"퀴즈 {quiz_id}의 저장 방식이 POST가 아닙니다.")
    missing = [name for name in IMMEDIATE_FIELDS if name not in parser.fields]
    if missing:
        raise QuizFeedbackError(f"퀴즈 {quiz_id}에서 즉시 공개 필드를 확인하지 못했습니다: {', '.join(missing)}")
    parser.action = action.path
    return page, page_url, parser


def _payload(parser: _EditParser) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name, info in parser.fields.items():
        if info.get("kind") in {"file", "submit", "button", "reset"}:
            continue
        values = parser.field_values.get(name, [])
        if values:
            payload[name] = values if len(values) > 1 else values[0]
    for name in IMMEDIATE_FIELDS:
        payload[name] = "1"
    if "submitbutton" in parser.fields:
        payload["submitbutton"] = parser.fields["submitbutton"].get("value", "저장")
    return payload


def update_quizzes(course_id: str, quiz_ids: list[str] | None = None, *, apply: bool = False) -> dict[str, object]:
    if not re.fullmatch(r"[1-9][0-9]*", course_id):
        raise QuizFeedbackError("course id must be numeric")
    session = authenticated_session()
    courses = MoodleTLSProvider(session).get_autonomous_courses(os.environ.get("TLS_USERNAME", ""))
    course = next((item for item in courses if str(item.get("externalId")) == course_id), None)
    if not course or course.get("role") not in {"운영자", "교수"}:
        raise QuizFeedbackError("내 자율강좌로 확인된 강좌가 아니어서 실행하지 않았습니다.")
    ids = quiz_ids or _quiz_ids(session, course_id)
    if any(not re.fullmatch(r"[1-9][0-9]*", quiz_id) for quiz_id in ids):
        raise QuizFeedbackError("quiz id must be numeric")
    result = {"courseId": course_id, "mode": "apply" if apply else "plan", "quizCount": len(ids), "quizzes": []}
    for quiz_id in ids:
        page, page_url, parser = _read_form(session, quiz_id)
        current = {name: parser.field_values.get(name, []) for name in IMMEDIATE_FIELDS}
        entry = {"quizId": quiz_id, "name": parser.field_values.get("name", [""])[0],
                 "current": current, "enabled": list(IMMEDIATE_FIELDS), "applied": False}
        if apply:
            response_html, response = session._request(parser.action or "/course/modedit.php",
                                                        data=urlencode(_payload(parser), doseq=True).encode(),
                                                        referer=page_url)
            location = urlsplit(response.geturl())
            if "/login" in location.path or re.search(r'name=["\']username["\']', response_html, re.I):
                raise LoginError("TLS session expired; sign in again")
            if location.path == "/course/modedit.php" and _error_text(response_html):
                raise QuizFeedbackError(f"퀴즈 {quiz_id} 저장 실패: {_error_text(response_html)}")
            _, _, verified = _read_form(session, quiz_id)
            if any("1" not in verified.field_values.get(name, []) for name in IMMEDIATE_FIELDS):
                raise QuizFeedbackError(f"퀴즈 {quiz_id} 저장 후 즉시 공개 설정을 확인하지 못했습니다.")
            entry["applied"] = True
        result["quizzes"].append(entry)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Enable immediate answer feedback for TLS quizzes")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--quiz-id", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(update_quizzes(args.course_id, args.quiz_id or None, apply=args.apply), ensure_ascii=False, indent=2))
    except (LoginError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
