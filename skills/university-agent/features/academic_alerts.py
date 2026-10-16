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
    resources = provider.get_resources(user_id)
    return {"asOf": now.isoformat(), "alerts": alerts, "notices": notices[:10], "resources": resources, "courses": courses}


def fingerprint(data: dict[str, Any]) -> str:
    payload = [{key: item.get(key) for key in ("kind", "id", "state", "dueAt", "status", "summary")} for item in data["alerts"]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def resource_fingerprint(data: dict[str, Any]) -> dict[str, str]:
    """Track visible resource metadata without exposing local or remote paths."""
    result = {}
    for item in data.get("resources", []):
        key = str(item.get("id") or "")
        if key:
            result[key] = hashlib.sha256(json.dumps([
                item.get("courseId"), item.get("title"), item.get("fileName"),
                item.get("downloadStatus"),
            ], ensure_ascii=False).encode()).hexdigest()
    return result


def format_daily_report(provider, user_id, checked_at, *, now=None):
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    courses = {item['id']: item['name'] for item in provider.get_courses(user_id)}
    grouped = {}
    for item in provider.get_assignments(user_id):
        if item.get('source') != 'tls':
            continue
        status = item.get('submissionStatus', 'UNKNOWN')
        due = deadline(item.get('dueAt'))
        labels = []
        if status == 'NOT_SUBMITTED':
            labels.append('미제출')
        elif status == 'UNKNOWN':
            labels.append('제출 상태 확인 필요')
        if due and due < now:
            labels.append('이미 마감')
        elif due and due <= now + timedelta(days=7):
            labels.append('7일 이내 마감')
        if labels:
            state = {'SUBMITTED': '제출 완료', 'LATE': '지각 제출 완료', 'NOT_SUBMITTED': '미제출'}.get(status, '확인 불가')
            grouped.setdefault(item.get('courseId'), []).append(
                f"- [{', '.join(labels)}] {_clean(item['title'])} · 마감: {format_deadline(item.get('dueAt'))} · 제출: {state}")
    for item in provider.get_lectures(user_id):
        if item.get('source') != 'tls' or item.get('completed') in (True, 1):
            continue
        completed = item.get('completed')
        state = '미완료' if completed in (False, 0) else '시청 상태 확인 필요'
        progress = item.get('watchProgress')
        progress_text = f' · 시청률 {float(progress):g}%' if progress is not None else ''
        grouped.setdefault(item.get('courseId'), []).append(
            f"- [강의] {_clean(item['title'])} · 마감: {format_deadline(item.get('availableUntil'))} · 시청: {state}{progress_text}")
    lines = [f"터틀넥 학사 확인 · {format_deadline(checked_at)} (Asia/Seoul)"]
    for course_id in sorted(grouped, key=lambda key: courses.get(key, '')):
        lines.extend(['', courses.get(course_id, '과목 확인 필요'), *grouped[course_id]])
    if not grouped:
        lines.append('미제출·마감 관련 과제와 미완료 강의가 없어요.')
    return '\n'.join(lines)


def notice_ids(data: dict[str, Any]) -> list[str]:
    return [str(item["id"]) for item in data.get("notices", [])]


def format_alerts(data: dict[str, Any], *, changed: bool = True) -> str:
    alerts = data.get("alerts", [])
    notices = data.get("newNotices", [])
    resource_changes = data.get("resourceChanges", [])
    if (not changed and not notices and not resource_changes) or (not alerts and not notices and not resource_changes):
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
    courses = data.get("courses", {})
    for item in resource_changes[:5]:
        lines.append(f"\n[새 자료·자료 업데이트] {courses.get(item.get('courseId'), '과목 확인 필요')} · {item.get('title', '수업자료')}")
        lines.append("수업자료 목록이 바뀌었어요. 필요하면 이 자료를 읽고 핵심 개념과 연습문제를 준비할 수 있어요.")
    return "\n".join(lines)
