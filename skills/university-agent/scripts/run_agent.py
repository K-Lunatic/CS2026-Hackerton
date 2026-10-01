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
from features.handover import HandoverError, create_handover, format_handover, prepare_handover
from features.lectures import get_lectures
from storage.local_db import LocalDatabase

USER_ID = os.environ.get("UNIVERSITY_AGENT_USER_ID", "user-hong")
DB_PATH = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db"))
DB = LocalDatabase(DB_PATH)
PROVIDER = DB


def handover_result(text: str, *, prepare: bool = False, **options) -> dict[str, Any]:
    try:
        if prepare:
            if options.pop('analysis_json', None) is not None:
                raise HandoverError('--prepare와 --analysis-json은 함께 사용할 수 없습니다.')
            messages = prepare_handover(text, **options)
            return {"toolCalls": ["prepare_handover"], "data": {"messages": messages},
                    "needsAnalysis": True, "answer": "호출한 AI가 messages를 분석하고 --analysis-json으로 검증해주세요."}
        data = create_handover(text, **options)
        return {"toolCalls": ["create_handover"], "data": data, "answer": format_handover(data)}
    except HandoverError as exc:
        return {"toolCalls": ["create_handover"], "data": None,
                "error": {"code": "HANDOVER_FAILED", "message": str(exc)}, "answer": str(exc)}


def ask(text: str, *, records: str = "", **options) -> dict[str, Any]:
    if re.search(r"팀플|인수인계|진행 상황|안 끝난 작업|역할.*넘|작업.*담당자", text):
        if not records.strip():
            return {"toolCalls": [], "data": None, "needsInput": True,
                    "answer": "분석할 회의록이나 작업 기록을 --records로 입력해주세요."}
        return handover_result(records, **options)
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
    return {"toolCalls": [], "data": None, "answer": "과제, 강의, 북마크, 학업 컨텍스트, 팀플 진행 상황이나 인수인계를 물어보세요."}


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
    ask_parser.add_argument("--records", default="", help="팀플 분석 대상 기록 (요청과 분리)")
    for target in (ask_parser, handover_parser):
        target.add_argument("--prepare", action="store_true", help="현재 AI용 분석 요청 생성; 결과가 아님")
        target.add_argument("--analysis-json", default=None, help="현재 AI가 생성한 JSON 결과 검증")
        target.add_argument("--project-name", default="")
        target.add_argument("--team", default="", help="팀원 이름과 역할 설명")
        target.add_argument("--assignee", default="", help="작업을 넘기는 현재 담당자")
    args = parser.parse_args()

    if args.command == "context": result = {"toolCalls": ["get_current_context"], "data": get_current_context(PROVIDER, USER_ID, lambda: list_bookmarks(DB, USER_ID))}
    elif args.command == "assignments": result = {"toolCalls": ["get_assignments"], "data": get_assignments(PROVIDER, USER_ID, unsubmitted=args.unsubmitted, upcoming=args.upcoming)}
    elif args.command == "lectures": result = {"toolCalls": ["get_lectures"], "data": get_lectures(PROVIDER, USER_ID, unfinished=args.unfinished)}
    elif args.command == "bookmarks": result = {"toolCalls": ["get_bookmarks"], "data": list_bookmarks(DB, USER_ID)}
    elif args.command == "bookmark-add": result = {"toolCalls": ["create_bookmark"], "data": add_bookmark(DB, USER_ID, args.target_type, args.target_id, args.note)}
    elif args.command == "bookmark-delete": result = {"toolCalls": ["delete_bookmark"], "data": delete_bookmark(DB, USER_ID, args.target_id)}
    elif args.command == "ask": result = ask(args.text, records=args.records, project_name=args.project_name, team=args.team, assignee=args.assignee, prepare=args.prepare, analysis_json=args.analysis_json)
    else: result = handover_result(args.text, project_name=args.project_name, team=args.team, assignee=args.assignee, prepare=args.prepare, analysis_json=args.analysis_json)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    DB.close()
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
