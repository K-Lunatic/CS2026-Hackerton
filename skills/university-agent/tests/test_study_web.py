"""Quiz page HTTP checks. Questions and grades are explicit fixtures, never a real AI result."""
import json
import threading
import urllib.error
import urllib.request

from test_project import ProjectTestBase, snapshot, upsert
import test_study
from storage.local_db import LocalDatabase
from features.study import StudySession
from study_web import create_server

TYPES = ['mcq', 'mcq', 'short']


class StudyWebTests(ProjectTestBase):
    def setUp(self):
        super().setUp()
        self.files = self.db_path.parent / 'files'
        file = self.files / 'course-1' / 'w3.txt'
        file.parent.mkdir(parents=True)
        file.write_text('이진 탐색은 정렬된 배열에서 탐색한다.\n', encoding='utf-8')
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data['resources'] = [dict(data['resources'][0], title='3주차 탐색', fileName='w3.txt', extension='txt', localPath=str(file))]
        upsert(db, 'fixture-user', data)
        db.close()
        self.server = create_server(self.call, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def call(self, event):
        db = LocalDatabase(self.db_path, read_only=True)
        try:
            return StudySession(self.db_path.parent / 'study-sessions.db', 'fixture-user', 'web', db, self.files).call(event)
        finally:
            db.close()

    def http(self, path='/api/quiz', event=None, headers=None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        if event is not None:
            request.data = json.dumps(event).encode('utf-8')
            request.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode('utf-8')

    def page(self, event=None):
        status, body = self.http(event=event)
        self.assertEqual(status, 200, body)
        return body, json.loads(body)

    def generate(self):
        prepared = self.call({'action': 'request', 'selection': {'courseId': 'course-1'}, 'settings': {'types': TYPES}})
        questions = [test_study.StudyFlowTests.question(kind, i) for i, kind in enumerate(TYPES, 1)]
        self.call({'action': 'generate', 'requestId': prepared['requestId'], 'data': {'questions': questions}})

    def test_page_assets_and_waiting_state(self):
        status, body = self.http('/')
        self.assertEqual(status, 200)
        self.assertIn('답안 제출하기', body)
        for path in ('/app.js', '/style.css', '/turtleneck.png'):
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
        self.assertEqual(self.http('/../scripts/study_web.py')[0], 404)
        self.assertEqual(self.page()[1], {'status': 'idle', 'questions': [], 'single': False})
        self.assertEqual(self.http(headers={'Host': 'example.com'})[0], 403)

    def test_answers_stay_hidden_until_each_question_is_finished(self):
        self.generate()
        body, view = self.page()
        self.assertEqual([q['type'] for q in view['questions']], TYPES)
        self.assertEqual(view['materials'], ['3주차 탐색'])
        for secret in ('자료에 명시되어 있습니다.', '정렬된 배열에서 탐색한다.', '배열 조건을 생각해보세요', 'hostOnly'):
            self.assertNotIn(secret, body)
        _, view = self.page({'action': 'hint', 'questionId': 'q1'})
        self.assertIn('배열 조건', view['questions'][0]['hint'])
        self.assertNotIn('hint', view['questions'][1])
        body, view = self.page({'action': 'reveal', 'questionId': 'q2'})
        self.assertEqual(view['questions'][1]['result']['outcome'], 'revealed')
        self.assertNotIn('result', view['questions'][0])

    def test_page_cannot_generate_or_grade_and_rejects_other_origins(self):
        self.generate()
        for action in ('generate', 'grade_batch', 'request', 'view', 'status'):
            self.assertEqual(self.http(event={'action': action})[0], 422)
        self.assertEqual(self.http(event={'action': 'stop'}, headers={'Origin': 'http://example.com'})[0], 403)
        status, body = self.http(event={'action': 'submit', 'answers': [{'questionId': 'q1', 'text': '1'}]})
        self.assertEqual(status, 422)
        self.assertIn('미응답 문항', json.loads(body)['error'])
        self.assertEqual(self.page()[1]['status'], 'question')

    def test_submit_from_page_then_host_grades_from_status(self):
        self.generate()
        answers = [{'questionId': 'q1', 'text': '1'}, {'questionId': 'q2', 'text': '2'}, {'questionId': 'q3', 'text': '정렬된 리스트를 탐색'}]
        body, view = self.page({'action': 'submit', 'answers': answers})
        self.assertNotIn('hostOnly', body)
        self.assertEqual(view['status'], 'grading')
        self.assertEqual([q.get('result', {}).get('outcome') for q in view['questions']], ['correct', 'incorrect', None])
        self.assertEqual(view['questions'][2]['submitted'], '정렬된 리스트를 탐색')
        self.assertNotIn('acceptedAnswers', body)
        # The host never saw the page's submit response; status hands it the same grading request.
        grading = self.call({'action': 'status'})
        self.assertEqual(grading['hostOnly']['answers'][0]['submitted'], '정렬된 리스트를 탐색')
        self.call({'action': 'grade_batch', 'gradeId': grading['gradeId'], 'grades': [{'questionId': 'q3',
                   'criteria': [{'criterion': '정렬 조건', 'met': True, 'feedback': '정렬 조건 충족'}]}]})
        _, view = self.page()
        self.assertEqual(view['status'], 'finished')
        self.assertEqual(view['counts']['correct'], 2)
        self.assertEqual(view['questions'][2]['result']['criteria'][0]['feedback'], '정렬 조건 충족')
        self.assertEqual(view['questions'][1]['result']['answer'], '정렬된 배열에서 탐색')

    def test_single_delivery_is_left_to_the_conversation(self):
        prepared = self.call({'action': 'request', 'selection': {'courseId': 'course-1'}, 'settings': {'types': ['mcq'], 'delivery': 'single'}})
        self.call({'action': 'generate', 'requestId': prepared['requestId'], 'data': {'questions': [test_study.StudyFlowTests.question()]}})
        self.assertEqual(self.page()[1], {'status': 'question', 'questions': [], 'single': True})


if __name__ == '__main__':
    import unittest
    unittest.main()
