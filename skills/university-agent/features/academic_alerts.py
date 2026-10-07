"""Small, local-only priority cards for the next academic actions."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from features.deadlines import SEOUL, deadline, format_deadline


TLS_BASE_URL = "https://tls.kku.ac.kr/"


def _clean(value: Any, limit: int = 180) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _url(item: dict[str, Any], kind: str, base_url: str = TLS_BASE_URL) -> str | None:
    if item.get("url"):
        return str(item["url"])
    # Notice records currently keep the article number but not the board number;
    # do not manufacture a URL that may open a different article.
    if kind == "notice":
        return None
    external_id = item.get("externalId")
    if not external_id or item.get("source") != "tls":
        return None
    paths = {
        "assignment": f"mod/assign/view.php?id={external_id}",
        "lecture": f"mod/vod/view.php?id={external_id}",
        "notice": f"mod/ubboard/article.php?id={external_id}",
    }
    return urljoin(base_url.rstrip("/") + "/", paths[kind])


def _urgency(due: datetime | None, now: datetime) -> tuple[int, str]:
    if due is None:
        return 4, "마감 확인 필요"
    if due < now:
        return 0, "기한 지남"
    if due <= now + timedelta(days=1):
        return 1, "내일 전"
    if due <= now + timedelta(days=3):
        return 2, "곧 마감"
    return 3, "미리 확인"


def collect_alerts(provider, user_id: str, *, now: datetime | None = None,
                   base_url: str = TLS_BASE_URL) -> dict[str, Any]:
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    courses = {item["id"]: item["name"] for item in provider.get_courses(user_id)}
    alerts: list[dict[str, Any]] = []

    for item in provider.get_assignments(user_id):
        if item.get("submissionStatus") not in {"NOT_SUBMITTED", "UNKNOWN"}:
            continue
        due = deadline(item.get("dueAt"))
        rank, state = _urgency(due, now)
        alerts.append({
            "kind": "assignment", "id": item["id"], "courseId": item.get("courseId"),
            "courseName": courses.get(item.get("courseId"), "과목 확인 필요"),
            "title": item["title"], "state": state, "rank": rank,
            "dueAt": item.get("dueAt"), "status": item.get("submissionStatus"),
            "summary": _clean(item.get("description")) or ("제출 상태를 확인할 수 없는 과제예요." if item.get("submissionStatus") == "UNKNOWN" else "아직 제출하지 않은 과제예요."),
            "url": _url(item, "assignment", base_url),
        })

    for item in provider.get_lectures(user_id):
        if item.get("completed"):
            continue
        start = deadline(item.get("availableFrom"))
        if start and start > now:
            continue
        due = deadline(item.get("availableUntil"))
        rank, state = _urgency(due, now)
        progress = float(item.get("watchProgress") or 0)
        alerts.append({
            "kind": "lecture", "id": item["id"], "courseId": item.get("courseId"),
            "courseName": courses.get(item.get("courseId"), "과목 확인 필요"),
            "title": item["title"], "state": state, "rank": rank,
            "dueAt": item.get("availableUntil"), "status": "UNFINISHED",
            "summary": f"현재 시청률 {progress:g}%예요. 아직 끝까지 보지 않았어요.",
            "url": _url(item, "lecture", base_url),
        })

    alerts.sort(key=lambda item: (item["rank"], item.get("dueAt") is None, item.get("dueAt") or "", item["title"]))
    notices = provider.get_notices(user_id)
    for item in notices:
        item["url"] = _url(item, "notice", base_url)
    notices.sort(key=lambda item: item.get("publishedAt") or "", reverse=True)
    return {"asOf": now.isoformat(), "alerts": alerts, "notices": notices[:10], "courses": courses}


def fingerprint(data: dict[str, Any]) -> str:
    payload = [{key: item.get(key) for key in ("kind", "id", "state", "dueAt", "status", "summary")} for item in data["alerts"]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def notice_ids(data: dict[str, Any]) -> list[str]:
    return [str(item["id"]) for item in data.get("notices", [])]


def format_alerts(data: dict[str, Any], *, changed: bool = True) -> str:
    alerts = data.get("alerts", [])
    notices = data.get("newNotices", [])
    if (not changed and not notices) or (not alerts and not notices):
        return ""
    lines = ["⚠️ 터틀넥이 먼저 하라고 알려드리는 것들이 있어요."]
    for item in alerts[:8]:
        lines.append(f"\n[{item['state']}] {item['courseName']} · {item['title']}")
        if item.get("dueAt"):
            lines.append(f"기한: {format_deadline(item['dueAt'])}")
        lines.append(f"내용: {item['summary']}")
        if item.get("url"):
            lines.append(f"바로 보기: {item['url']}")
    for item in notices[:5]:
        lines.append(f"\n[새 공지] {item.get('title', '공지')}")
        content = _clean(item.get("content"))
        if content:
            lines.append(f"내용: {content}")
        if item.get("url"):
            lines.append(f"바로 보기: {item['url']}")
    return "\n".join(lines)
