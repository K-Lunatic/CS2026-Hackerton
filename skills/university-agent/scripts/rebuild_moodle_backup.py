#!/usr/bin/env python3
"""Build a clean, local Moodle backup skeleton from enrolled subjects."""
from __future__ import annotations

import argparse
import copy
import io
import json
import re
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.autonomous_course_policy import AutonomousCoursePolicyError, validate_visible_name
from scripts.validate_moodle_backup import validate_backup


class MoodleBackupRebuildError(ValueError):
    pass


_XML_DECL = b'<?xml version="1.0" encoding="UTF-8"?>\n'
_DROP_ROOT_FILES = {"completion.xml", "gradebook.xml", "grade_history.xml", "groups.xml", "outcomes.xml", "scales.xml"}


def _xml_bytes(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return _XML_DECL + ET.tostring(root, encoding="utf-8")


def _name(value: object, field: str) -> str:
    try:
        return validate_visible_name(value, field=field)
    except AutonomousCoursePolicyError as error:
        raise MoodleBackupRebuildError(str(error)) from error


def _max_id(payloads: list[bytes]) -> int:
    values = []
    for payload in payloads:
        values.extend(int(value) for value in re.findall(rb'\b(?:id|moduleid|contextid|sectionid)="([1-9][0-9]*)"', payload))
        values.extend(int(value) for value in re.findall(rb'<(?:id|moduleid|contextid|sectionid)>([1-9][0-9]*)</', payload))
    return max(values, default=0)


def _read_archive(path: Path) -> tuple[list[tarfile.TarInfo], dict[str, bytes]]:
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        files = {member.name: archive.extractfile(member).read() for member in members if member.isfile()}
    return members, files


def _section_template(files: dict[str, bytes]) -> tuple[ET.Element, bytes | None]:
    for path, payload in files.items():
        if path.startswith("sections/") and path.endswith("/section.xml"):
            try:
                root = ET.fromstring(payload)
            except ET.ParseError:
                continue
            prefix = path.rsplit("/", 1)[0]
            return root, files.get(f"{prefix}/inforef.xml")
    raise MoodleBackupRebuildError("기존 백업에서 토픽 양식을 찾지 못했어요.")


def _empty_question_bank(course_id: str, category_id: int) -> bytes:
    root = ET.Element("question_categories")
    category = ET.SubElement(root, "question_category", {"id": str(category_id)})
    for tag, value in (("name", "터틀넥 문제은행"), ("contextid", course_id), ("contextlevel", "50"),
                       ("contextinstanceid", course_id), ("info", "터틀넥이 현재 학습 범위로 관리하는 문제은행입니다."),
                       ("infoformat", "0"), ("stamp", "turtleneck-local"), ("parent", "0"), ("sortorder", "999")):
        ET.SubElement(category, tag).text = value
    ET.SubElement(category, "questions")
    return _xml_bytes(root)


def rebuild_backup(template: Path, output: Path, subjects: list[str], *, include_overview: bool = True) -> dict[str, object]:
    template = template.expanduser().resolve()
    output = output.expanduser().resolve()
    if template == output:
        raise MoodleBackupRebuildError("원본 백업과 새 백업 저장 위치는 달라야 해요.")
    if not subjects:
        raise MoodleBackupRebuildError("재구성할 수강 과목이 없어요.")
    clean_subjects = []
    for subject in subjects:
        value = _name(subject, "과목 토픽 이름")
        if value not in clean_subjects:
            clean_subjects.append(value)
    validate_backup(template)
    members, files = _read_archive(template)
    try:
        backup_root = ET.fromstring(files["moodle_backup.xml"])
        course_root = ET.fromstring(files["course/course.xml"])
    except (KeyError, ET.ParseError) as error:
        raise MoodleBackupRebuildError("Moodle 백업 기본 XML을 읽지 못했어요.") from error
    section_template, inforef = _section_template(files)
    course_id = course_root.attrib.get("id", "")
    if not re.fullmatch(r"[1-9][0-9]*", course_id):
        raise MoodleBackupRebuildError("백업에서 과목 ID를 확인하지 못했어요.")

    new_base = _max_id(list(files.values())) + 1
    topic_names = (["강의 개요"] if include_overview else []) + clean_subjects
    old_paths = list(files)
    for path in old_paths:
        if path.startswith(("activities/", "sections/", "files/")) or path in _DROP_ROOT_FILES:
            del files[path]

    contents = backup_root.find("./information/contents")
    if contents is None:
        raise MoodleBackupRebuildError("백업의 콘텐츠 목록을 찾지 못했어요.")
    activities = contents.find("activities")
    if activities is None:
        activities = ET.SubElement(contents, "activities")
    else:
        activities.clear()
    sections = contents.find("sections")
    if sections is None:
        sections = ET.SubElement(contents, "sections")
    else:
        sections.clear()

    settings = backup_root.find("./information/settings")
    if settings is not None:
        for setting in list(settings.findall("setting")):
            if setting.findtext("level") in {"activity", "section"}:
                settings.remove(setting)
                continue
            if setting.findtext("level") == "root" and setting.findtext("name") == "activities":
                value = setting.find("value")
                if value is not None:
                    value.text = "0"

    section_dirs: list[tuple[str, bytes, bytes | None]] = []
    for number, topic_name in enumerate(topic_names):
        section_id = new_base + number
        section = copy.deepcopy(section_template)
        section.attrib["id"] = str(section_id)
        for tag, value in (("number", str(number)), ("name", topic_name), ("summary", ""),
                           ("summaryformat", "1"), ("sequence", ""), ("visible", "1"),
                           ("availabilityjson", '{"op":"&amp;","c":[],"showc":[]}')):
            node = section.find(tag)
            if node is None:
                node = ET.SubElement(section, tag)
            node.text = value
        directory = f"sections/section_{section_id}"
        files[f"{directory}/section.xml"] = _xml_bytes(section)
        if inforef is not None:
            files[f"{directory}/inforef.xml"] = inforef
        entry = ET.SubElement(sections, "section")
        for tag, value in (("sectionid", str(section_id)), ("title", topic_name), ("directory", directory)):
            ET.SubElement(entry, tag).text = value

    course_numsections = course_root.find("numsections")
    if course_numsections is not None:
        course_numsections.text = str(len(clean_subjects))
    files["course/course.xml"] = _xml_bytes(course_root)
    files["moodle_backup.xml"] = _xml_bytes(backup_root)
    files["questions.xml"] = _empty_question_bank(course_id, new_base + len(topic_names) + 1)
    files["files.xml"] = _xml_bytes(ET.Element("files"))

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="turtleneck-clean-", suffix=".mbz", dir=output.parent, delete=False) as temp:
        temporary = Path(temp.name)
    now = int(time.time())
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            written = set()
            for member in members:
                if member.name == ".ARCHIVE_INDEX" or member.name.startswith(("activities/", "sections/", "files/")) or member.name in _DROP_ROOT_FILES:
                    continue
                if member.isfile() and member.name in files:
                    info = copy.copy(member)
                    info.size = len(files[member.name])
                    archive.addfile(info, io.BytesIO(files[member.name]))
                    written.add(member.name)
                elif member.isdir() and not member.name.startswith(("activities/", "sections/", "files/")):
                    archive.addfile(copy.copy(member))
            for path, payload in files.items():
                if path in written:
                    continue
                info = tarfile.TarInfo(path)
                info.size = len(payload)
                info.mtime = now
                archive.addfile(info, io.BytesIO(payload))
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    report = validate_backup(output)
    return {**report, "subjects": clean_subjects, "topicCount": len(topic_names),
            "questionCount": 0, "activityCount": 0, "localOnly": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a clean local Moodle backup skeleton")
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--no-overview", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(rebuild_backup(args.template, args.output, args.subject,
                                        include_overview=not args.no_overview), ensure_ascii=False))
    except (MoodleBackupRebuildError, ValueError, OSError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
