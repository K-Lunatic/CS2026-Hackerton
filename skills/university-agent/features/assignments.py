from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta
import re

from providers.tls_provider import TLSProvider
from features.deadlines import SEOUL, deadline, deadline_key, format_deadline


def get_assignments(provider: TLSProvider, user_id: str, *, unsubmitted: bool = False, upcoming: bool = False, this_week: bool = False, overdue: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    result = [item.copy() for item in provider.get_assignments(user_id)]
    if unsubmitted or upcoming or this_week or overdue:
        result = [item for item in result if item["submissionStatus"] == "NOT_SUBMITTED"]
    if upcoming:
        result = [item for item in result if (due := deadline(item.get("dueAt"))) is not None and due >= now]
    if overdue:
        result = [item for item in result if (due := deadline(item.get("dueAt"))) is not None and due < now]
    if this_week:
        start = now.date() - timedelta(days=now.weekday())
        end = start + timedelta(days=7)
        result = [item for item in result if (due := deadline(item.get("dueAt"))) is not None and start <= due.date() < end]
    return sorted(result, key=deadline_key)


def assignment_answer(provider: TLSProvider, user_id: str, text: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    weekly = bool(re.search(r"이번\s*주", text))
    upcoming = bool(re.search(r"앞으로|예정|다가오는", text))
    overdue = bool(re.search(r"기한\s*지난|마감\s*지난|밀린|연체", text))
    data = get_assignments(provider, user_id, unsubmitted=True, upcoming=upcoming, this_week=weekly, overdue=overdue, now=now)
    courses = {item["id"]: item["name"] for item in provider.get_courses(user_id)}
    label = "미제출 과제"
    if weekly:
        start = now.date() - timedelta(days=now.weekday())
        label = f"이번 주({start:%Y-%m-%d}~{start + timedelta(days=6):%Y-%m-%d}) " + label
    elif upcoming:
        label = "앞으로 마감되는 " + label
    elif overdue:
        label = "기한 지난 " + label
    lines = [f"저장된 학사 데이터 기준 · {now:%Y-%m-%d %H:%M} (한국 시간)", f"{label}: {len(data)}개"]
    for item in data:
        due = deadline(item.get("dueAt"))
        state = "기한 지남" if due is not None and due < now else "미제출"
        lines.append(f"{courses.get(item.get('courseId'), '기타 과제')}: {item['title']} — {format_deadline(item.get('dueAt'))} · {state}")
    all_items = provider.get_assignments(user_id)
    unknown = sum(item['submissionStatus'] == 'UNKNOWN' for item in all_items)
    if unknown:
        lines.append(f"제출 상태 확인 필요: {unknown}개 (미제출 수에 포함하지 않음)")
    if weekly or upcoming or overdue:
        undated = sum(item['submissionStatus'] == 'NOT_SUBMITTED' and deadline(item.get('dueAt')) is None for item in all_items)
        if undated:
            lines.append(f"마감 확인 필요: {undated}개 (기간 목록에서 제외)")
    return {"toolCalls": ["get_unsubmitted_assignments"], "data": data, "answer": "\n".join(lines)}
