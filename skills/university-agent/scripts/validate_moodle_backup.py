#!/usr/bin/env python3
"""Validate a Moodle 2 course backup without extracting or executing it."""
from __future__ import annotations

import argparse
import json
import re
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path


_REQUIRED = {"moodle_backup.xml", "course/course.xml", "files.xml"}
_SAFE_MEMBER = re.compile(r"^(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$")


def validate_backup(path: Path) -> dict[str, object]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError("Moodle 백업 파일을 찾지 못했어요.")
    try:
        with tarfile.open(path, mode="r:*") as archive:
            names = [member.name for member in archive.getmembers()]
            if any(not _SAFE_MEMBER.fullmatch(name) for name in names):
                raise ValueError("백업 안에 안전하지 않은 경로가 있어 읽지 않았어요.")
            missing = sorted(_REQUIRED.difference(names))
            if missing:
                raise ValueError(f"Moodle 백업 필수 항목이 없습니다: {', '.join(missing)}")
            backup_root = ET.parse(archive.extractfile("moodle_backup.xml")).getroot()
            course_root = ET.parse(archive.extractfile("course/course.xml")).getroot()
            if backup_root.tag != "moodle_backup" or course_root.tag != "course":
                raise ValueError("Moodle 백업 XML 루트 형식이 올바르지 않아요.")
    except (OSError, tarfile.TarError, ET.ParseError, TypeError) as error:
        raise ValueError("Moodle 백업 파일을 읽거나 검증하지 못했어요.") from error
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "memberCount": len(names),
        "sectionCount": sum(name.startswith("sections/") and name.endswith("/section.xml") for name in names),
        "activityCount": sum(name.startswith("activities/") and name.endswith("/module.xml") for name in names),
        "hasFiles": any(name.startswith("files/") for name in names),
        "courseNamePresent": bool(course_root.findtext("fullname")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Moodle course backup")
    parser.add_argument("--input", required=True, type=Path, help="Local .mbz/.tar.gz backup")
    try:
        print(json.dumps(validate_backup(parser.parse_args().input), ensure_ascii=False))
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
