from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def list_bookmarks(read_state: Callable[[], dict[str, Any]]) -> list[dict[str, Any]]:
    return read_state().get("bookmarks", [])


def add_bookmark(read_state: Callable[[], dict[str, Any]], write_state: Callable[[dict[str, Any]], None], target_type: str, target_id: str, note: str) -> dict[str, Any]:
    state = read_state()
    bookmarks = state.setdefault("bookmarks", [])
    item = {"id": f"bookmark-{len(bookmarks) + 1}", "userId": "user-hong", "targetType": target_type, "targetId": target_id, "note": note, "createdAt": datetime.now(timezone.utc).isoformat()}
    bookmarks.append(item)
    write_state(state)
    return item


def delete_bookmark(read_state: Callable[[], dict[str, Any]], write_state: Callable[[dict[str, Any]], None], target_id: str) -> dict[str, Any]:
    state = read_state()
    before = len(state.get("bookmarks", []))
    state["bookmarks"] = [item for item in state.get("bookmarks", []) if item["targetId"] != target_id]
    write_state(state)
    return {"deleted": before - len(state["bookmarks"]), "targetId": target_id}
