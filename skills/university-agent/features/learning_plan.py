"""Deterministic weekly study plans from local resources and saved exam history."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import json
import re
import sqlite3
from pathlib import Path


_WEEK = re.compile(r"(?:제\s*)?(\d{1,2})\s*주차|\bweek\s*[-_ ]?(\d{1,2})\b|\bw(\d{1,2})\b", re.IGNORECASE)
REVIEW_MIX = {"weak": 50, "core": 30, "random": 20}


def week_label(*values: object) -> str:
    text = " ".join(str(value or "") for value in values)
    match = _WEEK.search(text)
    if not match:
        return "주차 미지정"
    return f"{int(next(value for value in match.groups() if value))}주차"


def review_focus(score: float | None, weakness: float) -> str:
    if score is None:
        return "아직 성적 기록이 없어 핵심 개념부터 확인"
    if score < 60 or weakness >= 40:
        return "취약 개념 보완을 먼저 진행"
    if score < 80 or weakness >= 20:
        return "취약 개념과 핵심 개념을 함께 복습"
    return "취약 개념을 유지하면서 새로운 문제 비중을 늘림"


def _safe_json(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _question_resources(question: dict) -> set[str]:
    return {str(item.get("resourceId")) for item in question.get("evidence", [])
            if isinstance(item, dict) and item.get("resourceId")}


def _question_week(question: dict) -> str:
    evidence = question.get("evidence", [])
    names = [item.get("name") for item in evidence if isinstance(item, dict)]
    locations = [item.get("location") for item in evidence if isinstance(item, dict)]
    return week_label(*names, *locations)


def _history(root: Path, user: str, course_id: str) -> tuple[list[dict], dict[str, dict]]:
    path = root / "study-sessions.db"
    if not path.exists():
        return [], {}
    exams, attempts = [], {}
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='study_exams'").fetchone():
            return [], {}
        rows = db.execute("SELECT score,state FROM study_exams WHERE user=?", (user,)).fetchall()
    for score, encoded in rows:
        state = _safe_json(encoded)
        selection = state.get("selection") if isinstance(state.get("selection"), dict) else {}
        if selection.get("courseId") != course_id or not state.get("questions"):
            continue
        exams.append({"score": float(score) if isinstance(score, (int, float)) else None})
        outcomes = {item.get("questionId"): item for item in state.get("history", []) if isinstance(item, dict)}
        for question in state["questions"]:
            if not isinstance(question, dict):
                continue
            for resource_id in _question_resources(question):
                item = attempts.setdefault(resource_id, {"attempts": 0, "weak": 0, "weeks": defaultdict(lambda: {"attempts": 0, "weak": 0})})
                result = outcomes.get(question.get("id"), {})
                item["attempts"] += 1
                is_weak = result.get("outcome") in {"incorrect", "partial", "skipped"}
                item["weak"] += int(is_weak)
                week = _question_week(question)
                item["weeks"][week]["attempts"] += 1
                item["weeks"][week]["weak"] += int(is_weak)
    return exams, attempts


def _candidate_counts(root: Path, user: str, resource_ids: set[str]) -> dict[str, int]:
    path = root / "analysis-records.db"
    if not path.exists() or not resource_ids:
        return {}
    counts = defaultdict(set)
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analyses'").fetchone():
            return {}
        rows = db.execute("SELECT value FROM analyses WHERE user=?", (user,)).fetchall()
    for (encoded,) in rows:
        record = _safe_json(encoded)
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        for question in data.get("candidates", []) if isinstance(data.get("candidates"), list) else []:
            if not isinstance(question, dict):
                continue
            signature = question.get("question")
            for resource_id in _question_resources(question) & resource_ids:
                if signature:
                    counts[resource_id].add(signature)
    return {resource_id: len(values) for resource_id, values in counts.items()}


def build_plan(root, user: str, courses: list[dict], resources: list[dict], *, course_ids=None) -> dict:
    root = Path(root)
    allowed = set(course_ids) if course_ids is not None else {item["id"] for item in courses}
    course_map = {item["id"]: item for item in courses if item.get("id") in allowed}
    grouped = defaultdict(lambda: defaultdict(list))
    for resource in resources:
        if resource.get("courseId") not in course_map or resource.get("downloadStatus") == "PROHIBITED":
            continue
        grouped[resource["courseId"]][week_label(resource.get("title"), resource.get("fileName"))].append(resource)
    result = []
    for course_id, course in course_map.items():
        exams, attempts = _history(root, user, course_id)
        scores = [item["score"] for item in exams if item["score"] is not None]
        score = round(sum(scores) / len(scores), 1) if scores else None
        total_attempts = sum(item["attempts"] for item in attempts.values())
        total_weak = sum(item["weak"] for item in attempts.values())
        weakness = round(total_weak / total_attempts * 100) if total_attempts else 0
        weeks = []
        candidate_counts = _candidate_counts(root, user, {item["id"] for items in grouped[course_id].values() for item in items})
        for label, items in sorted(grouped[course_id].items(), key=lambda item: (int(re.search(r"\d+", item[0]).group()) if re.search(r"\d+", item[0]) else 999, item[0])):
            ids = {item["id"] for item in items}
            week_attempts = sum(attempts.get(resource_id, {}).get("weeks", {}).get(label, {}).get("attempts", 0) for resource_id in ids)
            week_weak = sum(attempts.get(resource_id, {}).get("weeks", {}).get(label, {}).get("weak", 0) for resource_id in ids)
            weeks.append({"label": label, "resources": [item["title"] for item in items],
                          "resourceIds": sorted(ids), "candidateCount": sum(candidate_counts.get(resource_id, 0) for resource_id in ids),
                          "attempts": week_attempts, "weakAttempts": week_weak,
                          "status": "보완 우선" if week_weak else "학습 예정"})
        result.append({"courseId": course_id, "courseName": course.get("name", "과목 확인 필요"),
                       "score": score, "weakness": weakness, "focus": review_focus(score, weakness),
                       "reviewMix": dict(REVIEW_MIX), "weeks": weeks})
    return {"courses": result, "answer": "주차별 자료를 기준으로 학습 순서와 시험 전 복습 비율을 정리했어요."}


def select_review_questions(states: list[dict], course_id: str, count: int, weeks=None) -> dict:
    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError("복습 문항 수는 1~200개 사이여야 해요.")
    wanted = set(weeks or [])
    all_questions, weak, core = [], [], []
    scores = []
    for state in states:
        selection = state.get("selection") if isinstance(state.get("selection"), dict) else {}
        if selection.get("courseId") != course_id or not state.get("questions"):
            continue
        if state.get("score") is not None:
            scores.append(float(state["score"]))
        outcomes = {item.get("questionId"): item for item in state.get("history", []) if isinstance(item, dict)}
        for question in state["questions"]:
            if not isinstance(question, dict):
                continue
            label = _question_week(question)
            if wanted and label not in wanted:
                continue
            signature = (question.get("question", "").strip(), tuple(sorted(_question_resources(question))))
            item = {**question, "_reviewSignature": signature}
            if any(existing.get("_reviewSignature") == signature for existing in all_questions):
                continue
            all_questions.append(item)
            result = outcomes.get(question.get("id"), {})
            (weak if result.get("outcome") in {"incorrect", "partial", "skipped"} else core).append(item)
    if not all_questions:
        raise ValueError("아직 해당 과목의 저장된 문제은행이 없습니다. 주차별 문제를 먼저 만들어 주세요.")
    randomizer = __import__("random").SystemRandom()
    for pool in (weak, core, all_questions):
        randomizer.shuffle(pool)
    weak_count = round(count * REVIEW_MIX["weak"] / 100)
    core_count = round(count * REVIEW_MIX["core"] / 100)
    chosen = weak[:weak_count] + core[:core_count]
    chosen_signatures = {item["_reviewSignature"] for item in chosen}
    chosen.extend(item for item in all_questions if item["_reviewSignature"] not in chosen_signatures)
    chosen = chosen[:count]
    for item in chosen:
        item.pop("_reviewSignature", None)
    return {"questions": chosen, "courseScore": round(sum(scores) / len(scores), 1) if scores else None,
            "reviewMix": dict(REVIEW_MIX), "questionCount": len(chosen),
            "weeks": sorted({_question_week(item) for item in chosen})}
