#!/usr/bin/env python3
"""Dependency-free runner backed by a private device-local SQLite database."""
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
from features.context_bookmarks import format_resume_card, get_context_bookmark, save_context_bookmark
from features.context_commands import command_template, detect_context_intent, parse_context_command
from features.handover import HandoverError, create_handover, format_handover, prepare_handover
from features.lectures import get_lectures
from storage.local_db import LocalDatabase

USER_ID = os.environ.get("UNIVERSITY_AGENT_USER_ID", "user-hong")
DB_PATH = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db")).expanduser()
DB: LocalDatabase | None = None


def database() -> LocalDatabase:
    """Open the device database only when a command actually needs it."""
    global DB
    if DB is None:
        DB = LocalDatabase(DB_PATH)
    return DB


def handover_result(text: str, *, prepare: bool = False, **options) -> dict[str, Any]:
    try:
        if prepare:
            if options.pop("analysis_json", None) is not None:
                raise HandoverError("--prepare와 --analysis-json은 함께 사용할 수 없습니다.")
            messages = prepare_handover(text, **options)
            return {
                "toolCalls": ["prepare_handover"],
                "data": {"messages": messages},
                "needsAnalysis": True,
                "answer": "호출한 AI가 messages를 분석하고 --analysis-json으로 검증해주세요.",
            }
        data = create_handover(text, **options)
        return {"toolCalls": ["create_handover"], "data": data, "answer": format_handover(data)}
    except HandoverError as exc:
        return {
            "toolCalls": ["create_handover"],
            "data": None,
            "error": {"code": "HANDOVER_FAILED", "message": str(exc)},
            "answer": str(exc),
        }


def _checkpoint_command(text: str) -> dict[str, Any] | None:
    command = parse_context_command(text)
    if command is None:
        return None
    operation = command["operation"]
    if "error" in command:
        return {
            "toolCalls": ["prompt_context_command"],
            "data": {"operation": operation, "performed": False},
            "answer": (
                f"명령 형식이 맞지 않아 실행하지 않았습니다. {command['error']}\n"
                f"다음 형식으로 입력해 주세요:\n{command_template(operation)}"
            ),
        }

    values = command["values"]
    if operation == "load":
        record = get_context_bookmark(
            user_id=USER_ID,
            assignment_id=values.get("assignmentId"),
            db_path=DB_PATH,
        )
        return {
            "toolCalls": ["get_context_bookmark"],
            "data": record,
            "answer": format_resume_card(record) if record else "해당 과제에 저장된 진행 기록이 없습니다.",
        }

    provider = database()
    assignment = next(
        (item.copy() for item in provider.get_assignments(USER_ID) if item["id"] == values["assignmentId"]),
        None,
    )
    if assignment is None:
        return {
            "toolCalls": ["create_context_bookmark"],
            "data": {"performed": False, "assignmentId": values["assignmentId"]},
            "answer": "provider에서 해당 과제를 찾지 못해 저장하지 않았습니다. 과제 ID를 확인한 뒤 표준 명령어로 다시 입력해 주세요.",
        }
    course = next(
        (item for item in provider.get_courses(USER_ID) if item["id"] == assignment.get("courseId")),
        None,
    )
    if course:
        assignment["courseName"] = course["name"]
    record = save_context_bookmark(
        assignment,
        user_id=USER_ID,
        progress=values["progress"],
        blocker=values["blocker"],
        next_action=values["nextAction"],
        completed_items=values["completedItems"],
        db_path=DB_PATH,
    )
    return {
        "toolCalls": ["create_context_bookmark"],
        "data": record,
        "answer": format_resume_card(record),
    }


