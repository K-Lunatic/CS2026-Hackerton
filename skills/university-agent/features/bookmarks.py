from __future__ import annotations

from typing import Any, Protocol

from .context_commands import command_template


def validate_bookmark_target(target_type: str) -> None:
    if target_type == "CUSTOM":
        raise ValueError(
            "사용자 지정 북마크로 대화 내용을 저장할 수 없습니다. "
            f"다음 명령을 사용자에게 안내해 주세요: {command_template('save')}"
        )
    if target_type not in {"ASSIGNMENT", "LECTURE", "COURSE", "NOTICE", "PROJECT"}:
        raise ValueError("지원하지 않는 북마크 대상입니다.")


class BookmarkStore(Protocol):
    def list_bookmarks(self, user_id: str) -> list[dict[str, Any]]: ...
    def add_bookmark(self, user_id: str, target_type: str, target_id: str, note: str) -> dict[str, Any]: ...
    def delete_bookmark(self, user_id: str, target_id: str) -> dict[str, Any]: ...


def list_bookmarks(store: BookmarkStore, user_id: str) -> list[dict[str, Any]]:
    return store.list_bookmarks(user_id)


def bookmark_answer(provider, user_id: str, records: list[dict[str, Any]]) -> str:
    targets = {}
    for kind, getter, field in (("ASSIGNMENT", provider.get_assignments, "title"), ("LECTURE", provider.get_lectures, "title"),
                                ("COURSE", provider.get_courses, "name"), ("NOTICE", provider.get_notices, "title")):
        if any(record["targetType"] == kind for record in records):
            targets.update({(kind, item["id"]): item[field] for item in getter(user_id)})
    lines = [f"학사 북마크: {len(records)}개"]
    for record in records:
        title = targets.get((record["targetType"], record["targetId"]), "이전 북마크 (대상 정보 확인 필요)")
        lines.append(title + (f" — {record['note']}" if record.get("note") else ""))
    if not records:
        lines.append("아직 학사 북마크가 없어요. 과제 진행 기록을 찾으려면 `list`를 보내 주세요.")
    return "\n".join(lines)


def add_bookmark(store: BookmarkStore, user_id: str, target_type: str, target_id: str, note: str) -> dict[str, Any]:
    validate_bookmark_target(target_type)
    return store.add_bookmark(user_id, target_type, target_id, note)


def delete_bookmark(store: BookmarkStore, user_id: str, target_id: str) -> dict[str, Any]:
    return store.delete_bookmark(user_id, target_id)
