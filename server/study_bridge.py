"""Small bridge between the local Actions gateway and device-local study packs."""
from __future__ import annotations

import json
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from features.study import StudySession, settings, validate_generated
from features import study_pack
from storage.local_db import LocalDatabase


def root_for(data_dir, user: str) -> Path:
    root = Path(data_dir) / (hashlib.sha256(user.encode()).hexdigest() + "-study")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


def import_pack(data_dir, db_path, user: str, uploaded: Path) -> dict:
    root = root_for(data_dir, user)
    db_file = db_path(user) if callable(db_path) else db_path
    db = LocalDatabase(db_file)
    try:
        return study_pack.import_pack(root, user, uploaded, database=db)
    finally:
        db.close()


def session(data_dir, db_path, user: str, conversation: str) -> StudySession:
    root = root_for(data_dir, user)
    provider = LocalDatabase(db_path(user), read_only=True)
    result = StudySession(root / "study-sessions.db", user, conversation, provider, root / "files")
    result._mobile_provider = provider
    return result


def close_session(value: StudySession) -> None:
    provider = getattr(value, "_mobile_provider", None)
    if provider:
        provider.close()


def history(data_dir, db_path, user: str, conversation: str) -> dict:
    current = session(data_dir, db_path, user, conversation)
    try:
        return current.call({"action": "exam_history", "limit": 20})
    finally:
        close_session(current)


def analysis_catalog(data_dir, db_path, user: str) -> list[dict]:
    root = root_for(data_dir, user)
    db = LocalDatabase(db_path(user), read_only=True)
    try:
        resources = db.get_resources(user)
    finally:
        db.close()
    records = study_pack._read_analyses(root, user, {item["id"] for item in resources})
    clean = []
    for record in records:
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        clean.append({**record, "data": {key: value for key, value in data.items() if key != "candidates"}})
    return clean


def save_generated_exam(data_dir, db_path, user: str, conversation: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("새 문제 형식이 올바르지 않아요.")
    records = analysis_catalog(data_dir, db_path, user)
    source_pool = [source for record in records for source in record.get("sources", [])]
    if not source_pool:
        raise ValueError("이 기기에는 새 문제를 만들 분석 자료가 없어요.")
    cfg = settings({**(payload.get("settings") or {}), "delivery": "web"})
    state = {"settings": cfg, "sources": source_pool}
    questions = validate_generated(payload.get("data"), state)
    if not questions:
        raise ValueError("새 문제를 만들지 못했어요.")
    exam_id = secrets.token_hex(12)
    saved = {
        "phase": "question", "selection": {}, "settings": cfg, "sources": source_pool,
        "questions": questions, "index": 0, "history": [], "hinted": False,
        "hintedIds": [], "examId": exam_id, "drafts": {}, "draftRevision": 0,
    }
    root = root_for(data_dir, user)
    path = root / "study-sessions.db"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS study_sessions (user TEXT, conversation TEXT, state TEXT, PRIMARY KEY(user,conversation))")
        db.execute("CREATE TABLE IF NOT EXISTS study_exams (user TEXT, exam_id TEXT, conversation TEXT, created_at TEXT, title TEXT, question_count INTEGER, phase TEXT, score REAL, state TEXT, PRIMARY KEY(user,exam_id))")
        title = payload.get("title") or "모바일 학습 팩 연습 시험"
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("시험 제목을 확인해 주세요.")
        encoded = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT OR REPLACE INTO study_sessions VALUES (?,?,?)", (user, conversation, encoded))
        db.execute("INSERT INTO study_exams VALUES (?,?,?,?,?,?,?,?,?)", (user, exam_id, conversation, now, title.strip(), len(questions), "question", None, encoded))
        db.commit()
    return {"examId": exam_id, "title": title.strip(), "questionCount": len(questions), "status": "question"}


def grade(data_dir, db_path, user: str, conversation: str, payload: dict) -> dict:
    current = session(data_dir, db_path, user, conversation)
    try:
        return current.call({**payload, "action": "grade_batch"})
    finally:
        close_session(current)
