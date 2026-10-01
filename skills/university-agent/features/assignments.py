from __future__ import annotations

from typing import Any

from providers.tls_provider import TLSProvider


def get_assignments(provider: TLSProvider, user_id: str, *, unsubmitted: bool = False, upcoming: bool = False) -> list[dict[str, Any]]:
    result = [item.copy() for item in provider.get_assignments(user_id)]
    if unsubmitted or upcoming:
        result = [item for item in result if item["submissionStatus"] == "NOT_SUBMITTED"]
    if upcoming:
        result.sort(key=lambda item: item["dueAt"])
    return result