def ask(text: str, *, records: str = "", **options) -> dict[str, Any]:
    checkpoint = _checkpoint_command(text)
    if checkpoint:
        return checkpoint

    intent = detect_context_intent(text)
    if intent:
        if intent == "ambiguous":
            answer = (
                "저장과 불러오기는 실행하지 않았습니다. 원하는 작업의 표준 명령어 하나를 입력해 주세요.\n"
                f"저장: {command_template('save')}\n불러오기: {command_template('load')}"
            )
        else:
            action = "저장" if intent == "save" else "불러오기"
            answer = (
                f"요청하신 {action}은 아직 실행하지 않았습니다. 아래 표준 명령어를 채워서 다시 입력해 주세요.\n"
                f"{command_template(intent)}"
            )
            if intent == "save":
                answer += (
                    "\n과제 ID는 `assignments` 명령으로 확인할 수 있습니다. "
                    "막힌 부분이 없으면 `없음`이라고 입력해 주세요. "
                    "완료 항목도 남기려면 `--완료항목 \"완료한 내용\"`을 덧붙이면 됩니다."
                )
            else:
                answer += "\n특정 과제는 `--과제ID <과제ID>`를 덧붙이고, 생략하면 가장 최근 기록을 불러옵니다."
        return {
            "toolCalls": ["prompt_context_command"],
            "data": {"operation": intent, "performed": False},
            "answer": answer,
        }

    if re.search(r"팀플|인수인계|진행 상황|안 끝난 작업|역할.*넘|작업.*담당자", text):
        if not records.strip():
            return {
                "toolCalls": [],
                "data": None,
                "needsInput": True,
                "answer": "분석할 회의록이나 작업 기록을 --records로 입력해주세요.",
            }
        return handover_result(records, **options)
    if re.search(r"과제|안 낸|미제출|밀린", text):
        data = get_assignments(database(), USER_ID, unsubmitted=True)
        answer = "현재 미제출 과제가 없습니다." if not data else "\n".join(f"{x['title']} — 마감 {x['dueAt']}" for x in data)
        return {"toolCalls": ["get_unsubmitted_assignments"], "data": data, "answer": answer}
    if re.search(r"강의|시청|안 본", text):
        data = get_lectures(database(), USER_ID, unfinished=True)
        answer = "\n".join(f"{x['title']} — {x['watchProgress']}%" for x in data) or "미시청 강의가 없습니다."
        return {"toolCalls": ["get_unwatched_lectures"], "data": data, "answer": answer}
    if re.search(r"북마크|즐겨찾기", text):
        data = list_bookmarks(database(), USER_ID)
        return {"toolCalls": ["get_bookmarks"], "data": data, "answer": json.dumps(data, ensure_ascii=False)}
    if re.search(r"컨텍스트|전체|상태", text):
        store = database()
        data = get_current_context(store, USER_ID, lambda: list_bookmarks(store, USER_ID))
        return {"toolCalls": ["get_current_context"], "data": data, "answer": "현재 학업 컨텍스트를 조회했습니다."}
    return {
        "toolCalls": [],
        "data": None,
        "answer": "과제, 강의, 북마크, 학업 컨텍스트, 팀플 진행 상황이나 인수인계를 물어보세요.",
    }


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
    ask_parser.add_argument("--records", default="", help="팀플 분석 대상 기록 (요청과 분리)")
    handover_parser = sub.add_parser("handover")
    handover_parser.add_argument("--text", required=True)
    for target in (ask_parser, handover_parser):
        target.add_argument("--prepare", action="store_true", help="현재 AI용 분석 요청 생성; 결과가 아님")
        target.add_argument("--analysis-json", default=None, help="현재 AI가 생성한 JSON 결과 검증")
        target.add_argument("--project-name", default="")
        target.add_argument("--team", default="", help="팀원 이름과 역할 설명")
        target.add_argument("--assignee", default="", help="작업을 넘기는 현재 담당자")
    args = parser.parse_args()

    if args.command == "context":
        store = database()
        result = {
            "toolCalls": ["get_current_context"],
            "data": get_current_context(store, USER_ID, lambda: list_bookmarks(store, USER_ID)),
        }
    elif args.command == "assignments":
        result = {
            "toolCalls": ["get_assignments"],
            "data": get_assignments(database(), USER_ID, unsubmitted=args.unsubmitted, upcoming=args.upcoming),
        }
    elif args.command == "lectures":
        result = {
            "toolCalls": ["get_lectures"],
            "data": get_lectures(database(), USER_ID, unfinished=args.unfinished),
        }
    elif args.command == "bookmarks":
        result = {"toolCalls": ["get_bookmarks"], "data": list_bookmarks(database(), USER_ID)}
    elif args.command == "bookmark-add":
        result = {
            "toolCalls": ["create_bookmark"],
            "data": add_bookmark(database(), USER_ID, args.target_type, args.target_id, args.note),
        }
    elif args.command == "bookmark-delete":
        result = {"toolCalls": ["delete_bookmark"], "data": delete_bookmark(database(), USER_ID, args.target_id)}
    elif args.command == "ask":
        result = ask(
            args.text,
            records=args.records,
            project_name=args.project_name,
            team=args.team,
            assignee=args.assignee,
            prepare=args.prepare,
            analysis_json=args.analysis_json,
        )
    else:
        result = handover_result(
            args.text,
            project_name=args.project_name,
            team=args.team,
            assignee=args.assignee,
            prepare=args.prepare,
            analysis_json=args.analysis_json,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if DB is not None:
        DB.close()
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
