"""Saved exam -> HTTP answers -> host-AI rubric -> HTTP results, no live account."""
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import test_study
from test_project import ProjectTestBase
from features.exam_web import create_exam_server
from features.study import settings


class ExamWebTests(ProjectTestBase):
    def setUp(self):
        # Same isolated source fixture as study tests, without inheriting their test cases.
        ProjectTestBase.setUp(self)
        self.files = self.db_path.parent / 'files'
        self.file = self.files / 'course-1' / 'w3.txt'
        self.file.parent.mkdir(parents=True)
        self.file.write_text('이진 탐색은 정렬된 배열에서 탐색한다.\n반복마다 탐색 범위를 절반으로 줄인다.\n', encoding='utf-8')
        from storage.local_db import LocalDatabase
        from test_project import snapshot, upsert
        with_db = LocalDatabase(self.db_path)
        data = snapshot()
        data['courses'][0]['name'] = '자료구조'
        data['resources'] = [dict(data['resources'][0], title='3주차 탐색', fileName='w3.txt', extension='txt', localPath=str(self.file))]
        upsert(with_db, 'fixture-user', data)
        with_db.close()

    session = test_study.StudyFlowTests.session
    question = staticmethod(test_study.StudyFlowTests.question)
    def extract_questions(self, session, prepared, questions):
        refs = questions[0]['evidence']
        return session.call({'action': 'extract', 'requestId': prepared['requestId'], 'data': {
            'context': '정렬된 배열에서 이진 탐색하는 수업',
            'learning': [{'concept': '이진 탐색', 'explanation': '정렬 조건과 범위 축소', 'evidence': refs}],
            'types': [{'type': t, 'reason': '탐색 조건을 확인한다'} for t in dict.fromkeys(q['type'] for q in questions)],
            'questions': questions, 'shortageReason': '이 부분의 근거 있는 후보만 저장'}})

    def make_exam(self):
        session = self.session('exam')
        prepared = session.call({'action': 'request', 'selection': {'courseId': 'course-1'},
                                 'settings': {'count': 6, 'choices': 3, 'delivery': 'web'}})
        types = ['mcq', 'short', 'essay', 'code_fix', 'code_output', 'trace_table']
        questions = []
        for index, kind in enumerate(types, 1):
            q = self.question('mcq' if kind == 'mcq' else 'short' if kind == 'short' else 'essay', index)
            q['type'] = kind
            if kind == 'mcq':
                q['options'] = q['options'][:3]
            if kind.startswith('code_'):
                q.update(code='int low = 0;\nint high = 7;\nint mid = (low + high) / 2;', language='java')
            if kind == 'trace_table':
                q.update(typeLabel='탐색 과정 표 작성', responseFormat='text')
            q.update(rubric=['정렬 조건', '탐색 범위 변화'], keywords=['정렬', '절반'])
            questions.append(q)
        self.extract_questions(session, prepared, questions)
        session.call({'action': 'assemble', 'candidateIds': [f'c{i}' for i in range(1, 7)]})
        return session

    def test_web_all_types_drafts_ai_grades_and_no_answer_leaks(self):
        s = self.make_exam()
        public = s.call({'action': 'web_status'})
        self.assertEqual([q['type'] for q in public['questions']], ['mcq', 'short', 'essay', 'code_fix', 'code_output', 'trace_table'])
        serialized = json.dumps(public, ensure_ascii=False)
        for secret in ('acceptedAnswers', 'rubric', 'keywords', 'evidence', 'hostOnly', 'explanation'):
            self.assertNotIn(secret, serialized)
        answers = {q['id']: '1' if q['type'] == 'mcq' else '정렬된 배열' for q in public['questions']}
        draft = s.call({'action': 'draft', 'examId': public['examId'], 'revision': 0, 'answers': answers})
        self.assertEqual(self.session('exam').call({'action': 'web_status'})['drafts'], answers)
        with self.assertRaises(ValueError):
            s.call({'action': 'draft', 'examId': public['examId'], 'revision': 0, 'answers': {}})
        for action in ('hint', 'reveal', 'stop', 'submit'):
            with self.assertRaises(ValueError): s.call({'action': action, 'questionId': 'q1'})
        submitted = s.call({'action': 'web_submit', 'examId': draft['examId'], 'revision': 1, 'answers': answers})
        self.assertEqual(submitted['status'], 'grading')
        self.assertNotIn('hostOnly', submitted)
        pending = self.session('exam').call({'action': 'status'})
        self.assertEqual(len(pending['hostOnly']['answers']), 6)  # Includes MCQ: AI grades all web answers.
        grades = [{'questionId': item['questionId'], 'criteria': [
            {'criterion': '정렬 조건', 'met': True, 'feedback': '정렬 조건 충족'},
            {'criterion': '탐색 범위 변화', 'met': False, 'feedback': '범위 변화 누락'}]}
                  for item in pending['hostOnly']['answers']]
        with self.assertRaises(ValueError):
            s.call({'action': 'grade_batch', 'gradeId': 'stale', 'grades': grades})
        result = s.call({'action': 'grade_batch', 'gradeId': pending['gradeId'], 'grades': grades})
        self.assertEqual(result['summary']['score'], 30)
        finished = s.call({'action': 'web_status'})
        self.assertEqual(finished['summary']['score'], 30)
        self.assertEqual(len(finished['feedback']), 6)
        self.assertIn('answer', finished['feedback'][0])
        with self.assertRaises(ValueError): s.call({'action': 'web_submit', 'examId': draft['examId'], 'revision': 1, 'answers': answers})

    def test_web_blank_answers_and_invalid_grading_are_atomic(self):
        s = self.make_exam(); public = s.call({'action': 'web_status'})
        answers = {q['id']: '' for q in public['questions']}
        with self.assertRaises(ValueError):
            s.call({'action': 'web_submit', 'examId': 'old', 'revision': 0, 'answers': answers})
        s.call({'action': 'web_submit', 'examId': public['examId'], 'revision': 0, 'answers': answers})
        pending = s.call({'action': 'status'})
        grades = [{'questionId': item['questionId'], 'criteria': [
            {'criterion': criterion, 'met': False, 'feedback': '미응답'} for criterion in item['question']['rubric']]}
                  for item in pending['hostOnly']['answers']]
        grades[-1]['criteria'][0]['met'] = True
        with self.assertRaises(ValueError): s.call({'action': 'grade_batch', 'gradeId': pending['gradeId'], 'grades': grades})
        self.assertEqual(s.call({'action': 'status'})['gradeId'], pending['gradeId'])
        self.assertEqual(s.call({'action': 'web_status'})['status'], 'grading')
        grades[-1]['criteria'][0]['met'] = False
        result = s.call({'action': 'grade_batch', 'gradeId': pending['gradeId'], 'grades': grades})
        self.assertEqual(result['summary']['score'], 0)

    def test_http_only_accepts_exam_answers_and_loopback_token(self):
        session = self.make_exam()
        server, url = create_exam_server(session)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base, token = url.split('#')
        def request(path, data=None, access=token, origin=None):
            headers = {'X-Exam-Token': access, 'Content-Type': 'application/json'}
            if origin: headers['Origin'] = origin
            req = Request(base.rstrip('/') + path, headers=headers, data=json.dumps(data).encode() if data is not None else None)
            with urlopen(req, timeout=3) as response:
                return response.read()
        for access, origin in [('', None), (token, 'https://example.invalid')]:
            with self.assertRaises(HTTPError) as exc: request('/api/exam', access=access, origin=origin)
            self.assertEqual(exc.exception.code, 403)
        page = request('/').decode()
        self.assertIn('답안 제출하기', page)
        self.assertNotIn(token, page)
        public = json.loads(request('/api/exam'))
        self.assertNotIn('hostOnly', public)
        with self.assertRaises(HTTPError): request('/api/grade', {'action': 'grade_batch'})
        data = {'examId': public['examId'], 'revision': 0, 'answers': {'q1': '2'}}
        draft = json.loads(request('/api/draft', data))
        self.assertEqual(draft['drafts']['q1'], '2')
        data.update(revision=1, answers={q['id']: '' for q in public['questions']})
        self.assertEqual(json.loads(request('/api/submit', data))['status'], 'grading')
        self.assertNotIn('hostOnly', json.loads(request('/api/exam')))

    def test_custom_type_contract_and_choices(self):
        self.assertEqual(settings({'delivery': 'web', 'count': 4})['types'], ['auto'] * 4)
        self.assertEqual(settings({'delivery': 'web', 'choices': 7})['choices'], 7)
        with self.assertRaises(ValueError): settings({'types': ['<script>'], 'count': 1})

    def test_invalid_code_and_custom_definition_leave_generation_pending(self):
        s = self.session('invalid-exam')
        prepared = s.call({'action': 'request', 'selection': {'courseId': 'course-1'},
                           'settings': {'count': 1, 'delivery': 'web'}})
        q = dict(self.question('essay'), type='code_fix')
        with self.assertRaisesRegex(ValueError, '예제 코드'):
            self.extract_questions(s, prepared, [q])
        self.assertEqual(s.call({'action': 'status'})['status'], 'prepared')
        q.update(type='comparison', typeLabel='비교 분석')
        with self.assertRaisesRegex(ValueError, 'responseFormat'):
            self.extract_questions(s, prepared, [q])
        q['responseFormat'] = 'text'
        self.extract_questions(s, prepared, [q])
        s.call({'action': 'assemble', 'candidateIds': ['c1']})
        old = s.call({'action': 'web_status'})
        prepared = s.call({'action': 'request', 'selection': {'courseId': 'course-1'},
                           'settings': {'count': 1, 'delivery': 'web'}})
        self.extract_questions(s, prepared, [q])
        s.call({'action': 'assemble', 'candidateIds': ['c1']})
        with self.assertRaisesRegex(ValueError, '시험지가 바뀌었어요'):
            s.call({'action': 'web_submit', 'examId': old['examId'], 'revision': 0, 'answers': {'q1': '오래된 답'}})
        self.assertEqual(s.call({'action': 'web_status'})['drafts'], {})

    def test_cli_can_resume_ai_grading_from_event_file(self):
        s = self.make_exam(); public = s.call({'action': 'web_status'})
        s.call({'action': 'web_submit', 'examId': public['examId'], 'revision': 0,
                'answers': {q['id']: '' for q in public['questions']}})
        pending = self.cli('study', '--conversation', 'exam', '--event-json', '{"action":"status"}')
        self.assertTrue(pending['hostOnly']['SOURCE'])
        event = {'action': 'grade_batch', 'gradeId': pending['gradeId'], 'grades': [
            {'questionId': item['questionId'], 'criteria': [{'criterion': criterion, 'met': False, 'feedback': '미응답'}
             for criterion in item['question']['rubric']]} for item in pending['hostOnly']['answers']]}
        path = self.db_path.parent / 'grades.json'
        path.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
        result = self.cli('study', '--conversation', 'exam', '--event-file', str(path))
        self.assertEqual(result['summary']['score'], 0)
