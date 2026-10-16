#!/usr/bin/env python3
"""Dependency-free runner backed by a private device-local SQLite database."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

# Host pipes on Windows may use cp1252/cp949; all runner JSON is UTF-8.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='backslashreplace')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.assignments import assignment_answer, get_assignments
from features.assignment_selection import find_assignments, search_save_targets, selection_guidance, public_checkpoint, normalize
from features.study_materials import study_materials, original_files
from features.bookmarks import add_bookmark, bookmark_answer, delete_bookmark, list_bookmarks, validate_bookmark_target
from features.context import format_current_context, get_current_context
from features.context_bookmarks import format_resume_card, get_context_bookmark, list_unfinished_context_bookmarks, save_context_bookmark
from features.context_commands import command_template, detect_context_intent, parse_context_command, with_next_commands
from features.study import StudySession, study_intent, event_from_text
from features import analysis_records, study_pack
from features.concept_insights import build_insights
from features.learning_plan import build_plan
from features.lectures import get_lectures
from features.lecture_audio import select_lectures
from features.guidance import academic_list_answer, guidance_request, is_status_request, usage_guide
from features.deadlines import format_deadline
from features.moodle_xml import MoodleXmlError, export_saved_exam, reference_to_xml
from features.autonomous_course_policy import AutonomousCoursePolicyError, verify_course_reference
from scripts.assemble_moodle_backup import MoodleBackupAssemblyError, assemble_backup
from scripts.rebuild_moodle_backup import MoodleBackupRebuildError, rebuild_backup
from scripts.restore_moodle_backup import MoodleBackupRestoreError, restore_backup
from scripts.update_autonomous_course import CourseUpdateError, update_course_settings
from scripts.update_autonomous_section import SectionUpdateError, update_section_name
from scripts.inspect_tls_activity_forms import inspect_forms
from scripts.control_tls_activities import ActivityControlError, control_activities
from scripts.control_tls_quiz_questions import QuizQuestionError, control_questions, inspect_question_forms
from providers.moodle_session import LoginError
from providers.forms import FormUnavailable
from features.secret_boundary import redact_output
from providers.credentials import CONFIG_PATH
from storage.local_db import LocalDatabase

try:
    default_user_id = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["username"]
except (OSError, ValueError, KeyError):
    default_user_id = ""
DB_PATH = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db")).expanduser()
DEVICE_ID_PATH = DB_PATH.parent / "device-id"


def _load_device_id() -> str:
    try:
        value = DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
        return value if re.fullmatch(r"local-[a-f0-9]{16}", value) else ""
    except OSError:
        return ""


USER_ID = os.environ.get("UNIVERSITY_AGENT_USER_ID", default_user_id or _load_device_id())
DB: LocalDatabase | None = None


def database(*, write: bool = False) -> LocalDatabase:
    """Open the device database only when a command actually needs it."""
    global DB
    if not USER_ID:
        raise SystemExit("아직 학교 계정이 연결되지 않았어요. 먼저 터틀넥 연결 화면에서 학교 계정을 연결하거나 학습 팩을 가져와 주세요.")
    if DB is not None and write and DB.read_only:
        DB.close()
        DB = None
    if DB is None:
        if not write and not DB_PATH.exists():
            raise SystemExit("아직 이 컴퓨터에 학사 정보가 없어요. ‘TLS 새로고침해줘’라고 요청하면 동기화 후 다시 조회할 수 있어요.")
        DB = LocalDatabase(DB_PATH, read_only=not write)
        if DB.get_user(USER_ID) is None:
            DB.close()
            DB = None
            raise SystemExit("이 계정의 학사 정보가 아직 없어요. ‘TLS 새로고침해줘’라고 요청해 동기화한 뒤 다시 확인해 주세요.")
    return DB


def _open_exam_web(result: dict[str, Any], conversation: str) -> dict[str, Any]:
    """Open the required local exam UI when a study event produces a web exam."""
    if result.get('needsWeb') is not True or not conversation or result.get('webOpened'):
        return result
    command = [sys.executable, str(ROOT / 'scripts' / 'exam_web.py'), '--conversation', conversation]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding='utf-8', errors='replace', bufsize=1)
        line = process.stdout.readline().strip() if process.stdout else ''
        payload = json.loads(line) if line else {}
        if not isinstance(payload, dict) or not payload.get('url'):
            raise ValueError('시험 화면 주소를 받지 못했어요.')
        return {**result, 'webOpened': True, 'webUrl': payload['url'],
                'answer': payload.get('answer') or '시험지가 준비됐어요. 열린 시험 화면에서 풀어 주세요.'}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {**result, 'webOpened': False,
                'answer': result.get('answer', '시험지가 준비됐어요.') +
                          f' 시험 화면을 자동으로 열지 못했어요. 로컬 실행기를 다시 열어 주세요. ({exc})'}


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
    if operation == "list":
        if checkpoint_json is not None:
            return {"toolCalls": [], "data": {"performed": False}, "answer": "목록 조회에는 저장 내용이 필요하지 않습니다."}
        records = (list_unfinished_context_bookmarks(database(), user_id=USER_ID, db_path=DB_PATH)
                   if USER_ID and DB_PATH.exists() else [])
        public = [public_checkpoint(item) for item in records]
        lines = [f"저장된 미완성 과제: {len(public)}개"]
        lines.extend(f"• {item['courseName'] or '기타 과제'} / {item['assignmentTitle']} — {format_deadline(item['savedAt'])}" for item in public)
        if not public:
            lines.append("아직 저장된 진행 기록이 없어요. 과제를 진행하다 ‘과제 진행 상황 저장해줘’라고 말해 주세요.")
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
        if checkpoint_json is None and assignment is None:
            return {"toolCalls": [], "needsSummary": True,
                    "data": {"performed": False, "assignmentTitle": title},
                    "answer": f"새 과제 ‘{title}’의 현재 대화 내용을 정리한 뒤 저장할 수 있습니다."}
    elif values:
        matches = find_assignments(database(), USER_ID, values, prefer_exact_title=True)
        if len(matches) != 1:
            return selection_guidance(matches, operation)
        assignment = matches[0]
    if assignment and assignment["submissionStatus"] in {"SUBMITTED", "LATE"}:
        return ({"toolCalls": [], "data": None, "answer": "완료된 과제에는 저장된 진행 기록이 없습니다."}
                if operation == "load" else selection_guidance([assignment], operation))
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
            "answer": format_resume_card(record) if record else "해당 과제에 저장된 진행 기록이 없어요. `list`로 다른 기록을 확인하거나, 현재 대화에서 진행한 내용을 새로 저장할 수 있어요.",
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
                "현재 대화의 AI가 확인되는 내용을 한국어로 정리해 전달해야 합니다."
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
        "answer": "진행 기록을 저장했습니다.\n\n" + format_resume_card(record),
    }


def _ask(
    text: str,
    *,
    conversation: str = "",
    checkpoint_json: str | None = None,
    assignment_query: str | None = None,
) -> dict[str, Any]:
    checkpoint = _checkpoint_command(text, checkpoint_json=checkpoint_json)
    if checkpoint:
        return checkpoint

    guidance = guidance_request(text)
    if guidance:
        return guidance

    intent = detect_context_intent(text)
    if intent:
        if intent == "save":
            if assignment_query and assignment_query.strip():
                return search_save_targets(database(), USER_ID, assignment_query)
            return {
                "toolCalls": ["prompt_context_command"], "needsInput": True, "needsAssignmentQuery": True,
                "data": {"operation": "save", "performed": False, "stage": "need_clue"},
                "nextCommands": [],
                "answer": "저장할 과제를 먼저 찾아볼게요. 과목명이나 문제에 나온 단어를 알려주세요. "
                          "직접 정한 이름으로 남기려면 ‘새 이름: 내 과제’처럼 말해 주세요.",
            }
        if intent == "ambiguous":
            answer = (
                "저장과 불러오기는 실행하지 않았습니다. 원하는 작업의 표준 명령어 하나를 입력해 주세요.\n"
                f"저장: {command_template('save')}\n불러오기: {command_template('load')}"
            )
        elif intent == "list":
            answer = "저장해 둔 과제의 진행 기록 목록은 `list`를 보내면 불러올 수 있어요."
        else:
            answer = (
                '최근 저장 기록부터 이어가려면 `load`를 보내 주세요. 특정 과제는 `load "과제명"`으로 고를 수 있어요.'
            )
        return {
            "toolCalls": ["prompt_context_command"],
            "data": {"operation": intent, "performed": False},
            "answer": answer,
        }

    learning = study_intent(text)
    if learning:
        if learning not in ('save_context_note', 'list_context_notes') and not conversation:
            return {"toolCalls": ["study"], "needsConversation": True,
                    "answer": "학습 대화를 구분할 세션 ID가 필요합니다. 호출한 AI가 이 대화에서만 유지할 ID를 만들어 다시 호출하세요."}
        event = event_from_text(text, database().get_courses(USER_ID))
        try:
            response = StudySession(DB_PATH.parent / "study-sessions.db", USER_ID, conversation or 'context-notes',
                                    database(), DB_PATH.parent / "files").call(event)
        except ValueError as exc:
            response = {"status": "error", "answer": str(exc)}
        return {"toolCalls": ["study"], **response}
    if is_status_request(text) and re.search(r"뭐|무엇|뭘|할\s*일|어때", text):
        store = database()
        data = get_current_context(store, USER_ID, lambda: list_bookmarks(store, USER_ID))
        return {"toolCalls": ["get_current_context"], "data": data, "answer": format_current_context(data)}
    if re.search(r"공지|시험\s*(?:일정|범위)|(?:강의|수업)\s*계획|주차별\s*일정", text):
        store = database()
        data = store.get_notices(USER_ID)
        return {"toolCalls": ["get_notices"], "data": data, "answer": academic_list_answer(store, USER_ID, "notices", data)}
    if re.search(r"자료(?:\s|$|를|로|목록)|수업자료|강의자료|파일|PDF|PPT", text, re.I):
        store = database()
        data = store.get_resources(USER_ID)
        return {"toolCalls": ["get_resources"], "data": data, "answer": academic_list_answer(store, USER_ID, "resources", data)}
    if re.search(r"북마크|즐겨찾기", text):
        store = database()
        data = list_bookmarks(store, USER_ID)
        return {"toolCalls": ["get_bookmarks"], "data": data, "answer": bookmark_answer(store, USER_ID, data)}
    if re.search(r"과제|안 낸|미제출|밀린", text):
        return assignment_answer(database(), USER_ID, text)
    if re.search(r"강의|시청|안 본", text):
        store = database()
        data = get_lectures(store, USER_ID, unfinished=True)
        answer = academic_list_answer(store, USER_ID, "lectures", data)
        return {"toolCalls": ["get_unwatched_lectures"], "data": data, "answer": answer}
    if is_status_request(text):
        store = database()
        data = get_current_context(store, USER_ID, lambda: list_bookmarks(store, USER_ID))
        return {"toolCalls": ["get_current_context"], "data": data, "answer": format_current_context(data)}
    return usage_guide(text)


def ask(text: str, **options) -> dict[str, Any]:
    result = _ask(text, **options)
    conversation = options.get("conversation")
    if result.get("toolCalls") == ["study"]:
        result = _open_exam_web(result, conversation)
    if (conversation and result.get("toolCalls") != ["study"] and USER_ID and DB_PATH.exists()
            and (DB_PATH.parent / "study-sessions.db").exists()):
        StudySession(DB_PATH.parent / "study-sessions.db", USER_ID, conversation,
                     database(), DB_PATH.parent / "files").call({"action": "observe"})
    return result if result.get("toolCalls") == ["study"] else with_next_commands(result)


def main() -> None:
    global USER_ID
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
    audio_parser = sub.add_parser('lecture-audio', help='저장된 강의 목록에서 복습 음성 대상을 확인')
    audio_parser.add_argument('--course', default='')
    audio_parser.add_argument('--lecture', default='')
    audio_parser.add_argument('--id', action='append', default=[])
    audio_parser.add_argument('--all-completed', action='store_true')
    sub.add_parser("notices")
    sub.add_parser("todos")
    sub.add_parser("resources")
    profile_parser = sub.add_parser("learning-profile", help="여러 채팅에서 공유하는 학습 프로필 조회·저장")
    profile_parser.add_argument("--updates-json", default=None, help="사용자가 직접 확인한 프로필 항목 JSON")
    originals = sub.add_parser('resource-file', help='강의 원본 파일 제공')
    originals.add_argument('--course', default='')
    originals.add_argument('--resource', default='')
    originals.add_argument('--id', default='')
    analysis = sub.add_parser('analysis-history', help='자료별 분석 기록 조회')
    analysis.add_argument('--id', default='', help='분석 기록 ID: 지정하면 상세 조회')
    analysis.add_argument('--resource-id', default='')
    analysis.add_argument('--offset', type=int, default=0)
    analysis.add_argument('--limit', type=int, default=20)
    pack = sub.add_parser('study-pack', help='분석 결과와 학습 세트를 기기 사이에서 이동')
    pack_mode = pack.add_subparsers(dest='pack_action', required=True)
    pack_export = pack_mode.add_parser('export', help='원본 파일과 계정 정보 없이 내보내기')
    pack_export.add_argument('--output', type=Path, required=True)
    pack_export.add_argument('--course', default='')
    pack_export.add_argument('--include-notes', action='store_true')
    pack_import = pack_mode.add_parser('import', help='학습 팩 가져오기')
    pack_import.add_argument('--input', dest='input_path', type=Path, required=True)
    pack_import.add_argument('--user-id', default='')
    past_exams = sub.add_parser('exam-history', help='보관된 시험 목록')
    past_exams.add_argument('--offset', type=int, default=0)
    past_exams.add_argument('--limit', type=int, default=20)
    moodle_xml = sub.add_parser('tls-quiz-xml', help='저장된 시험지를 Moodle XML로 내보내기')
    moodle_xml.add_argument('--exam-id', required=True)
    moodle_xml.add_argument('--output', type=Path, required=True)
    moodle_xml.add_argument('--category', default='$course$/top/터틀넥')
    reference_xml = sub.add_parser('reference-xml', help='사용자 참고자료를 로컬 Moodle XML로 보관')
    reference_xml.add_argument('--input', type=Path, required=True)
    reference_xml.add_argument('--output', type=Path, required=True)
    reference_xml.add_argument('--category', default='$course$/top/터틀넥')
    backup_assemble = sub.add_parser('tls-backup-assemble', help='검증된 로컬 Moodle 백업에 시험 활동을 추가')
    backup_assemble.add_argument('--template', type=Path, required=True)
    backup_assemble.add_argument('--questions', type=Path, required=True)
    backup_assemble.add_argument('--section', required=True)
    backup_assemble.add_argument('--quiz-name', required=True)
    backup_assemble.add_argument('--output', type=Path, required=True)
    backup_rebuild = sub.add_parser('tls-backup-rebuild', help='수강 과목 기준으로 기존 Moodle 백업을 비우고 토픽을 재구성')
    backup_rebuild.add_argument('--template', type=Path, required=True)
    backup_rebuild.add_argument('--subject', action='append', default=[], help='직접 지정할 과목명; 생략하면 로컬 TLS 수강 목록 사용')
    backup_rebuild.add_argument('--output', type=Path, required=True)
    backup_restore = sub.add_parser('tls-backup-restore', help='검증된 백업을 지정한 소유 자율강좌로 복구')
    backup_restore.add_argument('--backup', type=Path, required=True)
    backup_restore.add_argument('--course-url', required=True)
    backup_restore.add_argument('--apply', action='store_true', help='파일을 TLS 복구 마법사에 전송')
    backup_restore.add_argument('--execute', action='store_true', help='기존 강좌 내용을 삭제 후 복구')
    autonomous_verify = sub.add_parser('autonomous-course-verify', help='지정한 TLS 자율강좌 주소를 로컬 수강 목록과 대조')
    autonomous_verify.add_argument('--url', required=True)
    course_update = sub.add_parser('tls-course-update', help='관찰된 TLS 강좌 설정 저장 엔드포인트로 안전한 설정 수정')
    course_update.add_argument('--course-url', required=True)
    course_update.add_argument('--set', dest='settings', action='append', default=[], metavar='FIELD=VALUE')
    section_update = sub.add_parser('tls-section-update', help='화면에서 확인한 TLS 토픽 편집 링크로 이름 수정')
    section_update.add_argument('--course-url', required=True)
    section_update.add_argument('--section', required=True)
    section_update.add_argument('--name', required=True)
    activity_inspect = sub.add_parser('tls-activity-inspect', help='TLS 활동 추가 양식을 읽기 전용으로 점검')
    activity_inspect.add_argument('--course-id', required=True)
    activity_control = sub.add_parser('tls-activity-control', help='TLS 활동 여러 개를 한 계획으로 점검·저장')
    activity_control.add_argument('--course-id', required=True)
    activity_control.add_argument('--plan', required=True, type=Path)
    activity_control.add_argument('--apply', action='store_true', help='계획을 실제 TLS에 저장')
    quiz_question_inspect = sub.add_parser('tls-quiz-question-inspect', help='실제 TLS 퀴즈 문항 양식을 읽기 전용으로 점검')
    quiz_question_inspect.add_argument('--quiz-id', required=True)
    quiz_question_control = sub.add_parser('tls-quiz-question-control', help='실제 TLS 퀴즈 문제은행 문항을 계획대로 점검·저장')
    quiz_question_control.add_argument('--quiz-id', required=True)
    quiz_question_control.add_argument('--plan', required=True, type=Path)
    quiz_question_control.add_argument('--apply', action='store_true', help='계획을 실제 TLS에 저장')
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
    ask_parser.add_argument("--assignment-query", default=None, help="저장 요청의 대화 문맥에서 찾은 검색어 (조회만 수행)")
    study_parser = sub.add_parser("study", help="학습 세션 이벤트 처리")
    study_parser.add_argument("--conversation", required=True, help="호스트 대화별 고유 ID")
    study_input = study_parser.add_mutually_exclusive_group(required=True)
    study_input.add_argument("--event-json", help="학습 이벤트 JSON")
    study_input.add_argument("--event-file", type=Path, help="문제 생성·채점 이벤트 JSON 파일")
    notes_parser = sub.add_parser("study-notes", help="과목별 학습·시험 메모 조회")
    notes_parser.add_argument("--course", default="", help="과목명 또는 일부")
    notes_parser.add_argument("--lesson", default="", help="주차·차시·단원 일부")
    insights_parser = sub.add_parser("study-insights", help="중요도와 취약도에 따른 개념 복습 자료")
    insights_parser.add_argument("--course", default="", help="과목명 또는 일부")
    insights_parser.add_argument("--limit", type=int, default=10)
    plan_parser = sub.add_parser("study-plan", help="주차별 학습 순서와 시험 전 복습 비율")
    plan_parser.add_argument("--course", default="", help="과목명 또는 일부")
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
        result = (search_save_targets(database(), USER_ID, args.query)
                  if args.source == "tls" and args.operation == "save"
                  else selection_guidance(find_assignments(database(), USER_ID, selectors), args.operation))
    elif args.command == "assignment-add":
        try:
            item = database(write=True).add_manual_assignment(USER_ID, args.title, course_id=args.course_id, due_at=args.due_at, description=args.description)
            result = {"toolCalls": ["add_manual_assignment"], "data": item,
                      "answer": f"‘{item['title']}’ 과제를 등록했어요. 마감: {format_deadline(item.get('dueAt'))}. 진행 기록도 남기려면 ‘과제 진행 상황 저장해줘’라고 말해 주세요."}
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
    elif args.command == 'lecture-audio':
        store = database()
        try:
            selected = select_lectures(store.get_courses(USER_ID), store.get_lectures(USER_ID),
                course_query=args.course, lecture_query=args.lecture, lecture_ids=args.id, all_completed=args.all_completed)
            saved = {item['lectureId']: item for item in store.get_lecture_audio(USER_ID)}
            result = {'toolCalls': ['get_lectures', 'get_lecture_audio'],
                      'data': [{**lecture, 'audioSavedAt': saved.get(lecture['id'], {}).get('savedAt')} for lecture in selected],
                      'answer': '저장된 강의 목록이에요. 음성을 사용할 때는 학교의 현재 완료·제한 상태를 다시 확인해요.'}
        except ValueError as error:
            result = {'toolCalls': ['get_lectures'], 'needsInput': True, 'data': [], 'answer': str(error)}
    elif args.command == "notices":
        result = {"toolCalls": ["get_notices"], "data": database().get_notices(USER_ID)}
    elif args.command == "resources":
        result = {"toolCalls": ["get_resources"], "data": database().get_resources(USER_ID)}
    elif args.command == "learning-profile":
        try:
            if args.updates_json is None:
                profile = database().get_learner_profile(USER_ID)
                result = {"toolCalls": ["get_learner_profile"], "data": profile,
                          "answer": "여러 채팅에서 함께 쓰는 학습 프로필이에요. 아직 사용자가 확인해 준 학습 방식은 저장되지 않았습니다." if not profile.get("learningStyle") else "여러 채팅에서 함께 쓰는 학습 프로필을 확인했어요."}
            else:
                updates = json.loads(args.updates_json)
                profile = database(write=True).save_learner_profile(USER_ID, updates)
                result = {"toolCalls": ["save_learner_profile"], "data": profile,
                          "answer": "확인해 준 학습 방식과 자료 접근 동의를 공용 학습 프로필에 저장했어요."}
        except (ValueError, json.JSONDecodeError) as error:
            result = {"toolCalls": ["get_learner_profile"], "data": None,
                      "error": {"code": "INVALID_LEARNING_PROFILE", "message": str(error)}, "answer": str(error)}
    elif args.command == 'analysis-history':
        result = {'toolCalls': ['get_analysis_records'], 'data': analysis_records.read(
            DB_PATH.parent, USER_ID, database().get_resources(USER_ID), args.id,
            args.resource_id, args.offset, args.limit)}
    elif args.command == 'study-pack':
        if args.pack_action == 'export':
            store = database()
            courses = store.get_courses(USER_ID)
            matches = [course for course in courses if not args.course.strip() or normalize(args.course) in normalize(course["name"])]
            if args.course.strip() and len(matches) != 1:
                raise SystemExit("내보낼 과목을 하나만 지정해 주세요.")
            result = {"toolCalls": ["export_study_pack"], "data": study_pack.export_pack(
                DB_PATH.parent, USER_ID, args.output, courses=courses, resources=store.get_resources(USER_ID),
                course_ids={course["id"] for course in (matches if args.course.strip() else courses)},
                include_notes=args.include_notes,
            ), "answer": "원본 파일과 계정 정보 없이 학습 팩을 만들었어요. 다른 기기에서 가져올 수 있습니다."}
        else:
            user_id = USER_ID or args.user_id or f"local-{secrets.token_hex(8)}"
            if not USER_ID:
                DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
                DEVICE_ID_PATH.write_text(user_id + "\n", encoding="utf-8")
                os.chmod(DEVICE_ID_PATH, 0o600)
                USER_ID = user_id
            store = LocalDatabase(DB_PATH)
            try:
                data = study_pack.import_pack(DB_PATH.parent, USER_ID, args.input_path, database=store)
            finally:
                store.close()
            result = {"toolCalls": ["import_study_pack"], "data": data, "answer": "학습 팩을 이 기기의 학습 공간에 추가했어요. 원본 파일과 학교 로그인 정보는 가져오지 않았습니다."}
    elif args.command == 'resource-file':
        store = database()
        result = original_files(store.get_courses(USER_ID), store.get_resources(USER_ID),
            files_root=DB_PATH.parent / 'files', course_query=args.course,
            resource_query=args.resource, resource_id=args.id)
    elif args.command == 'exam-history':
        store = database()
        result = StudySession(DB_PATH.parent / 'study-sessions.db', USER_ID, 'history', store,
            DB_PATH.parent / 'files').call({'action': 'exam_history', 'offset': args.offset, 'limit': args.limit})
    elif args.command == 'tls-quiz-xml':
        try:
            result = {'toolCalls': ['export_moodle_xml'], 'data': export_saved_exam(
                DB_PATH.parent / 'study-sessions.db', USER_ID, args.exam_id, args.output,
                category=args.category,
            ), 'answer': '저장된 시험지를 Moodle XML로 내보냈어요. TLS 업로드 전 문항 유형 변환 내역을 확인하세요.'}
        except MoodleXmlError as error:
            result = {'toolCalls': ['export_moodle_xml'], 'data': None,
                      'error': {'code': 'MOODLE_XML_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'reference-xml':
        try:
            result = {'toolCalls': ['export_reference_xml'], 'data': reference_to_xml(
                args.input, args.output, category=args.category,
            ), 'answer': '사용자 참고자료를 문제 문항이 아닌 로컬 보관용 Moodle XML로 저장했어요. TLS에는 자동 업로드하지 않습니다.'}
        except MoodleXmlError as error:
            result = {'toolCalls': ['export_reference_xml'], 'data': None,
                      'error': {'code': 'MOODLE_REFERENCE_XML_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-backup-assemble':
        try:
            result = {'toolCalls': ['assemble_moodle_backup'], 'data': assemble_backup(
                args.template, args.questions, args.output, section_name=args.section, quiz_name=args.quiz_name,
            ), 'answer': '검증된 로컬 백업에 시험 활동을 추가했어요. TLS에 반영하기 전 복구 화면의 대상과 중복 여부를 확인하세요.'}
        except (MoodleBackupAssemblyError, ValueError, OSError) as error:
            result = {'toolCalls': ['assemble_moodle_backup'], 'data': None,
                      'error': {'code': 'MOODLE_BACKUP_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-backup-rebuild':
        try:
            subjects = args.subject or [course['name'] for course in database().get_courses(USER_ID)]
            result = {'toolCalls': ['rebuild_moodle_backup'], 'data': rebuild_backup(
                args.template, args.output, subjects,
            ), 'answer': '내 수강 과목 기준으로 기존 활동·파일·문제은행을 비운 로컬 백업을 만들었어요. TLS 복구 전 대상과 토픽 수를 확인하세요.'}
        except (MoodleBackupRebuildError, ValueError, OSError) as error:
            result = {'toolCalls': ['rebuild_moodle_backup'], 'data': None,
                      'error': {'code': 'MOODLE_BACKUP_REBUILD_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-backup-restore':
        try:
            data = restore_backup(args.backup, args.course_url, apply=args.apply, execute=args.execute)
            result = {'toolCalls': ['restore_moodle_backup'], 'data': data,
                      'answer': ('백업을 검증하고 내 소유 자율강좌를 비운 뒤 과목 토픽만 복구했어요.'
                                 if args.execute else
                                 '백업과 대상 자율강좌를 확인했어요. 실제 복구 전 계획만 만들었습니다.')}
        except (MoodleBackupRestoreError, LoginError, ValueError, OSError) as error:
            result = {'toolCalls': ['restore_moodle_backup'], 'data': None,
                      'error': {'code': 'MOODLE_BACKUP_RESTORE_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'autonomous-course-verify':
        try:
            data = verify_course_reference(database().get_courses(USER_ID), args.url)
            result = {'toolCalls': ['verify_autonomous_course'], 'data': data,
                      'answer': ('주소와 이 기기의 TLS 강좌 정보가 일치했어요. 운영자 권한 확인 전에는 업로드하지 않습니다.'
                                 if data['verified'] else data['reason'])}
        except AutonomousCoursePolicyError as error:
            result = {'toolCalls': ['verify_autonomous_course'], 'data': None,
                      'error': {'code': 'AUTONOMOUS_COURSE_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-course-update':
        try:
            updates = {}
            for item in args.settings:
                if '=' not in item:
                    raise CourseUpdateError('--set은 FIELD=VALUE 형식이어야 해요.')
                name, value = item.split('=', 1)
                updates[name] = value
            data = update_course_settings(args.course_url, updates)
            result = {'toolCalls': ['update_autonomous_course'], 'data': data,
                      'answer': 'TLS 강좌 설정을 저장하고 다시 확인했어요.'}
        except (CourseUpdateError, LoginError, ValueError, OSError) as error:
            result = {'toolCalls': ['update_autonomous_course'], 'data': None,
                      'error': {'code': 'AUTONOMOUS_COURSE_UPDATE_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-section-update':
        try:
            data = update_section_name(args.course_url, args.section, args.name)
            result = {'toolCalls': ['update_autonomous_section'], 'data': data,
                      'answer': 'TLS 토픽 이름을 저장하고 다시 확인했어요.'}
        except (SectionUpdateError, LoginError, ValueError, OSError) as error:
            result = {'toolCalls': ['update_autonomous_section'], 'data': None,
                      'error': {'code': 'AUTONOMOUS_SECTION_UPDATE_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-activity-inspect':
        try:
            result = {'toolCalls': ['inspect_tls_activity_forms'], 'data': inspect_forms(args.course_id),
                      'answer': 'TLS 활동 추가 양식을 읽기 전용으로 확인했어요. 아직 활동을 만들거나 저장하지 않았습니다.'}
        except (FormUnavailable, LoginError, ValueError, OSError) as error:
            result = {'toolCalls': ['inspect_tls_activity_forms'], 'data': None,
                      'error': {'code': 'TLS_ACTIVITY_FORM_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-activity-control':
        try:
            result = {'toolCalls': ['control_tls_activities'], 'data': control_activities(
                args.course_id, args.plan, apply=args.apply),
                'answer': ('TLS 활동 계획을 확인하고 저장했어요.' if args.apply else
                           'TLS 활동 계획을 확인했어요. 아직 저장하지 않았습니다.')}
        except (ActivityControlError, FormUnavailable, LoginError, ValueError, OSError) as error:
            result = {'toolCalls': ['control_tls_activities'], 'data': None,
                      'error': {'code': 'TLS_ACTIVITY_CONTROL_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-quiz-question-inspect':
        try:
            result = {'toolCalls': ['inspect_tls_quiz_question_forms'], 'data': inspect_question_forms(args.quiz_id),
                      'answer': '현재 TLS 퀴즈에서 실제로 제공하는 문항 양식과 전달 인자를 읽기 전용으로 확인했어요.'}
        except (QuizQuestionError, LoginError, ValueError, OSError) as error:
            result = {'toolCalls': ['inspect_tls_quiz_question_forms'], 'data': None,
                      'error': {'code': 'TLS_QUIZ_QUESTION_FORM_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == 'tls-quiz-question-control':
        try:
            data = control_questions(args.quiz_id, args.plan, apply=args.apply)
            attached = args.apply and all(item.get('attached') is True for item in data['questions'])
            result = {'toolCalls': ['control_tls_quiz_questions'], 'data': data,
                'answer': ('TLS 퀴즈에 문항을 저장하고 편집 화면 연결까지 확인했어요.' if attached else
                           'TLS 문제은행에 문항을 저장했지만 퀴즈 연결은 확인되지 않았어요. 연결 경로를 추가로 확인해야 합니다.' if args.apply else
                           'TLS 문제은행 문항 계획과 전달 인자를 확인했어요. 아직 저장하지 않았습니다.')}
        except (QuizQuestionError, LoginError, ValueError, OSError) as error:
            result = {'toolCalls': ['control_tls_quiz_questions'], 'data': None,
                      'error': {'code': 'TLS_QUIZ_QUESTION_CONTROL_INVALID', 'message': str(error)}, 'answer': str(error)}
    elif args.command == "study-materials":
        if not 1000 <= args.max_chars <= 80000:
            parser.error("--max-chars는 1000~80000 사이여야 합니다.")
        store = database()
        result = study_materials(
            store.get_courses(USER_ID), store.get_resources(USER_ID),
            files_root=DB_PATH.parent / "files", course_query=args.course,
            resource_query=args.resource, max_chars=args.max_chars,
        )
    elif args.command == "study-notes":
        selection = {"courseName": args.course} if args.course else {}
        if args.lesson:
            selection["lessonKey"] = args.lesson
        result = StudySession(DB_PATH.parent / "study-sessions.db", USER_ID, 'context-notes',
            database(), DB_PATH.parent / "files").call({'action': 'list_context_notes', 'selection': selection})
    elif args.command == "study-insights":
        store = database()
        courses = store.get_courses(USER_ID)
        matches = [course for course in courses if not args.course.strip() or normalize(args.course) in normalize(course["name"])]
        if args.course.strip() and len(matches) != 1:
            raise SystemExit("복습할 과목을 하나만 지정해 주세요.")
        course_ids = {course["id"] for course in (matches if args.course.strip() else courses)}
        data = build_insights(DB_PATH.parent, USER_ID, store.get_resources(USER_ID), course_ids=course_ids, limit=args.limit)
        result = {"toolCalls": ["get_concept_insights"], "data": data,
                  "answer": "취약한 개념부터 다시 확인할 수 있는 학습 자료를 정리했어요." if data["concepts"] else "아직 풀이 기록이나 자료 분석이 없어 개념 우선순위를 정하지 못했어요."}
    elif args.command == "study-plan":
        store = database()
        courses = store.get_courses(USER_ID)
        matches = [course for course in courses if not args.course.strip() or normalize(args.course) in normalize(course["name"])]
        if args.course.strip() and len(matches) != 1:
            raise SystemExit("학습 계획을 볼 과목을 하나만 지정해 주세요.")
        data = build_plan(DB_PATH.parent, USER_ID, matches if args.course.strip() else courses,
                          store.get_resources(USER_ID), course_ids={item["id"] for item in matches})
        result = {"toolCalls": ["get_study_plan"], "data": data,
                  "answer": "주차별 자료와 지금까지의 풀이 결과를 바탕으로 학습 순서를 정리했어요."}
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
        result = ask(args.text, conversation=args.conversation, checkpoint_json=args.checkpoint_json,
                     assignment_query=args.assignment_query)
    elif args.command == "study":
        try:
            event = json.loads(args.event_file.read_text(encoding='utf-8') if args.event_file else args.event_json)
            store = database()
            result = {"toolCalls": ["study"], **StudySession(DB_PATH.parent / "study-sessions.db", USER_ID,
                args.conversation, store, DB_PATH.parent / "files").call(event)}
            result = _open_exam_web(result, args.conversation)
        except (ValueError, OSError) as exc:
            result = {"toolCalls": ["study"], "status": "error", "error": {"code": "STUDY_INVALID", "message": str(exc)}, "answer": str(exc)}
    if args.command not in ("ask", "study"):
        result = with_next_commands(result)
    print(json.dumps(redact_output(result), ensure_ascii=False, indent=2))
    if DB is not None:
        DB.close()
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
