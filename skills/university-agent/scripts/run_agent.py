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

from features.assignments import assignment_answer, get_assignments
from features.assignment_selection import find_assignments, find_similar_tls_assignments, selection_guidance, public_checkpoint, normalize
from features.study_materials import study_materials
from features.bookmarks import add_bookmark, delete_bookmark, list_bookmarks, validate_bookmark_target
from features.context import format_current_context, get_current_context
from features.context_bookmarks import format_resume_card, get_context_bookmark, list_unfinished_context_bookmarks, save_context_bookmark
from features.context_commands import command_template, detect_context_intent, parse_context_command, with_next_commands
from features.study import StudySession, study_intent, event_from_text
from features.lectures import get_lectures
from features.guidance import guidance_request, is_status_request, usage_guide
from features.deadlines import format_deadline
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
    if DB is not None and write and DB.read_only:
        DB.close()
        DB = None
    if DB is None:
        if not write and not DB_PATH.exists():
            raise SystemExit("학사 데이터가 없습니다. python3 scripts/sync_tls.py로 먼저 동기화하세요.")
        DB = LocalDatabase(DB_PATH, read_only=not write)
        if DB.get_user(USER_ID) is None:
            DB.close()
            DB = None
            raise SystemExit("이 사용자의 학사 데이터가 없습니다. python3 scripts/sync_tls.py로 먼저 동기화하세요.")
    return DB


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
    # Natural-language conversation requests must reach the guidance path without opening SQLite.
    if re.search(r"\b(?:save|bookmark)\s+(?:this|my)\s+(?:chat|conversation)\b", text, re.I):
        return None
    command = parse_context_command(text)
    if command is None:
        return None
    operation = command["operation"]
    if operation == "list":
        if checkpoint_json is not None:
            return {"toolCalls": [], "data": {"performed": False}, "answer": "목록 조회에는 저장 내용이 필요하지 않습니다."}
        records = (list_unfinished_context_bookmarks(database(), user_id=USER_ID, db_path=DB_PATH)
                   if USER_ID and DB_PATH.exists() else [])
        public = [public_checkpoint(item) for item in records]
        lines = [f"저장된 미완성 과제: {len(public)}개"]
        lines.extend(f"• {item['courseName'] or '기타 과제'} / {item['assignmentTitle']} — {format_deadline(item['savedAt'])}" for item in public)
        return {"toolCalls": ["list_context_bookmarks"], "data": public, "answer": "\n".join(lines)}
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
    if "newTitle" in values:
        title = values["newTitle"].strip()
        if not title or len(title) > 200:
            return {"toolCalls": [], "data": {"performed": False}, "answer": "새 과제 제목은 1~200자로 입력해 주세요."}
        existing_manual = [item for item in find_assignments(database(), USER_ID, {"title": title, "source": "manual"})
                           if normalize(item["title"]) == normalize(title)]
        if len(existing_manual) > 1:
            return selection_guidance(existing_manual, "save")
        if existing_manual:
            assignment = existing_manual[0]
        else:
            existing_tls = find_assignments(database(), USER_ID, {"title": title, "source": "tls"})
            if existing_tls:
                return selection_guidance(existing_tls, "save")
        if checkpoint_json is None:
            return {"toolCalls": [], "needsSummary": True,
                    "data": {"performed": False, "assignmentTitle": title},
                    "answer": f"새 과제 ‘{title}’의 현재 대화 내용을 정리한 뒤 저장할 수 있습니다."}
    elif values:
        matches = find_assignments(database(), USER_ID, values)
        if len(matches) != 1:
            return selection_guidance(matches, operation)
        assignment = matches[0]
        if assignment["submissionStatus"] in {"SUBMITTED", "LATE"}:
            return {"toolCalls": [], "data": None if operation == "load" else {"performed": False},
                    "answer": "완료된 과제에는 저장된 진행 기록이 없습니다." if operation == "load" else "완료된 과제에는 진행 기록을 저장할 수 없습니다."}
    if operation == "load":
        if checkpoint_json is not None:
            return {
                "toolCalls": ["get_context_bookmark"],
                "data": {"performed": False},
                "answer": "불러오기 명령에는 저장 데이터가 필요하지 않습니다. 저장하지 않았습니다.",
            }
        record = (get_context_bookmark(user_id=USER_ID, assignment_id=assignment["id"], db_path=DB_PATH)
                  if assignment else (next(iter(list_unfinished_context_bookmarks(database(), user_id=USER_ID, db_path=DB_PATH)), None)
                                      if USER_ID and DB_PATH.exists() else None))
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

    if "newTitle" in values and assignment is None:
        assignment = database(write=True).add_manual_assignment(USER_ID, values["newTitle"])
        assignment["courseName"] = "기타 과제"
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


