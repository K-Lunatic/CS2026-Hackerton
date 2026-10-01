#!/usr/bin/env python3
"""Dependency-free runner backed by one private device-local SQLite database."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.assignments import get_assignments
from features.bookmarks import add_bookmark, delete_bookmark, list_bookmarks
from features.context import get_current_context
from features.handover import create_handover
from features.lectures import get_lectures
from storage.local_db import LocalDatabase

USER_ID = os.environ.get("UNIVERSITY_AGENT_USER_ID", "user-hong")
DB_PATH = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db"))
DB = LocalDatabase(DB_PATH)
PROVIDER = DB


def ask(text: str) -> dict[str, Any]:
    if re.search(r"과제|안 낸|미제출|밀린", text):
        data = get_assignments(PROVIDER, USER_ID, unsubmitted=True)
        return {"toolCalls": ["get_unsubmitted_assignments"], "data": data, "answer": "현재 미제출 과제가 없습니다." if not data else "\n".join(f"{x['title']} — 마감 {x['dueAt']}" for x in data)}
    if re.search(r"강의|시청|안 본", text):
        data = get_lectures(PROVIDER, USER_ID, unfinished=True)
        return {"toolCalls": ["get_unwatched_lectures"], "data": data, "answer": "\n".join(f"{x['title']} — {x['watchProgress']}%" for x in data) or "미시청 강의가 없습니다."}
    if re.search(r"북마크|즐겨찾기", text):
        data = list_bookmarks(DB, USER_ID)
        return {"toolCalls": ["get_bookmarks"], "data": data, "answer": json.dumps(data, ensure_ascii=False)}
    if re.search(r"컨텍스트|전체|상태", text):
        data = get_current_context(PROVIDER, USER_ID, lambda: list_bookmarks(DB, USER_ID))
        return {"toolCalls": ["get_current_context"], "data": data, "answer": "현재 학업 컨텍스트를 조회했습니다."}
    return {"toolCalls": [], "data": None, "answer": "과제, 강의, 북마크, 학업 컨텍스트를 물어보세요."}


def main() -> None:
    parser = argparse.ArgumentParser(description="University Agent local skill")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("context")
    assignment_parser = sub.add_parser("assignments")
    assignment_parser.add_argument("--unsubmitted", action="store_true")
    assignment_parser.add_argument("--upcoming", action="store_true")
    lecture_parser = sub.add_parser("lectures")
    lecture_parser.add_argument("--unfinished", action="store_true")
    sub.add_parser("bookmarks")
    add = sub.add_parser("bookmark-add")
    add.add_argument("--target-type", required=True)
    add.add_argument("--target-id", required=True)
    add.add_argument("--note", default="")
    remove = sub.add_parser("bookmark-delete")
    remove.add_argument("--target-id", required=True)
    ask_parser = sub.add_parser("ask")
    ask_parser.add_argument("--text", required=True)
    handover_parser = sub.add_parser("handover")
    handover_parser.add_argument("--text", required=True)
    args = parser.parse_args()

    if args.command == "context": result = {"toolCalls": ["get_current_context"], "data": get_current_context(PROVIDER, USER_ID, lambda: list_bookmarks(DB, USER_ID))}
    elif args.command == "assignments": result = {"toolCalls": ["get_assignments"], "data": get_assignments(PROVIDER, USER_ID, unsubmitted=args.unsubmitted, upcoming=args.upcoming)}
    elif args.command == "lectures": result = {"toolCalls": ["get_lectures"], "data": get_lectures(PROVIDER, USER_ID, unfinished=args.unfinished)}
    elif args.command == "bookmarks": result = {"toolCalls": ["get_bookmarks"], "data": list_bookmarks(DB, USER_ID)}
    elif args.command == "bookmark-add": result = {"toolCalls": ["create_bookmark"], "data": add_bookmark(DB, USER_ID, args.target_type, args.target_id, args.note)}
    elif args.command == "bookmark-delete": result = {"toolCalls": ["delete_bookmark"], "data": delete_bookmark(DB, USER_ID, args.target_id)}
    elif args.command == "ask": result = ask(args.text)
    else: result = {"toolCalls": ["create_handover"], "data": create_handover(args.text)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    DB.close()


if __name__ == "__main__":
    main()
