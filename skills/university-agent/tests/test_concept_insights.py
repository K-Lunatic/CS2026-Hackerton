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
    def test_mixed_record_never_reveals_blocked_or_other_course_concepts(self):
        with tempfile.TemporaryDirectory() as temp:
            sources = [{'resourceId': f'r{i}', 'courseId': f'c{i}', 'name': f'자료{i}', 'location': 'p.1', 'quote': f'원문{i}'} for i in (1, 2)]
            analysis_records.save(Path(temp), 'student', sources, {}, 'concepts', {'concepts': [
                {'concept': f'개념{i}', 'explanation': f'설명{i}', 'evidence': [source]}
                for i, source in enumerate(sources, 1)]})
            resources = [{'id': 'r1', 'courseId': 'c1'}, {'id': 'r2', 'courseId': 'c2', 'downloadStatus': 'PROHIBITED'}]
            for scope in (None, {'c1'}):
                result = build_insights(temp, 'student', resources, course_ids=scope)
                self.assertEqual([c['concept'] for c in result['concepts']], ['개념1'])

    def test_own_attachment_history_remains_available_in_its_course_only(self):
        with tempfile.TemporaryDirectory() as temp:
            source = {'resourceId': 'attachment-abc', 'courseId': 'user-attachment', 'name': '첨부', 'location': 'p.1', 'quote': '연결 리스트'}
            analysis_records.save(Path(temp), 'student', [source], {}, 'concepts', {'concepts': [
                {'concept': '리스트', 'explanation': '첨부 자료 설명', 'evidence': [source]}]})
            self.assertEqual(build_insights(temp, 'student', [], course_ids={'user-attachment'})['total'], 1)
            self.assertEqual(build_insights(temp, 'student', [], course_ids={'other'})['total'], 0)
            self.assertEqual(build_insights(temp, 'student', [], course_ids=set())['total'], 0)
            self.assertEqual(build_insights(temp, 'other', [])['total'], 0)

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
            blocked = [{**item, 'downloadStatus': 'PROHIBITED'} for item in resources]
            for scope in (None, {'c1'}):
                self.assertEqual(build_insights(root, 'student', blocked, course_ids=scope)['total'], 0)

    def test_guidance_routes_to_saved_concept_material(self):
        result = guidance_request("약한 개념을 먼저 복습할 학습 자료 보여줘")
        self.assertTrue(result["needsConceptInsights"])
        self.assertIn("study-insights", result["nextCommands"][0])
        self.assertIsNone(guidance_request("핵심 개념 학습시켜줘"))
        self.assertIsNone(guidance_request("학습 자료 만들어줘"))

    def test_keeps_importance_by_source_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            source = {"resourceId": "r1", "courseId": "c1", "name": "자료", "location": "p.1", "quote": "핵심"}
            analysis_records.save(Path(temp), "student", [source], {}, "exam_analysis", {"units": [{
                "learning": [{"concept": "핵심", "explanation": "설명", "evidence": [source],
                               "importanceBySource": [
                                   {"sourceKind": "everytime_review", "score": 80, "reason": "후기에서 반복", "referenceIds": ["ref-e"]},
                                   {"sourceKind": "ai_evaluation", "score": 40, "reason": "본문 연결도", "referenceIds": []},
                               ]}],
            }]})
            result = build_insights(Path(temp), "student", [{"id": "r1", "courseId": "c1"}], course_ids={"c1"})
            self.assertEqual(result["concepts"][0]["importanceBySource"], [
                {"sourceKind": "ai_evaluation", "score": 40, "reasons": ["본문 연결도"], "referenceIds": []},
                {"sourceKind": "everytime_review", "score": 80, "reasons": ["후기에서 반복"], "referenceIds": ["ref-e"]},
            ])


if __name__ == "__main__":
    unittest.main()
