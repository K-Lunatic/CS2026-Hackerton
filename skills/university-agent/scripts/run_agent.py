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
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.assignments import assignment_answer, get_assignments
from features.assignment_selection import find_assignments, selection_guidance, public_checkpoint
from features.study_materials import study_materials
from features.bookmarks import add_bookmark, delete_bookmark, list_bookmarks, validate_bookmark_target
from features.context import format_current_context, get_current_context
from features.context_bookmarks import format_resume_card, get_context_bookmark, save_context_bookmark
from features.context_commands import command_template, detect_context_intent, parse_context_command
from features.handover import HandoverError, create_handover, format_handover, prepare_handover, is_handover_request
from features.lectures import get_lectures
from features.guidance import guidance_request, is_status_request, usage_guide
from providers.credentials import CONFIG_PATH
from storage.local_db import LocalDatabase

try:
    default_user_id = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["username"]
except (OSError, ValueError, KeyError):
    default_user_id = ""
USER_ID = os.environ.get("UNIVERSITY_AGENT_USER_ID", default_user_id)
DB_PATH = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db")).expanduser()
DB: LocalDatabase | None = None


def database(*, write: bool = False) -> LocalDatabase:
    """Open the device database only when a command actually needs it."""
    global DB
    if not USER_ID:
        raise SystemExit("TLS 계정이 없습니다. python3 scripts/sync_tls.py를 먼저 실행하거나 UNIVERSITY_AGENT_USER_ID를 지정하세요.")
    if DB is None:
        if not write and not DB_PATH.exists():
            raise SystemExit("학사 데이터가 없습니다. python3 scripts/sync_tls.py로 먼저 동기화하세요.")
        DB = LocalDatabase(DB_PATH, read_only=not write)
        if DB.get_user(USER_ID) is None:
            DB.close()
            DB = None
            raise SystemExit("이 사용자의 학사 데이터가 없습니다. python3 scripts/sync_tls.py로 먼저 동기화하세요.")
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


