import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features import analysis_records
from features.concept_insights import build_insights
from features.guidance import guidance_request


class ConceptInsightTests(unittest.TestCase):
    def test_ranks_weak_concepts_and_keeps_course_scope(self):
        resources = [
            {"id": "r1", "courseId": "c1", "name": "배열.pdf"},
            {"id": "r2", "courseId": "c2", "name": "네트워크.pdf"},
        ]
        source = {"resourceId": "r1", "courseId": "c1", "name": "배열.pdf", "location": "p.1", "quote": "배열은 연속 저장이다."}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            analysis_records.save(root, "student", [source], {}, "concepts", {
                "concepts": [{"concept": "배열", "explanation": "같은 자료형을 연속 저장합니다.", "evidence": [source]}]
            })
            other_source = {**source, "resourceId": "r2", "courseId": "c2", "name": "네트워크.pdf"}
            analysis_records.save(root, "student", [other_source], {}, "concepts", {
                "concepts": [{"concept": "네트워크", "explanation": "다른 과목 자료", "evidence": [other_source]}]
            })
            with sqlite3.connect(root / "study-sessions.db") as db:
                db.execute("CREATE TABLE study_exams (user TEXT, state TEXT)")
                db.execute("INSERT INTO study_exams VALUES (?, ?)", ("student", json.dumps({
                    "questions": [{"id": "q1", "concept": "배열", "evidence": [source]}],
                    "history": [{"questionId": "q1", "outcome": "incorrect", "hintUsed": True}],
                })))
                db.commit()
            result = build_insights(root, "student", resources, course_ids={"c1"})
            self.assertEqual(result["total"], 1)
            concept = result["concepts"][0]
            self.assertEqual(concept["concept"], "배열")
            self.assertGreater(concept["importance"], 0)
            self.assertGreater(concept["weakness"], 0)
            self.assertEqual(concept["learningMaterial"], "같은 자료형을 연속 저장합니다.")
            self.assertEqual(concept["evidence"][0]["location"], "p.1")

    def test_guidance_routes_to_saved_concept_material(self):
        result = guidance_request("약한 개념을 먼저 복습할 학습 자료 보여줘")
        self.assertTrue(result["needsConceptInsights"])
        self.assertIn("study-insights", result["nextCommands"][0])


if __name__ == "__main__":
    unittest.main()
