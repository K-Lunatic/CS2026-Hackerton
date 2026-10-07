"""Grounded concept priorities from saved analysis and study history."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def _load(value, fallback):
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return result if isinstance(result, type(fallback)) else fallback


def _add(bucket, concept, *, importance=0, weak=0, attempts=0, explanation="", evidence=()):
    concept = str(concept or "").strip()
    if not concept:
        return
    item = bucket[concept]
    item["importanceRaw"] += importance
    item["weakRaw"] += weak
    item["attempts"] += attempts
    if explanation and len(explanation) > len(item["explanation"]):
        item["explanation"] = explanation[:2000]
    for ref in evidence:
        if not isinstance(ref, dict) or not ref.get("location"):
            continue
        item["evidence"][(ref.get("resourceId"), ref["location"])] = {
            "location": str(ref["location"])[:200],
            "quote": str(ref.get("quote") or "")[:500],
        }


def _record_sources(record, allowed, course_filter):
    raw_sources = record.get("sources", [])
    sources = [source for source in raw_sources if isinstance(source, dict)] if isinstance(raw_sources, list) else []
    return [source for source in sources if not course_filter or source.get("resourceId") in allowed]


def build_insights(root, user, resources, *, course_ids=None, limit=20):
    """Return compact, source-backed learning cards; never invent explanations."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("개념 우선순위는 1~50개까지 볼 수 있어요.")
    root, course_filter = Path(root), course_ids is not None
    course_ids = set(course_ids or ())
    allowed = {item["id"] for item in resources if not course_ids or item.get("courseId") in course_ids}
    bucket = defaultdict(lambda: {"importanceRaw": 0, "weakRaw": 0, "attempts": 0, "explanation": "", "evidence": {}})

    analysis_path = root / "analysis-records.db"
    if analysis_path.exists():
        with closing(sqlite3.connect(analysis_path)) as db:
            rows = db.execute("SELECT value FROM analyses WHERE user=? ORDER BY created DESC, id DESC", (user,)).fetchall()
        for (encoded,) in rows:
            record = _load(encoded, {})
            sources = _record_sources(record, allowed, course_filter)
            if not sources:
                continue
            source_ids = {source.get("resourceId") for source in sources}
            data = record.get("data") if isinstance(record.get("data"), dict) else {}
            concepts = data.get("concepts", []) if isinstance(data.get("concepts", []), list) else []
            units = data.get("units", []) if isinstance(data.get("units", []), list) else []
            candidates = data.get("candidates", []) if isinstance(data.get("candidates", []), list) else []
            for concept in concepts:
                if not isinstance(concept, dict):
                    continue
                _add(bucket, concept.get("concept"), importance=3, explanation=concept.get("explanation", ""), evidence=concept.get("evidence", []))
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                for concept in unit.get("learning", []):
                    if not isinstance(concept, dict):
                        continue
                    _add(bucket, concept.get("concept"), importance=3, explanation=concept.get("explanation", ""), evidence=concept.get("evidence", unit.get("sources", [])))
                questions = unit.get("questions", []) if isinstance(unit.get("questions", []), list) else []
                for question in questions + candidates:
                    if isinstance(question, dict) and any(ref.get("resourceId") in source_ids for ref in question.get("evidence", []) if isinstance(ref, dict)):
                        _add(bucket, question.get("concept"), importance=2, evidence=question.get("evidence", []))

    study_path = root / "study-sessions.db"
    if study_path.exists():
        with closing(sqlite3.connect(study_path)) as db:
            for table in ("study_exams", "study_match_history"):
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                rows = db.execute(f"SELECT state FROM {table} WHERE user=?", (user,)).fetchall()
                for (encoded,) in rows:
                    state = _load(encoded, {})
                    if table == "study_exams":
                        history_values = state.get("history", []) if isinstance(state.get("history", []), list) else []
                        questions = state.get("questions", []) if isinstance(state.get("questions", []), list) else []
                        history = {item.get("questionId"): item for item in history_values if isinstance(item, dict)}
                        for question in questions:
                            if not isinstance(question, dict):
                                continue
                            refs = question.get("evidence", [])
                            if course_filter and not any(ref.get("resourceId") in allowed for ref in refs if isinstance(ref, dict)):
                                continue
                            result = history.get(question.get("id"), {})
                            weak = {"incorrect": 2, "partial": 1.5, "skipped": 1}.get(result.get("outcome"), 0)
                            weak += 0.5 if result.get("hintUsed") else 0
                            _add(bucket, question.get("concept"), importance=1, weak=weak, attempts=1, evidence=refs)
                    else:
                        match = state.get("match", {})
                        wrong = set(match.get("wrongPairIds", [])) if isinstance(match, dict) else set()
                        pairs = match.get("pairs", []) if isinstance(match, dict) and isinstance(match.get("pairs", []), list) else []
                        for pair in pairs:
                            if not isinstance(pair, dict):
                                continue
                            if pair.get("id") in wrong:
                                _add(bucket, pair.get("concept"), weak=1, attempts=1, explanation=pair.get("explanation", ""), evidence=pair.get("evidence", []))

    if not bucket:
        return {"concepts": [], "total": 0}
    max_importance = max(item["importanceRaw"] for item in bucket.values()) or 1
    ranked = []
    for concept, item in bucket.items():
        importance = round(item["importanceRaw"] / max_importance * 100)
        weakness = round(min(100, item["weakRaw"] / max(item["attempts"], 1) * 40))
        priority = round(importance * 0.45 + weakness * 0.55)
        ranked.append({"concept": concept, "priority": priority, "importance": importance, "weakness": weakness,
                       "status": "우선 복습" if weakness >= 40 else "핵심 개념" if importance >= 60 else "확인 권장",
                       "learningMaterial": item["explanation"] or "저장된 설명이 없어 원문 근거를 먼저 확인해 주세요.",
                       "evidence": list(item["evidence"].values())[:5]})
    ranked.sort(key=lambda value: (-value["priority"], -value["importance"], value["concept"]))
    return {"concepts": ranked[:limit], "total": len(ranked)}
