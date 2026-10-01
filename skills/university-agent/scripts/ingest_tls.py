#!/usr/bin/env python3
"""Import normalized TLS data into the current device's private SQLite DB."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.local_db import LocalDatabase


def validate(snapshot: dict[str, Any]) -> None:
    for key in ("courses", "assignments", "lectures"):
        if not isinstance(snapshot.get(key), list):
            raise ValueError(f"{key} must be an array")
    for item in snapshot["assignments"]:
        if not {"id", "courseId", "title", "dueAt", "submissionStatus"} <= item.keys():
            raise ValueError("assignment is missing a required field")
    for item in snapshot["lectures"]:
        if not {"id", "courseId", "title", "durationSeconds", "watchedSeconds", "watchProgress", "completed"} <= item.keys():
            raise ValueError("lecture is missing a required field")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import normalized TLS data locally")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--user-id", default=os.environ.get("UNIVERSITY_AGENT_USER_ID"))
    parser.add_argument("--name")
    parser.add_argument("--department")
    args = parser.parse_args()
    if not args.user_id:
        parser.error("--user-id 또는 UNIVERSITY_AGENT_USER_ID가 필요합니다")
    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    validate(snapshot)
    db_path = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db"))
    database = LocalDatabase(db_path)
    database.upsert_tls_snapshot(args.user_id, snapshot["courses"], snapshot["assignments"], snapshot["lectures"], args.name or args.user_id, args.department)
    database.close()
    print(db_path)


if __name__ == "__main__":
    main()