def _checkpoint_data(raw: str | None) -> dict[str, Any]:
    if raw is None:
        raise ValueError("현재 대화에서 정리한 저장 내용이 전달되지 않았습니다.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("저장 내용 형식이 올바른 JSON이 아닙니다.") from None
    if not isinstance(data, dict):
        raise ValueError("저장 내용은 JSON 객체여야 합니다.")

    required = {"progress", "blocker", "nextAction"}
    allowed = required | {"completedItems"}
    missing = required - data.keys()
    unknown = data.keys() - allowed
    if missing or unknown:
        details = []
        if missing:
            details.append(f"필수 항목 누락: {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"알 수 없는 항목: {', '.join(sorted(unknown))}")
        raise ValueError("; ".join(details))
    for key in required:
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key}는 비어 있지 않은 문자열이어야 합니다.")
    completed_items = data.get("completedItems", [])
    if not isinstance(completed_items, list) or any(
        not isinstance(item, str) or not item.strip() for item in completed_items
    ):
        raise ValueError("completedItems는 비어 있지 않은 문자열들의 배열이어야 합니다.")
    return {
        "progress": data["progress"].strip(),
        "blocker": data["blocker"].strip(),
        "nextAction": data["nextAction"].strip(),
        "completedItems": [item.strip() for item in completed_items],
    }


def _checkpoint_command(text: str, *, checkpoint_json: str | None = None) -> dict[str, Any] | None:
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
    assignment = None
    if values:
        matches = find_assignments(database(), USER_ID, values)
        if len(matches) != 1:
            return selection_guidance(matches, operation)
        assignment = matches[0]
    if operation == "load":
        if checkpoint_json is not None:
            return {
                "toolCalls": ["get_context_bookmark"],
                "data": {"performed": False},
                "answer": "불러오기 명령에는 저장 데이터가 필요하지 않습니다. 저장하지 않았습니다.",
            }
        record = get_context_bookmark(
            user_id=USER_ID,
            assignment_id=assignment["id"] if assignment else None,
            db_path=DB_PATH,
        )
        return {
            "toolCalls": ["get_context_bookmark"],
            "data": public_checkpoint(record),
            "answer": format_resume_card(record) if record else "해당 과제에 저장된 진행 기록이 없습니다.",
        }

    if checkpoint_json is None:
        return {
            "toolCalls": ["find_assignments"],
            "needsSummary": True,
            "data": {"performed": False, "courseName": assignment.get("courseName"), "assignmentTitle": assignment["title"]},
            "answer": f"{assignment['title']} 과제를 찾았습니다. 현재 대화의 진행 내용을 정리한 뒤 저장할 수 있습니다.",
        }

    try:
        checkpoint = _checkpoint_data(checkpoint_json)
    except ValueError as exc:
        return {
            "toolCalls": ["create_context_bookmark"],
            "data": {"performed": False},
            "answer": (
                f"체크포인트를 저장하지 않았습니다. {exc} "
                "ChatGPT/Codex가 현재 대화에서 확인되는 내용을 한국어로 정리해 전달해야 합니다."
            ),
        }

    record = save_context_bookmark(
        assignment,
        user_id=USER_ID,
        progress=checkpoint["progress"],
        blocker=checkpoint["blocker"],
        next_action=checkpoint["nextAction"],
        completed_items=checkpoint["completedItems"],
        db_path=DB_PATH,
    )
    return {
        "toolCalls": ["create_context_bookmark"],
        "data": public_checkpoint(record),
        "answer": "진행 기록을 저장했습니다.\n" + format_resume_card(record),
    }


def ask(
    text: str,
    *,
    records: str = "",
    ui: bool = False,
    checkpoint_json: str | None = None,
    **options,
) -> dict[str, Any]:
    checkpoint = _checkpoint_command(text, checkpoint_json=checkpoint_json)
    if checkpoint:
        return checkpoint

    guidance = guidance_request(text)
    if guidance:
        return guidance

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
                    "\n예: 과제 저장 자바 Ex05. 과목명이나 제목의 일부만 입력해도 됩니다. "
                    "ChatGPT/Codex가 명령을 받은 뒤 현재 대화에서 확인되는 내용을 한국어로 정리합니다. "
                    "사용자가 진행·막힘·다음 행동을 직접 입력할 필요는 없습니다."
                )
            else:
                answer += "\n예: 과제 불러오기 자바 Ex05. 키워드를 생략하면 가장 최근 기록을 불러옵니다."
        return {
            "toolCalls": ["prompt_context_command"],
            "data": {"operation": intent, "performed": False},
            "answer": answer,
        }

    if is_handover_request(text):
        if ui:
            url = "http://127.0.0.1:8765/?" + urlencode({"request": text})
            return {"toolCalls": ["open_handover"], "data": {"url": url},
                    "answer": "팀플 화면에서 자료를 입력하고 결과를 수정하세요: " + url}
        if not records.strip():
            return {
                "toolCalls": [],
                "data": None,
                "needsInput": True,
                "answer": "회의 내용이나 작업 메모를 보내주세요. 누가 무엇을 했고 무엇이 남았는지 정리해 드릴게요. 예: ‘민수는 로그인 구현 완료, 지수는 발표 자료 작성 중.’",
            }
        return handover_result(records, **options)
    if re.search(r"과제|안 낸|미제출|밀린", text):
        return assignment_answer(database(), USER_ID, text)
    if re.search(r"강의|시청|안 본", text):
        data = get_lectures(database(), USER_ID, unfinished=True)
        answer = "\n".join(f"{x['title']} — {x['watchProgress']}%" for x in data) or "미시청 강의가 없습니다."
        return {"toolCalls": ["get_unwatched_lectures"], "data": data, "answer": answer}
    if re.search(r"공지", text):
        data = database().get_notices(USER_ID)
        return {"toolCalls": ["get_notices"], "data": data, "answer": json.dumps(data, ensure_ascii=False)}
    if re.search(r"자료|파일|PDF|PPT", text, re.I):
        data = database().get_resources(USER_ID)
        return {"toolCalls": ["get_resources"], "data": data, "answer": json.dumps(data, ensure_ascii=False)}
    if re.search(r"북마크|즐겨찾기", text):
        data = list_bookmarks(database(), USER_ID)
        return {"toolCalls": ["get_bookmarks"], "data": data, "answer": json.dumps(data, ensure_ascii=False)}
    if is_status_request(text):
        store = database()
        data = get_current_context(store, USER_ID, lambda: list_bookmarks(store, USER_ID))
        return {"toolCalls": ["get_current_context"], "data": data, "answer": format_current_context(data)}
    return usage_guide(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="터틀넥 local university skill")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("context")
    assignment_parser = sub.add_parser("assignments")
    assignment_parser.add_argument("--unsubmitted", action="store_true")
    assignment_parser.add_argument("--upcoming", action="store_true")
    assignment_parser.add_argument("--this-week", action="store_true")
    assignment_parser.add_argument("--overdue", action="store_true")
    find_parser = sub.add_parser("assignment-find", help="과목명이나 과제 키워드로 저장 대상을 찾기")
    find_parser.add_argument("--query", default="")
    find_parser.add_argument("--operation", choices=("save", "load"), default="save")
    manual_add = sub.add_parser("assignment-add", help="TLS에 없는 과제를 로컬에 등록")
    manual_add.add_argument("--title", required=True)
    manual_add.add_argument("--course-id")
    manual_add.add_argument("--due-at")
    manual_add.add_argument("--description")
    manual_complete = sub.add_parser("assignment-complete", help="직접 등록한 과제를 완료 처리")
    manual_complete.add_argument("--id", required=True)
    manual_delete = sub.add_parser("assignment-delete", help="직접 등록한 과제를 삭제")
    manual_delete.add_argument("--id", required=True)
    lecture_parser = sub.add_parser("lectures")
    lecture_parser.add_argument("--unfinished", action="store_true")
    sub.add_parser("notices")
    sub.add_parser("todos")
    sub.add_parser("resources")
    study_parser = sub.add_parser("study-materials", help="다운로드한 과목 자료를 학습 자료 생성용으로 읽기")
    study_parser.add_argument("--course", default="", help="과목명 또는 일부")
    study_parser.add_argument("--resource", default="", help="자료 제목 또는 파일명 일부")
    study_parser.add_argument("--max-chars", type=int, default=30000)
    sub.add_parser("bookmarks")
    add = sub.add_parser("bookmark-add")
    add.add_argument("--target-type", required=True)
    add.add_argument("--target-id", required=True)
    add.add_argument("--note", default="")
    remove = sub.add_parser("bookmark-delete")
    remove.add_argument("--target-id", required=True)
    ask_parser = sub.add_parser("ask")
    ask_parser.add_argument("--text", required=True)
    ask_parser.add_argument("--ui", action="store_true", help="실행 중인 로컬 팀플 화면 링크 반환")
    ask_parser.add_argument("--records", default="", help="팀플 분석 대상 기록 (요청과 분리)")
    ask_parser.add_argument("--checkpoint-json", default=None, help=argparse.SUPPRESS)
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
            "data": get_assignments(database(), USER_ID, unsubmitted=args.unsubmitted, upcoming=args.upcoming, this_week=args.this_week, overdue=args.overdue),
        }
    elif args.command == "assignment-find":
        selectors = {"query": args.query} if args.query.strip() else {}
        result = selection_guidance(find_assignments(database(), USER_ID, selectors), args.operation)
    elif args.command == "assignment-add":
        try:
            item = database(write=True).add_manual_assignment(USER_ID, args.title, course_id=args.course_id, due_at=args.due_at, description=args.description)
            result = {"toolCalls": ["add_manual_assignment"], "data": item, "answer": "과제를 등록했습니다."}
        except ValueError as error:
            result = {"toolCalls": ["add_manual_assignment"], "data": None, "error": {"code": "INVALID_ASSIGNMENT", "message": str(error)}, "answer": str(error)}
    elif args.command == "assignment-complete":
        done = database(write=True).complete_manual_assignment(USER_ID, args.id)
        result = {"toolCalls": ["complete_manual_assignment"], "data": {"completed": done, "id": args.id}, "answer": "완료 처리했습니다." if done else "직접 등록한 과제를 찾지 못했습니다."}
    elif args.command == "assignment-delete":
        deleted = database(write=True).delete_manual_assignment(USER_ID, args.id)
        result = {"toolCalls": ["delete_manual_assignment"], "data": {"deleted": deleted, "id": args.id}, "answer": "삭제했습니다." if deleted else "직접 등록한 과제를 찾지 못했습니다."}
    elif args.command == "lectures":
        result = {
            "toolCalls": ["get_lectures"],
            "data": get_lectures(database(), USER_ID, unfinished=args.unfinished),
        }
    elif args.command == "todos":
        result = {"toolCalls": ["get_todos"], "data": database().get_todos(USER_ID)}
    elif args.command == "notices":
        result = {"toolCalls": ["get_notices"], "data": database().get_notices(USER_ID)}
    elif args.command == "resources":
        result = {"toolCalls": ["get_resources"], "data": database().get_resources(USER_ID)}
    elif args.command == "study-materials":
        if not 1000 <= args.max_chars <= 80000:
            parser.error("--max-chars는 1000~80000 사이여야 합니다.")
        store = database()
        result = study_materials(
            store.get_courses(USER_ID), store.get_resources(USER_ID),
            files_root=DB_PATH.parent / "files", course_query=args.course,
            resource_query=args.resource, max_chars=args.max_chars,
        )
    elif args.command == "bookmarks":
        result = {"toolCalls": ["get_bookmarks"], "data": list_bookmarks(database(), USER_ID)}
    elif args.command == "bookmark-add":
        try:
            validate_bookmark_target(args.target_type)
            result = {
                "toolCalls": ["create_bookmark"],
                "data": add_bookmark(database(write=True), USER_ID, args.target_type, args.target_id, args.note),
            }
        except ValueError as error:
            result = {"toolCalls": [], "data": {"performed": False}, "error": {"code": "INVALID_BOOKMARK", "message": str(error)}, "answer": str(error)}
    elif args.command == "bookmark-delete":
        result = {"toolCalls": ["delete_bookmark"], "data": delete_bookmark(database(write=True), USER_ID, args.target_id)}
    elif args.command == "ask":
        result = ask(
            args.text,
            records=args.records,
            ui=args.ui,
            checkpoint_json=args.checkpoint_json,
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
