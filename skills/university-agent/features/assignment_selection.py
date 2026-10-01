"""Resolve human-readable selectors within the current user's assignments."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from features.deadlines import deadline, deadline_key, format_deadline
from providers.tls_provider import TLSProvider


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    # Common course-name spellings; no fuzzy guessing for unknown keywords.
    value = value.replace("자바", "java").replace("파이썬", "python")
    return re.sub(r"[^\w+#]", "", value)


def matches(query: str, *texts: str) -> bool:
    # Punctuation-only fragments (for example the hyphen in
    # `연습과제 - 배열`) are separators, not search terms.
    terms = [term for term in (normalize(part) for part in query.split()) if term]
    haystack = [normalize(text) for text in texts]
    return bool(terms) and all(term and any(term in text for text in haystack) for term in terms)


def find_assignments(provider: TLSProvider, user_id: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    courses = {course["id"]: course["name"] for course in provider.get_courses(user_id)}
    found = []
    for original in provider.get_assignments(user_id):
        if "source" in selectors and original.get("source") != selectors["source"]:
            continue
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


def find_similar_tls_assignments(provider: TLSProvider, user_id: str, query: str) -> list[dict[str, Any]]:
    """Show every plausible TLS candidate, including matches in its description."""
    items = find_assignments(provider, user_id, {"source": "tls"})
    query = re.sub(r"\.(?:java|pdf|pptx?|docx?)\b", " ", query, flags=re.I).strip()
    if not query:
        return items
    expanded = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", query)
    terms = [normalize(term) for term in re.findall(r"[A-Za-z]+|[가-힣]+|\d+", expanded)]
    terms = [term for term in terms if len(term) >= 2]
    whole = normalize(query)
    ranked = []
    for item in items:
        title = normalize(item["title"])
        course = normalize(item["courseName"])
        description = normalize(item.get("description") or "")
        score = 0
        if whole and whole in title:
            score += 12
        elif whole and whole in description:
            score += 8
        score += sum(4 for term in terms if term in title)
        score += sum(2 for term in terms if term in description)
        score += sum(1 for term in terms if term in course)
        if whole and len(whole) >= 5 and SequenceMatcher(None, whole, title).ratio() >= 0.72:
            score += 3
        if score:
            ranked.append((score, item))
    return [item for _, item in sorted(ranked, key=lambda row: (-row[0], deadline_key(row[1]), row[1]["courseName"], row[1]["title"]))]


def selection_command(item: dict[str, Any], operation: str, *, simple: bool = True) -> str:
    command = "save new" if operation == "save" and item.get("source") == "manual" else operation
    if simple:
        return f'{command} {_quoted(item["title"])}'
    tokens = [command]
    tokens.extend(["--course", _quoted(item.get("courseName") or "기타 과제")])
    tokens.extend(["--title", _quoted(item["title"])])
    tokens.extend(["--due", _quoted(item.get("dueAt") or "미정")])
    return " ".join(tokens)


def _quoted(value: str) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def selection_guidance(items: list[dict[str, Any]], operation: str = "save") -> dict[str, Any]:
    """Return only names/deadlines and copyable commands, never database IDs."""
    title_counts = {}
    for item in items:
        key = normalize(item["title"])
        title_counts[key] = title_counts.get(key, 0) + 1
    candidates = [dict(courseName=item["courseName"] or "기타 과제", title=item["title"],
                       dueAt=item.get("dueAt"),
                       command=selection_command(item, operation, simple=title_counts[normalize(item["title"])] == 1))
                 for item in items]
    if not items:
        intro = ('관련 과제를 TLS에서 찾지 못했습니다. TLS에 표시된 제목이 다른가요, '
                 '아니면 학교 과제 목록에 없는 과제인가요?\n\n'
                 '- 제목이 다르면 TLS 과제명을 알려주세요.\n'
                 '- 목록에 없다면 저장할 제목을 정해 주세요. 예: `save new "복소수 자료형 과제"`')
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
