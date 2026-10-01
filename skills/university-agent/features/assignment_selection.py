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
    exact_ids = {item["id"] for item in find_assignments(provider, user_id, {"source": "tls", "query": query})}
    expanded = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", query)
    terms = [normalize(term) for term in re.findall(r"[A-Za-z]+(?:[+#]+)?|[가-힣]+|\d+", expanded)]
    terms = [term for term in terms if len(term) >= 2]
    whole = normalize(query)
    ranked = []
    for item in items:
        title = normalize(item["title"])
        course = normalize(item["courseName"])
        description = normalize(item.get("description") or "")
        score = 20 if item["id"] in exact_ids else 0
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


def search_save_targets(provider: TLSProvider, user_id: str, query: str) -> dict[str, Any]:
    """Search academic metadata only; summaries and checkpoints are untouched."""
    result = selection_guidance(find_similar_tls_assignments(provider, user_id, query))
    result["data"].update(query=query, dataSource="local",
                          lastSyncedAt=(provider.get_user(user_id) or {}).get("lastSyncedAt"))
    return result


def selection_command(item: dict[str, Any], operation: str) -> str:
    tokens = ["save new" if operation == "save" and item.get("source") == "manual" else operation]
    tokens.extend(["--course", _quoted(item.get("courseName") or "기타 과제")])
    tokens.extend(["--title", _quoted(item["title"])])
    tokens.extend(["--due", _quoted(item.get("dueAt") or "미정")])
    return " ".join(tokens)


def _quoted(value: str) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def selection_guidance(items: list[dict[str, Any]], operation: str = "save") -> dict[str, Any]:
    """Return only names/deadlines and copyable commands, never database IDs."""
    candidates = []
    for item in items:
        status = item.get("submissionStatus", "UNKNOWN")
        available = status not in {"SUBMITTED", "LATE"}
        command = selection_command(item, operation) if available else None
        candidates.append(dict(courseName=item["courseName"] or "기타 과제", title=item["title"],
                               dueAt=item.get("dueAt"), submissionStatus=status, command=command))
    if not items:
        intro = ("저장된 TLS 과제에서 후보를 찾지 못했어요. 과목명이나 기억나는 단어를 하나 더 알려주세요. "
                 "직접 정한 이름으로 저장하려면 ‘새 이름: 내 과제’처럼 말해 주세요. 아직 저장하지 않았어요."
                 if operation == "save" else
                 "해당 이름의 과제를 찾지 못했어요. 다른 과목명·키워드를 알려주시거나 `list`로 저장 목록을 확인해 주세요.")
    elif len(items) == 1:
        action = "저장" if operation == "save" else "불러오기"
        intro = "저장된 과제 정보에서 후보를 찾았어요."
        if candidates[0]["command"]:
            intro += f" 찾던 과제가 맞으면 아래 명령으로 {action}할 수 있어요."
    else:
        intro = f"저장된 과제 정보에서 후보 {len(items)}개를 찾았어요. 번호나 과제명으로 골라 주세요."
    lines = [intro]
    labels = {"NOT_SUBMITTED": "미제출", "SUBMITTED": "제출 완료", "LATE": "지각 제출 완료", "UNKNOWN": "제출 상태 미상"}
    for index, item in enumerate(candidates, 1):
        lines.append(f"{index}. {item['courseName']} / {item['title']} — {format_deadline(item['dueAt'])} · {labels.get(item['submissionStatus'], '제출 상태 미상')}")
        if item["command"]:
            lines.append(item["command"])
    commands = [item["command"] for item in candidates if item["command"]]
    if any(item["command"] is None for item in candidates):
        lines.append("제출 완료로 기록된 과제에는 진행 기록을 저장할 수 없어요. 실제 상태가 다르면 TLS 새로고침을 요청해 주세요. "
                     "복습 기록을 따로 남기려면 ‘새 이름: 복습 기록’처럼 새 과제 이름을 알려주세요."
                     if operation == "save" else "제출 완료로 기록된 과제에는 남아 있는 진행 기록이 없어요.")
    if len(set(commands)) < len(commands):
        lines.append("과목·제목·마감까지 같은 항목이 있어 아직 구분할 수 없습니다. 학교에서 과제 정보를 확인해 주세요.")
    return {"toolCalls": ["find_assignments"], "needsInput": True,
            "data": {"performed": False, "operation": operation, "candidates": candidates,
                     "stage": "not_found" if not items else "choose_assignment" if commands else "completed"},
            "nextCommands": list(dict.fromkeys(commands)) if items or operation == "save" else ["list"],
            "answer": "\n".join(lines)}


def public_checkpoint(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {key: record[key] for key in ("courseName", "assignmentTitle", "progress", "completedItems", "blocker", "nextAction", "savedAt")}
