"""Shared deadline interpretation; date-only deadlines keep their precision."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")


def deadline(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if len(value) == 10:
        return datetime.combine(parsed.date(), time.max, SEOUL)
    return parsed.astimezone(SEOUL) if parsed.tzinfo else None


def deadline_key(item: dict) -> datetime:
    return deadline(item.get("dueAt")) or datetime.max.replace(tzinfo=SEOUL)


def format_deadline(value: str | None) -> str:
    parsed = deadline(value)
    if parsed is None:
        return "마감 확인 필요"
    return parsed.strftime("%Y-%m-%d" if len(value) == 10 else "%Y-%m-%d %H:%M")
