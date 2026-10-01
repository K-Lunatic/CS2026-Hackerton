from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta
import re

from providers.tls_provider import TLSProvider
from features.deadlines import SEOUL, deadline, format_deadline


def get_assignments(provider: TLSProvider, user_id: str, *, unsubmitted: bool = False, upcoming: bool = False, this_week: bool = False, overdue: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    return filter_assignments(provider.get_assignments(user_id), unsubmitted=unsubmitted,
                              upcoming=upcoming, this_week=this_week, overdue=overdue, now=now)


def filter_assignments(items: list[dict[str, Any]], *, unsubmitted: bool = False, upcoming: bool = False, this_week: bool = False, overdue: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
    """Filter one request's snapshot, preserving stable order and detached results."""
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    pending_only = unsubmitted or upcoming or this_week or overdue
    if this_week:
        start = now.date() - timedelta(days=now.weekday())
        end = start + timedelta(days=7)
    undated_key = datetime.max.replace(tzinfo=SEOUL)
    result = []
    for item in items:
        if pending_only and item["submissionStatus"] != "NOT_SUBMITTED":
            continue
        due = deadline(item.get("dueAt"))
        if upcoming and (due is None or due < now):
            continue
        if overdue and (due is None or due >= now):
            continue
        if this_week and (due is None or not start <= due.date() < end):
            continue
        result.append((due or undated_key, item))
    result.sort(key=lambda entry: entry[0])
    return [item.copy() for _, item in result]


def assignment_answer(provider: TLSProvider, user_id: str, text: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    weekly = bool(re.search(r"이번\s*주", text))
    upcoming = bool(re.search(r"앞으로|예정|다가오는", text))
    overdue = bool(re.search(r"기한\s*지난|마감\s*지난|밀린|연체", text))
    all_items = provider.get_assignments(user_id)
    data = filter_assignments(all_items, unsubmitted=True, upcoming=upcoming, this_week=weekly, overdue=overdue, now=now)
    courses = {item["id"]: item["name"] for item in provider.get_courses(user_id)}
    label = "미제출 과제"
    if weekly:
        start = now.date() - timedelta(days=now.weekday())
        label = f"이번 주({start:%Y-%m-%d}~{start + timedelta(days=6):%Y-%m-%d}) " + label
    elif upcoming:
        label = "앞으로 마감되는 " + label
    elif overdue:
        label = "기한 지난 " + label
    synced = (provider.get_user(user_id) or {}).get("lastSyncedAt")
    lines = [f"저장된 학사 데이터 기준 · 조회 {now:%Y-%m-%d %H:%M} (한국 시간)",
             f"마지막 TLS 동기화: {format_deadline(synced) if synced else '확인 불가'}", f"{label}: {len(data)}개"]
    for item in data:
        due = deadline(item.get("dueAt"))
        state = "기한 지남" if due is not None and due < now else "미제출"
        lines.append(f"{courses.get(item.get('courseId'), '기타 과제')}: {item['title']} — {format_deadline(item.get('dueAt'))} · {state}")
    unknown = sum(item['submissionStatus'] == 'UNKNOWN' for item in all_items)
    if unknown:
        lines.append(f"제출 상태 확인 필요: {unknown}개 (미제출 수에 포함하지 않음)")
    if weekly or upcoming or overdue:
        undated = sum(item['submissionStatus'] == 'NOT_SUBMITTED' and deadline(item.get('dueAt')) is None for item in all_items)
        if undated:
            lines.append(f"마감 확인 필요: {undated}개 (기간 목록에서 제외)")
    if not data:
        lines.append("이 조건에 해당하는 미제출 과제는 기록되어 있지 않아요. 최신 제출 상태가 필요하면 TLS 새로고침을 요청해 주세요.")
    return {"toolCalls": ["get_unsubmitted_assignments"], "data": data, "answer": "\n".join(lines)}
