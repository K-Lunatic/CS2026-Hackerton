from __future__ import annotations

from typing import Any, Protocol


class BookmarkStore(Protocol):
    def list_bookmarks(self, user_id: str) -> list[dict[str, Any]]: ...
    def add_bookmark(self, user_id: str, target_type: str, target_id: str, note: str) -> dict[str, Any]: ...
    def delete_bookmark(self, user_id: str, target_id: str) -> dict[str, Any]: ...


def list_bookmarks(store: BookmarkStore, user_id: str) -> list[dict[str, Any]]:
    return store.list_bookmarks(user_id)


def add_bookmark(store: BookmarkStore, user_id: str, target_type: str, target_id: str, note: str) -> dict[str, Any]:
    return store.add_bookmark(user_id, target_type, target_id, note)


def delete_bookmark(store: BookmarkStore, user_id: str, target_id: str) -> dict[str, Any]:
    return store.delete_bookmark(user_id, target_id)
