"""Persistent, source-grounded checkpoints for pausing and resuming assignments."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from features.deadlines import format_deadline


def _database_path(db_path: str | Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser()
    configured = os.environ.get("UNIVERSITY_AGENT_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".university-agent" / "university.db"


def _connect(db_path: str | Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    path = _database_path(db_path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True) if read_only else sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    if not read_only:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS context_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                assignment_id TEXT NOT NULL,
                course_id TEXT,
                course_name TEXT,
                assignment_title TEXT NOT NULL,
                progress TEXT NOT NULL,
                completed_items_json TEXT NOT NULL,
                blocker TEXT NOT NULL,
                next_action TEXT NOT NULL,
                saved_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_bookmarks_user_assignment "
            "ON context_bookmarks (user_id, assignment_id, id DESC)"
        )
    return connection


def _record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "assignmentId": row["assignment_id"],
        "courseId": row["course_id"],
        "courseName": row["course_name"],
        "assignmentTitle": row["assignment_title"],
        "progress": row["progress"],
        "completedItems": json.loads(row["completed_items_json"]),
        "blocker": row["blocker"],
        "nextAction": row["next_action"],
        "savedAt": row["saved_at"],
        "source": "sqlite:context_bookmarks",
    }


def save_context_bookmark(
    assignment: dict[str, Any],
    *,
    user_id: str,
    progress: str,
    blocker: str,
    next_action: str,
    completed_items: list[str] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a checkpoint summary supplied by the host ChatGPT or Codex agent."""
    assignment_id = str(assignment.get("id", "")).strip()
    assignment_title = str(assignment.get("title", "")).strip()
    progress = progress.strip()
    blocker = blocker.strip()
    next_action = next_action.strip()
    if not assignment_id or not assignment_title:
        raise ValueError("과제 ID와 과제명이 필요합니다.")
    if not progress or not blocker or not next_action:
        raise ValueError("현재 진행 상황, 막힌 부분(없으면 '없음'), 다음 행동을 입력해야 합니다.")

    items = [item.strip() for item in (completed_items or []) if item.strip()]
    saved_at = datetime.now(timezone.utc).isoformat()
    connection = _connect(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO context_bookmarks (
                user_id, assignment_id, course_id, course_name, assignment_title,
                progress, completed_items_json, blocker, next_action, saved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                assignment_id,
                assignment.get("courseId"),
                assignment.get("courseName"),
                assignment_title,
                progress,
                json.dumps(items, ensure_ascii=False),
                blocker,
                next_action,
                saved_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM context_bookmarks WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        connection.commit()
        return _record(row)
    finally:
        connection.close()


def get_context_bookmark(
    *,
    user_id: str,
    assignment_id: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return the latest saved checkpoint, optionally for one assignment."""
    if not _database_path(db_path).exists():
        return None
    connection = _connect(db_path, read_only=True)
    try:
        if assignment_id:
            row = connection.execute(
                """
                SELECT * FROM context_bookmarks
                WHERE user_id = ? AND assignment_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, assignment_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM context_bookmarks WHERE user_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return _record(row) if row else None
    finally:
        connection.close()


def list_context_bookmarks(
    *, user_id: str, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """List the latest checkpoint for each assignment, newest first."""
    if not _database_path(db_path).exists():
        return []
    connection = _connect(db_path, read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT checkpoint.*
            FROM context_bookmarks AS checkpoint
            JOIN (
                SELECT assignment_id, MAX(id) AS latest_id
                FROM context_bookmarks
                WHERE user_id = ?
                GROUP BY assignment_id
            ) AS latest ON checkpoint.id = latest.latest_id
            ORDER BY checkpoint.id DESC
            """,
            (user_id,),
        ).fetchall()
        return [_record(row) for row in rows]
    finally:
        connection.close()


def list_unfinished_context_bookmarks(
    provider, *, user_id: str, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Show one saved checkpoint per assignment whose completion is unconfirmed."""
    active = {item["id"] for item in provider.get_assignments(user_id)
              if item["submissionStatus"] not in {"SUBMITTED", "LATE"}}
    return [item for item in list_context_bookmarks(user_id=user_id, db_path=db_path)
            if item["assignmentId"] in active]


def format_resume_card(record: dict[str, Any]) -> str:
    """Render a card strictly from a persisted checkpoint."""
    course = record.get("courseName") or "과목 정보 없음"
    completed = record.get("completedItems") or []
    completed_text = ", ".join(completed) if completed else "기록된 완료 항목 없음"
    blocker = record.get("blocker") or "기록 없음"
    return "\n".join(
        [
            f"과제 진행 기록 — {course} / {record['assignmentTitle']}",
            f"저장 시각: {format_deadline(record['savedAt'])} (한국 시간)",
            f"현재 상태: {record['progress']}",
            f"완료한 항목: {completed_text}",
            f"막힌 부분: {blocker}",
            f"다음 행동: {record['nextAction']}",
        ]
    )
