"""Resolve human-readable selectors within the current user's assignments."""
from __future__ import annotations

import re
import shlex
import unicodedata
from typing import Any

from features.deadlines import deadline, deadline_key, format_deadline
from providers.tls_provider import TLSProvider


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    # Common course-name spellings; no fuzzy guessing for unknown keywords.
    value = value.replace("자바", "java").replace("파이썬", "python")
    return re.sub(r"[^\w+#]", "", value)


def matches(query: str, *texts: str) -> bool:
    terms = [normalize(term) for term in query.split()]
    haystack = [normalize(text) for text in texts]
    return bool(terms) and all(term and any(term in text for text in haystack) for term in terms)


def find_assignments(provider: TLSProvider, user_id: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    courses = {course["id"]: course["name"] for course in provider.get_courses(user_id)}
    found = []
    for original in provider.get_assignments(user_id):
        item = dict(original, courseName=courses.get(original.get("courseId"), ""))
        course_label = item["courseName"] or "기타 과제"
        if "assignmentId" in selectors and item["id"] != selectors["assignmentId"]:
            continue
        if "query" in selectors and not matches(selectors["query"], course_label, item["title"]):
            continue
        if "course" in selectors and not matches(selectors["course"], course_label):
            continue
        if "title" in selectors and not matches(selectors["title"], item["title"]):
            continue
        if "dueAt" in selectors:
            due = deadline(item.get("dueAt"))
            expected = selectors["dueAt"]
            if expected != item.get("dueAt") and not (due and expected == due.date().isoformat()) and not (expected == "미정" and not item.get("dueAt")):
                continue
        found.append(item)
    for selector, field in (("course", "courseName"), ("title", "title")):
        if selector in selectors:
            exact = [item for item in found if normalize(item[field] or ("기타 과제" if field == "courseName" else "")) == normalize(selectors[selector])]
            if exact:
                found = exact
    return sorted(found, key=lambda item: (deadline_key(item), item["courseName"], item["title"]))


def selection_command(item: dict[str, Any], operation: str) -> str:
    tokens = ["과제 저장" if operation == "save" else "과제 불러오기"]
    tokens.extend(["--과목", shlex.quote(item.get("courseName") or "기타 과제")])
    tokens.extend(["--과제", shlex.quote(item["title"])])
    tokens.extend(["--마감", shlex.quote(item.get("dueAt") or "미정")])
    return " ".join(tokens)


def selection_guidance(items: list[dict[str, Any]], operation: str = "save") -> dict[str, Any]:
    """Return only names/deadlines and copyable commands, never database IDs."""
    candidates = [dict(courseName=item["courseName"] or "기타 과제", title=item["title"],
                       dueAt=item.get("dueAt"), command=selection_command(item, operation)) for item in items]
    if not items:
        intro = "일치하는 과제를 찾지 못했습니다. 과목명이나 과제 제목의 다른 단어로 다시 찾아 주세요."
    elif len(items) == 1:
        action = "저장" if operation == "save" else "불러오기"
        intro = f"해당 과제를 찾았습니다. 진행 기록의 {action}을 요청하려면 아래 명령을 보내 주세요."
    else:
        intro = f"해당하는 과제가 {len(items)}개입니다. 과제명과 마감일을 보고 원하는 명령을 보내 주세요."
    lines = [intro]
    for item in candidates:
        lines.extend([f"• {item['courseName']} / {item['title']} — {format_deadline(item['dueAt'])}", item["command"]])
    if len({item["command"] for item in candidates}) < len(candidates):
        lines.append("과목·제목·마감까지 같은 항목이 있어 아직 구분할 수 없습니다. 학교에서 과제 정보를 확인해 주세요.")
    return {"toolCalls": ["find_assignments"], "needsInput": True,
            "data": {"performed": False, "operation": operation, "candidates": candidates},
            "answer": "\n".join(lines)}


def public_checkpoint(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {key: record[key] for key in ("courseName", "assignmentTitle", "progress", "completedItems", "blocker", "nextAction", "savedAt")}