def _ask(
    text: str,
    *,
    conversation: str = "",
    checkpoint_json: str | None = None,
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
        elif intent == "list":
            answer = "목록을 아직 조회하지 않았습니다. 정확히 `list`를 입력해 주세요."
        else:
            action = "저장" if intent == "save" else "불러오기"
            answer = (
                f"요청하신 {action}은 아직 실행하지 않았습니다. 아래 표준 명령어를 채워서 다시 입력해 주세요.\n"
                f"{command_template(intent)}"
            )
            if intent == "save":
                answer += (
                    '\n예: save 자바 Ex05 또는 save new "캡처 문제". '
                    "ChatGPT/Codex가 명령을 받은 뒤 현재 대화에서 확인되는 내용을 한국어로 정리합니다. "
                    "사용자가 진행·막힘·다음 행동을 직접 입력할 필요는 없습니다."
                )
            else:
                answer += '\n예: load "자바 Ex05". 키워드를 생략하면 가장 최근 기록을 불러옵니다.'
        return {
            "toolCalls": ["prompt_context_command"],
            "data": {"operation": intent, "performed": False},
            "answer": answer,
        }

    learning = study_intent(text)
    if learning:
        if not conversation:
            return {"toolCalls": ["study"], "needsConversation": True,
                    "answer": "학습 대화를 구분할 세션 ID가 필요합니다. 호출한 AI가 이 대화에서만 유지할 ID를 만들어 다시 호출하세요."}
        event = event_from_text(text, database().get_courses(USER_ID))
        try:
            response = StudySession(DB_PATH.parent / "study-sessions.db", USER_ID, conversation,
                                    database(), DB_PATH.parent / "files").call(event)
        except ValueError as exc:
            response = {"status": "error", "answer": str(exc)}
        return {"toolCalls": ["study"], **response}
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


def ask(text: str, **options) -> dict[str, Any]:
    result = _ask(text, **options)
    conversation = options.get("conversation")
    if (conversation and result.get("toolCalls") != ["study"] and USER_ID and DB_PATH.exists()
            and (DB_PATH.parent / "study-sessions.db").exists()):
        StudySession(DB_PATH.parent / "study-sessions.db", USER_ID, conversation,
                     database(), DB_PATH.parent / "files").call({"action": "observe"})
    return result if result.get("toolCalls") == ["study"] else with_next_commands(result)


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
    find_parser.add_argument("--source", choices=("tls", "manual"))
    manual_add = sub.add_parser("assignment-add", help="TLS에 없는 과제를 로컬에 등록")
    manual_add.add_argument("--title", required=True)
    manual_add.add_argument("--course-id")
    manual_add.add_argument("--due-at")
    manual_add.add_argument("--description")
    manual_complete = sub.add_parser("assignment-complete", help="제출을 확인한 직접 등록 과제를 완료 처리")
    manual_complete.add_argument("--id", required=True)
    manual_complete.add_argument("--submission-answer")
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
    ask_parser.add_argument("--conversation", default="", help="호스트 대화별 고유 ID")
    ask_parser.add_argument("--checkpoint-json", default=None, help=argparse.SUPPRESS)
    study_parser = sub.add_parser("study", help="학습 세션 이벤트 처리")
    study_parser.add_argument("--conversation", required=True, help="호스트 대화별 고유 ID")
    study_parser.add_argument("--event-json", required=True, help="학습 이벤트 JSON")
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
        if args.source:
            selectors["source"] = args.source
        matches = (find_similar_tls_assignments(database(), USER_ID, args.query)
                   if args.source == "tls" else find_assignments(database(), USER_ID, selectors))
        result = selection_guidance(matches, args.operation)
    elif args.command == "assignment-add":
        try:
            item = database(write=True).add_manual_assignment(USER_ID, args.title, course_id=args.course_id, due_at=args.due_at, description=args.description)
            result = {"toolCalls": ["add_manual_assignment"], "data": item, "answer": "과제를 등록했습니다."}
        except ValueError as error:
            result = {"toolCalls": ["add_manual_assignment"], "data": None, "error": {"code": "INVALID_ASSIGNMENT", "message": str(error)}, "answer": str(error)}
    elif args.command == "assignment-complete":
        if args.submission_answer == "예":
            done = database(write=True).complete_manual_assignment(USER_ID, args.id, submission_answer="예")
            result = {"toolCalls": ["complete_manual_assignment"], "data": {"completed": done, "id": args.id}, "answer": "제출을 확인해 완료 처리하고 저장 기록을 제거했습니다." if done else "직접 등록한 과제를 찾지 못했습니다."}
        elif args.submission_answer == "아니요":
            result = {"toolCalls": [], "data": {"completed": False}, "answer": "아직 제출하지 않은 과제로 기록을 유지했습니다."}
        else:
            result = {"toolCalls": [], "data": {"completed": False}, "needsInput": True,
                      "answer": "과제를 제출했나요? 예 또는 아니요로만 답해 주세요."}
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
        result = ask(args.text, conversation=args.conversation, checkpoint_json=args.checkpoint_json)
    elif args.command == "study":
        try:
            event = json.loads(args.event_json)
            store = database()
            result = {"toolCalls": ["study"], **StudySession(DB_PATH.parent / "study-sessions.db", USER_ID,
                args.conversation, store, DB_PATH.parent / "files").call(event)}
        except (ValueError, json.JSONDecodeError) as exc:
            result = {"toolCalls": ["study"], "status": "error", "error": {"code": "STUDY_INVALID", "message": str(exc)}, "answer": str(exc)}
    if args.command not in ("ask", "study"):
        result = with_next_commands(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if DB is not None:
        DB.close()
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
