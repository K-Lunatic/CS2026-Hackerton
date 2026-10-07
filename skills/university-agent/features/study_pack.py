"""Portable, source-free study packs for moving learning data between devices."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PACK_FORMAT = "turtleneck-study-pack"
PACK_VERSION = 1
MAX_PACK_BYTES = 50 * 1024 * 1024
STUDY_TABLES = {
    "study_exams": ("exam_id", "conversation", "created_at", "title", "question_count", "phase", "score", "state"),
    "study_pipelines": ("exam_id", "state"),
    "study_match_history": ("match_id", "source_exam_id", "conversation", "created_at", "phase", "pair_count", "attempts", "state"),
    "study_exam_collections": ("collection_id", "conversation", "created_at", "state"),
    "study_context_notes": ("note_id", "course_id", "lesson_key", "exam_type", "note_text", "tags_json", "created_at", "updated_at"),
}


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_study_rows(root: Path, user: str, course_ids: set[str], include_notes: bool) -> dict[str, list[dict]]:
    path = root / "study-sessions.db"
    if not path.exists():
        return {name: [] for name in STUDY_TABLES}
    result = {name: [] for name in STUDY_TABLES}
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        for table, columns in STUDY_TABLES.items():
            if table == "study_context_notes" and not include_notes:
                continue
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            rows = db.execute(f"SELECT {','.join(columns)} FROM {table} WHERE user=?", (user,)).fetchall()
            for row in rows:
                item = dict(row)
                if table in {"study_exams", "study_pipelines"} and course_ids:
                    try:
                        state = json.loads(item["state"])
                    except (TypeError, ValueError):
                        continue
                    sources = state.get("sources", [])
                    if table == "study_pipelines":
                        sources = state.get("files", []) or state.get("sources", [])
                    source_courses = {source.get("courseId") for source in sources if isinstance(source, dict)}
                    if source_courses and not source_courses & course_ids:
                        continue
                if table == "study_context_notes" and course_ids and item["course_id"] not in course_ids:
                    continue
                result[table].append(item)
    return result


def _read_analyses(root: Path, user: str, resource_ids: set[str]) -> list[dict]:
    path = root / "analysis-records.db"
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute("SELECT value FROM analyses WHERE user=? ORDER BY created DESC, id DESC", (user,)).fetchall()
    records = []
    for (encoded,) in rows:
        record = json.loads(encoded)
        if record.get("resourceId") not in resource_ids:
            continue
        records.append(record)
    return records


def _write_zip(output: Path, manifest: dict, files: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tpack-", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _json_bytes(manifest))
            for name, payload in files.items():
                archive.writestr(name, payload)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def export_pack(root, user: str, output, *, courses: list[dict], resources: list[dict],
                course_ids: set[str] | None = None, include_notes: bool = False) -> dict:
    root, output = Path(root), Path(output)
    course_ids = course_ids or {course["id"] for course in courses}
    selected_courses = [course for course in courses if course["id"] in course_ids]
    selected_resources = [resource for resource in resources if resource["courseId"] in course_ids]
    resource_ids = {resource["id"] for resource in selected_resources}
    analyses = _read_analyses(root, user, resource_ids)
    study = _load_study_rows(root, user, course_ids, include_notes)
    content = {
        "courses": [{key: course.get(key) for key in ("id", "externalId", "name", "professor", "semester", "source")} for course in selected_courses],
        "resources": [{key: resource.get(key) for key in ("id", "externalId", "courseId", "title", "fileName", "extension", "mimeType", "remotePath", "downloadStatus", "downloadReason", "source")} for resource in selected_resources],
        "analyses": analyses,
        "study": study,
    }
    files = {"content.json": _json_bytes(content)}
    manifest = {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "packId": secrets.token_hex(12),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "containsOriginalFiles": False,
        "containsCredentials": False,
        "files": {name: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()} for name, payload in files.items()},
    }
    _write_zip(output, manifest, files)
    return {
        "path": str(output.resolve()),
        "courses": len(selected_courses),
        "resources": len(selected_resources),
        "analyses": len(analyses),
        "studyRecords": sum(len(rows) for rows in study.values()),
        "containsOriginalFiles": False,
        "containsCredentials": False,
    }


def _read_pack(path: Path) -> tuple[dict, dict]:
    if path.stat().st_size > MAX_PACK_BYTES:
        raise ValueError("학습 팩이 너무 커요. 과목이나 자료를 나눠 내보내 주세요.")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if names != {"manifest.json", "content.json"}:
            raise ValueError("학습 팩에 허용되지 않은 파일이 들어 있어요.")
        if archive.getinfo("content.json").file_size > MAX_PACK_BYTES:
            raise ValueError("학습 팩 내용이 너무 커요. 과목이나 자료를 나눠 내보내 주세요.")
        manifest = json.loads(archive.read("manifest.json"))
        content_bytes = archive.read("content.json")
    if (manifest.get("format") != PACK_FORMAT or manifest.get("version") != PACK_VERSION
            or not isinstance(manifest.get("packId"), str)):
        raise ValueError("지원하지 않는 터틀넥 학습 팩 버전이에요.")
    expected = manifest.get("files", {}).get("content.json", {})
    if expected.get("sha256") != hashlib.sha256(content_bytes).hexdigest():
        raise ValueError("학습 팩이 손상됐거나 전송 중 바뀌었어요.")
    content = json.loads(content_bytes)
    if not isinstance(content, dict) or not all(isinstance(content.get(key), list) for key in ("courses", "resources", "analyses")):
        raise ValueError("학습 팩 내용 형식이 올바르지 않아요.")
    return manifest, content


def _ensure_study_tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS study_exams (user TEXT, exam_id TEXT, conversation TEXT, created_at TEXT, title TEXT, question_count INTEGER, phase TEXT, score REAL, state TEXT, PRIMARY KEY(user,exam_id))")
    db.execute("CREATE TABLE IF NOT EXISTS study_pipelines (user TEXT, exam_id TEXT, state TEXT, PRIMARY KEY(user,exam_id))")
    db.execute("CREATE TABLE IF NOT EXISTS study_match_history (user TEXT, match_id TEXT, source_exam_id TEXT, conversation TEXT, created_at TEXT, phase TEXT, pair_count INTEGER, attempts INTEGER, state TEXT, PRIMARY KEY(user,match_id))")
    db.execute("CREATE TABLE IF NOT EXISTS study_exam_collections (user TEXT, collection_id TEXT, conversation TEXT, created_at TEXT, state TEXT, PRIMARY KEY(user,collection_id))")
    db.execute("CREATE TABLE IF NOT EXISTS study_context_notes (user TEXT NOT NULL, note_id TEXT NOT NULL, course_id TEXT NOT NULL, lesson_key TEXT, exam_type TEXT NOT NULL, note_text TEXT NOT NULL, tags_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(user,note_id))")


def import_pack(root, user: str, path, *, database) -> dict:
    manifest, content = _read_pack(Path(path))
    courses = content["courses"]
    resources = [{**resource, "localPath": None, "downloadStatus": "NOT_DOWNLOADED"} for resource in content["resources"]]
    database.merge_courses_resources(user, courses, resources)
    analysis_path = Path(root) / "analysis-records.db"
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    imported_analyses = 0
    with closing(sqlite3.connect(analysis_path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS analyses (user TEXT, id TEXT, resource TEXT, created TEXT, metadata TEXT, value TEXT, PRIMARY KEY(user,id))")
        db.execute("CREATE INDEX IF NOT EXISTS analyses_history ON analyses(user,created DESC,id DESC)")
        for record in content["analyses"]:
            identifier = record.get("recordId")
            if not isinstance(identifier, str) or not identifier or not isinstance(record.get("resourceId"), str):
                raise ValueError("학습 팩의 분석 기록 식별자가 올바르지 않아요.")
            metadata = {key: value for key, value in record.items() if key not in ("sources", "data")}
            encoded = json.dumps(record, ensure_ascii=False)
            existing = db.execute("SELECT value FROM analyses WHERE user=? AND id=?", (user, identifier)).fetchone()
            if existing:
                if existing[0] != encoded:
                    raise ValueError(f"분석 기록 {identifier}가 기존 기록과 달라요.")
                continue
            db.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?)", (user, identifier, record["resourceId"], record.get("analyzedAt", ""), json.dumps(metadata, ensure_ascii=False), encoded))
            imported_analyses += 1
    study = content.get("study", {})
    if not isinstance(study, dict):
        raise ValueError("학습 기록 형식이 올바르지 않아요.")
    imported_study = 0
    study_path = Path(root) / "study-sessions.db"
    with closing(sqlite3.connect(study_path)) as db, db:
        _ensure_study_tables(db)
        for table, columns in STUDY_TABLES.items():
            for row in study.get(table, []):
                if not isinstance(row, dict) or any(column not in row for column in columns):
                    raise ValueError(f"학습 기록 형식이 올바르지 않아요: {table}")
                values = [user, *[row[column] for column in columns]]
                marks = ",".join("?" for _ in values)
                before = db.total_changes
                db.execute(f"INSERT OR IGNORE INTO {table} VALUES ({marks})", values)
                imported_study += db.total_changes - before
    return {"packId": manifest["packId"], "courses": len(courses), "resources": len(resources), "analyses": imported_analyses, "studyRecords": imported_study}
