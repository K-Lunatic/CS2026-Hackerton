from __future__ import annotations

from typing import Any

from providers.tls_provider import TLSProvider


def get_lectures(provider: TLSProvider, user_id: str, *, unfinished: bool = False) -> list[dict[str, Any]]:
    result = [item.copy() for item in provider.get_lectures(user_id)]
    return [item for item in result if not unfinished or not item["completed"]]
