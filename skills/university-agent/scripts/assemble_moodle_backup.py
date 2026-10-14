#!/usr/bin/env python3
"""Assemble a local Moodle course backup from an existing backup template.

This only prepares a new .mbz locally. It never uploads, restores, deletes or
changes a TLS course.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import re
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.autonomous_course_policy import AutonomousCoursePolicyError, validate_visible_name
from scripts.validate_moodle_backup import validate_backup


class MoodleBackupAssemblyError(ValueError):
    pass


_XML_DECL = b'<?xml version="1.0" encoding="UTF-8"?>\n'


def _xml_bytes(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return _XML_DECL + ET.tostring(root, encoding="utf-8")


def _safe_name(value: object, field: str) -> str:
    try:
        return validate_visible_name(value, field=field)
    except AutonomousCoursePolicyError as error:
        raise MoodleBackupAssemblyError(str(error)) from error


def _numeric_ids(payloads: list[bytes]) -> int:
    values = []
    for payload in payloads:
        values.extend(int(value) for value in re.findall(rb'\b(?:id|moduleid|contextid|sectionid)="([1-9][0-9]*)"', payload))
        values.extend(int(value) for value in re.findall(rb'<(?:questionid|id|moduleid|contextid|sectionid)>([1-9][0-9]*)</', payload))
    return max(values, default=0)


def _fraction(value: str | None) -> str:
    try:
        return f"{float(value or 0) / 100:.7f}"
    except ValueError as error:
        raise MoodleBackupAssemblyError("문항 정답 비율 형식이 올바르지 않아요.") from error


def _answer(node: ET.Element, answer_id: int, *, fraction: str, text: str) -> ET.Element:
    answer = ET.SubElement(node, "answer", {"id": str(answer_id)})
    for tag, value in (("answertext", text), ("answerformat", "1"),
                       ("fraction", fraction), ("feedback", ""), ("feedbackformat", "1")):
        child = ET.SubElement(answer, tag)
        child.text = value
    return answer


def _backup_question(source: ET.Element, question_id: int, answer_id: int, now: int) -> tuple[ET.Element, int]:
    kind = source.attrib.get("type", "").strip()
    # shortcut: .mbz plugin XML is verified only for these core types; use the
    # observed TLS form or direct Moodle XML import for other installed types.
    if kind not in {"multichoice", "shortanswer", "truefalse", "essay"}:
        raise MoodleBackupAssemblyError(f"Moodle 백업에서 지원하지 않는 문항 유형이에요: {kind or '알 수 없음'}")
    name = _safe_name(source.findtext("./name/text"), "문항 이름")
    text = source.findtext("./questiontext/text") or ""
    if source.findall("./questiontext/file") or "@@PLUGINFILE@@" in text:
        raise MoodleBackupAssemblyError("이미지가 포함된 문항은 파일 업로드 연결 후에만 백업에 넣을 수 있어요.")
    question = ET.Element("question", {"id": str(question_id)})
    fields = {
        "parent": "0", "name": name, "questiontext": text,
        "questiontextformat": "1", "generalfeedback": source.findtext("./generalfeedback/text") or "",
        "generalfeedbackformat": "1", "defaultmark": source.findtext("./defaultgrade") or "1.0000000",
        "penalty": source.findtext("./penalty") or "0.0000000", "qtype": kind, "length": "1",
        "stamp": "turtleneck+" + hashlib.sha256(f"{question_id}:{name}".encode()).hexdigest()[:12],
        "version": "turtleneck+" + hashlib.sha256(f"{question_id}:{now}".encode()).hexdigest()[:12],
        "hidden": "0", "timecreated": str(now), "timemodified": str(now),
        "createdby": "0", "modifiedby": "0",
    }
    for tag, value in fields.items():
        child = ET.SubElement(question, tag)
        child.text = value

    answers = source.findall("./answer")
    plugin = ET.SubElement(question, f"plugin_qtype_{kind}_question")
    if kind == "essay":
        essay = ET.SubElement(plugin, "essay", {"id": str(answer_id)})
        for tag, value in (("responseformat", "editor"), ("responserequired", "1"),
                           ("responsefieldlines", "10"), ("attachments", "0")):
            ET.SubElement(essay, tag).text = value
        return question, answer_id + 1

    answers_node = ET.SubElement(plugin, "answers")
    answer_ids: list[int] = []
    for item in answers:
        current = answer_id
        answer_ids.append(current)
        _answer(answers_node, current, fraction=_fraction(item.attrib.get("fraction")),
                text=item.findtext("text") or "")
        answer_id += 1
    if not answers:
        raise MoodleBackupAssemblyError(f"문항에 답안이 없어 백업에 넣을 수 없어요: {name}")
    detail = ET.SubElement(plugin, kind, {"id": str(answer_id)})
    if kind == "multichoice":
        for tag, value in (("layout", "0"), ("single", source.findtext("./single") or "true"),
                           ("shuffleanswers", source.findtext("./shuffleanswers") or "true"),
                           ("correctfeedback", ""), ("correctfeedbackformat", "1"),
                           ("partiallycorrectfeedback", ""), ("partiallycorrectfeedbackformat", "1"),
                           ("incorrectfeedback", ""), ("incorrectfeedbackformat", "1"),
                           ("answernumbering", source.findtext("./answernumbering") or "abc"),
                           ("shownumcorrect", "0")):
            ET.SubElement(detail, tag).text = value
    elif kind == "shortanswer":
        ET.SubElement(detail, "usecase").text = source.findtext("./usecase") or "0"
    else:
        if len(answer_ids) != 2:
            raise MoodleBackupAssemblyError("OX 문항은 답안이 두 개여야 해요.")
        true_id, false_id = answer_ids
        if any((item.findtext("text") or "").strip() in {"참", "true", "True"} and float(item.attrib.get("fraction", "0")) == 100 for item in answers):
            true_id, false_id = answer_ids[0], answer_ids[1]
        ET.SubElement(detail, "trueanswer").text = str(true_id)
        ET.SubElement(detail, "falseanswer").text = str(false_id)
    ET.SubElement(question, "question_hints")
    ET.SubElement(question, "tags")
    return question, answer_id + 1


def _find_template(files: dict[str, bytes]) -> tuple[str, ET.Element]:
    for path, payload in files.items():
        if not path.startswith("activities/quiz_") or not path.endswith("/module.xml"):
            continue
        try:
            return path.rsplit("/", 1)[0], ET.fromstring(payload)
        except ET.ParseError:
            continue
    raise MoodleBackupAssemblyError("백업 안에서 복제할 퀴즈 활동을 찾지 못했어요.")


def _section(files: dict[str, bytes], name: str) -> tuple[str, ET.Element]:
    for path, payload in files.items():
        if not path.startswith("sections/") or not path.endswith("/section.xml"):
            continue
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            continue
        if (root.findtext("name") or "").strip() == name:
            return path.rsplit("/", 1)[0], root
    raise MoodleBackupAssemblyError(f"백업 안에서 '{name}' 토픽을 찾지 못했어요.")


def _reject_banned_existing_names(files: dict[str, bytes]) -> None:
    checks: list[tuple[str, str]] = []
    for path, payload in files.items():
        if path == "questions.xml":
            root = ET.fromstring(payload)
            checks.extend((path, node.findtext("name")) for node in root.findall("./question_category/questions/question") if node.findtext("name"))
        elif path == "moodle_backup.xml":
            root = ET.fromstring(payload)
            checks.extend((path, node.findtext("title")) for node in root.findall("./information/contents/activities/activity") if node.findtext("title"))
        elif path.startswith("sections/") and path.endswith("/section.xml"):
            root = ET.fromstring(payload)
            if root.findtext("name"):
                checks.append((path, root.findtext("name")))
    for path, value in checks:
        try:
            _safe_name(value, "기존 백업 표시 이름")
        except MoodleBackupAssemblyError as error:
            raise MoodleBackupAssemblyError(f"기존 백업의 금지된 표시 이름을 먼저 정리해야 해요 ({path}).") from error


def assemble_backup(template: Path, questions_xml: Path, output: Path, *, section_name: str, quiz_name: str) -> dict[str, object]:
    template = template.expanduser().resolve()
    questions_xml = questions_xml.expanduser().resolve()
    output = output.expanduser().resolve()
    if template == output:
        raise MoodleBackupAssemblyError("원본 백업과 새 백업 저장 위치는 달라야 해요.")
    if not questions_xml.is_file():
        raise MoodleBackupAssemblyError("문항 XML 파일을 찾지 못했어요.")
    _safe_name(section_name, "토픽 이름")
    quiz_name = _safe_name(quiz_name, "시험 활동 이름")
    validate_backup(template)
    try:
        question_root = ET.parse(questions_xml).getroot()
    except (OSError, ET.ParseError) as error:
        raise MoodleBackupAssemblyError("문항 XML을 읽지 못했어요.") from error
    sources = [node for node in question_root.findall("question") if node.attrib.get("type") != "category"]
    if not sources:
        raise MoodleBackupAssemblyError("문항 XML에 실제 문항이 없어요.")

    with tarfile.open(template, "r:*") as archive:
        members = archive.getmembers()
        files = {member.name: archive.extractfile(member).read() for member in members if member.isfile()}
    try:
        _reject_banned_existing_names(files)
    except ET.ParseError as error:
        raise MoodleBackupAssemblyError("기존 백업의 표시 이름 XML을 읽지 못했어요.") from error
    template_dir, _ = _find_template(files)
    section_dir, section_root = _section(files, section_name)
    backup_root = ET.fromstring(files["moodle_backup.xml"])
    question_bank = ET.fromstring(files["questions.xml"])
    course_root = ET.fromstring(files["course/course.xml"])
    course_id = course_root.attrib.get("id", "")
    categories = [node for node in question_bank.findall("question_category")
                  if node.findtext("contextlevel") == "50" and node.findtext("contextinstanceid") == course_id]
    if not categories:
        raise MoodleBackupAssemblyError("과목 문제은행 범주를 찾지 못했어요.")
    if any((node.findtext("title") or "").strip() == quiz_name for node in backup_root.findall("./information/contents/activities/activity")):
        raise MoodleBackupAssemblyError("같은 이름의 시험 활동이 이미 백업에 있어요.")

    new_base = _numeric_ids(list(files.values())) + 1
    new_module_id, new_section_id, new_context_id = new_base, new_base + 1, new_base + 2
    new_activity_dir = f"activities/quiz_{new_module_id}"
    now = int(time.time())
    answer_id = new_base + 100
    question_ids: list[int] = []
    for offset, source in enumerate(sources):
        question, answer_id = _backup_question(source, new_base + 3 + offset, answer_id, now)
        question_ids.append(int(question.attrib["id"]))
        categories[0].find("questions").append(question)

    module_root = ET.fromstring(files[f"{template_dir}/module.xml"])
    module_root.attrib["id"] = str(new_module_id)
    if module_root.find("sectionid") is not None:
        module_root.find("sectionid").text = section_root.attrib.get("id", "").removeprefix("section_") or section_root.attrib.get("id", "")
    quiz_root = ET.fromstring(files[f"{template_dir}/quiz.xml"])
    quiz_root.attrib.update({"id": str(new_module_id), "moduleid": str(new_module_id), "contextid": str(new_context_id)})
    quiz = quiz_root.find("quiz")
    if quiz is None:
        raise MoodleBackupAssemblyError("복제할 퀴즈 XML 구조가 올바르지 않아요.")
    quiz.attrib["id"] = str(new_module_id)
    quiz.find("name").text = quiz_name
    instances = quiz.find("question_instances")
    if instances is None:
        instances = ET.SubElement(quiz, "question_instances")
    else:
        quiz.remove(instances)
        instances = ET.SubElement(quiz, "question_instances")
    total = 0.0
    for slot, question_id in enumerate(question_ids, 1):
        source = sources[slot - 1]
        mark = float(source.findtext("defaultgrade") or "1")
        total += mark
        instance = ET.SubElement(instances, "question_instance", {"id": str(new_base + 1000 + slot)})
        for tag, value in (("slot", str(slot)), ("page", str(slot)), ("requireprevious", "0"),
                           ("questionid", str(question_id)), ("maxmark", f"{mark:.7f}")):
            ET.SubElement(instance, tag).text = value
    for tag in ("sumgrades", "grade"):
        if quiz.find(tag) is not None:
            quiz.find(tag).text = f"{total:.5f}"

    section_root.find("sequence").text = ((section_root.findtext("sequence") or "").strip() + "," + str(new_module_id)).lstrip(",")
    activities = backup_root.find("./information/contents/activities")
    if activities is None:
        raise MoodleBackupAssemblyError("백업 활동 목록을 찾지 못했어요.")
    activity = ET.SubElement(activities, "activity")
    for tag, value in (("moduleid", str(new_module_id)), ("sectionid", section_root.attrib.get("id", "").removeprefix("section_")),
                       ("modulename", "quiz"), ("title", quiz_name), ("directory", new_activity_dir)):
        ET.SubElement(activity, tag).text = value

    files[f"questions.xml"] = _xml_bytes(question_bank)
    files[f"{section_dir}/section.xml"] = _xml_bytes(section_root)
    files[f"{new_activity_dir}/module.xml"] = _xml_bytes(module_root)
    files[f"{new_activity_dir}/quiz.xml"] = _xml_bytes(quiz_root)
    for path, payload in list(files.items()):
        if path.startswith(f"{template_dir}/") and path not in {f"{template_dir}/module.xml", f"{template_dir}/quiz.xml"}:
            files[f"{new_activity_dir}/{path[len(template_dir) + 1:]}"] = payload
    files["moodle_backup.xml"] = _xml_bytes(backup_root)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="turtleneck-mbz-", suffix=".mbz", dir=output.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            for member in members:
                if member.name == ".ARCHIVE_INDEX" or member.name in files and member.isfile():
                    if member.name == ".ARCHIVE_INDEX":
                        continue
                    info = copy.copy(member)
                    info.size = len(files[member.name])
                    archive.addfile(info, io.BytesIO(files[member.name]))
                elif member.isdir():
                    archive.addfile(copy.copy(member))
            for path, payload in files.items():
                if not any(member.name == path for member in members):
                    info = tarfile.TarInfo(path)
                    info.size = len(payload)
                    info.mtime = now
                    archive.addfile(info, io.BytesIO(payload))
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    report = validate_backup(output)
    return {**report, "quizName": quiz_name, "sectionName": section_name,
            "questionCount": len(question_ids), "localOnly": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble a local Moodle backup from a validated template")
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path, help="Moodle XML exported by tls-quiz-xml")
    parser.add_argument("--section", required=True, help="Existing topic name in the backup")
    parser.add_argument("--quiz-name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(assemble_backup(args.template, args.questions, args.output,
                                         section_name=args.section, quiz_name=args.quiz_name), ensure_ascii=False))
    except (MoodleBackupAssemblyError, ValueError, OSError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
