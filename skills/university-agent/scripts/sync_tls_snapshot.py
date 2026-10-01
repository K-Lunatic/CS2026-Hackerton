#!/usr/bin/env python3
"""Write normalized TLS data into the user's private sync space."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def data_root() -> Path:
    return Path(os.environ.get("UNIVERSITY_AGENT_DATA_DIR", Path.home() / ".university-agent" / "data"))


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
    parser = argparse.ArgumentParser(description="Sync a normalized TLS snapshot")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--user-id", default=os.environ.get("UNIVERSITY_AGENT_USER_ID", "user-hong"))
    args = parser.parse_args()
    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    validate(snapshot)
    target_dir = data_root() / "users" / args.user_id
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="tls_snapshot.", suffix=".json", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(snapshot, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, target_dir / "tls_snapshot.json")
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    print(target_dir / "tls_snapshot.json")


if __name__ == "__main__":
    main()
