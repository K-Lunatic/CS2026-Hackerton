import tempfile
import unittest
from pathlib import Path

from features.learning_plan import build_plan, select_review_questions, week_label


class LearningPlanTests(unittest.TestCase):
    def test_week_label_accepts_korean_and_english_file_names(self):
        self.assertEqual(week_label('3주차 탐색.pptx'), '3주차')
        self.assertEqual(week_label('week-04-arrays.pdf'), '4주차')
        self.assertEqual(week_label('extra.pdf'), '주차 미지정')

    def test_review_questions_keep_fixed_mix_and_prioritize_weak_items(self):
        evidence = lambda number: [{'resourceId': 'r1', 'name': f'{number}주차 자료', 'location': '본문', 'quote': '근거'}]
        questions = [
            {'id': 'q1', 'question': '약한 문제', 'concept': 'A', 'evidence': evidence(1)},
            {'id': 'q2', 'question': '핵심 문제', 'concept': 'B', 'evidence': evidence(1)},
            {'id': 'q3', 'question': '무작위 문제', 'concept': 'C', 'evidence': evidence(2)},
        ]
        plan = select_review_questions([{
            'score': 55, 'selection': {'courseId': 'c1'}, 'questions': questions,
            'history': [{'questionId': 'q1', 'outcome': 'incorrect'}],
        }], 'c1', 3)
        self.assertEqual(plan['reviewMix'], {'weak': 50, 'core': 30, 'random': 20})
        self.assertEqual(plan['courseScore'], 55.0)
        self.assertEqual(plan['questionCount'], 3)
        self.assertEqual(plan['questions'][0]['question'], '약한 문제')

    def test_plan_groups_downloadable_resources_by_week(self):
        with tempfile.TemporaryDirectory() as folder:
            result = build_plan(Path(folder), 'student',
                                [{'id': 'c1', 'name': '자료구조'}],
                                [{'id': 'r1', 'courseId': 'c1', 'title': '3주차 탐색', 'fileName': 'w3.txt', 'downloadStatus': 'DOWNLOADED'},
                                 {'id': 'r2', 'courseId': 'c1', 'title': '금지 자료', 'fileName': 'w4.pdf', 'downloadStatus': 'PROHIBITED'}])
        self.assertEqual(result['courses'][0]['weeks'][0]['label'], '3주차')
        self.assertEqual(result['courses'][0]['weeks'][0]['resources'], ['3주차 탐색'])


if __name__ == '__main__':
    unittest.main()
