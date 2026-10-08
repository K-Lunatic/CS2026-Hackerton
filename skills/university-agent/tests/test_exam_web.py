"""Saved exam -> HTTP answers -> host-AI rubric -> HTTP results, no live account."""
import json
import sqlite3
import threading
import socket
import queue
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
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

    def make_exam(self, time_limit=None):
        session = self.session('exam')
        exam_settings = {'count': 6, 'choices': 3, 'delivery': 'web'}
        if time_limit is not None:
            exam_settings['timeLimitMinutes'] = time_limit
        prepared = session.call({'action': 'request', 'selection': {'courseId': 'course-1'}, 'settings': exam_settings})
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
        self.assertEqual(result['summary']['score'], 50)
        self.assertEqual(result['summary']['totalPoints'], 100)
        finished = s.call({'action': 'web_status'})
        self.assertEqual(finished['summary']['score'], 50)
        self.assertEqual(finished['totalPoints'], 100)
        self.assertEqual(len(finished['feedback']), 6)
        self.assertIn('answer', finished['feedback'][0])
        with self.assertRaises(ValueError): s.call({'action': 'web_submit', 'examId': draft['examId'], 'revision': 1, 'answers': answers})
        s.call({'action': 'cancel'})
        archived = s.call({'action': 'exam_review', 'examId': public['examId']})
        self.assertTrue(archived['readOnly'])
        self.assertEqual(archived['summary']['score'], finished['summary']['score'])
        self.assertEqual(archived['feedback'], finished['feedback'])

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
        self.assertEqual(result['summary']['totalPoints'], 100)

    def test_overall_score_is_normalized_when_question_points_change(self):
        from features.study import summary
        result = summary({'questions': [{'points': 10}, {'points': 30}], 'history': [
            {'score': 5, 'outcome': 'partial', 'hintUsed': False, 'concept': 'A', 'evidence': []},
            {'score': 30, 'outcome': 'correct', 'hintUsed': False, 'concept': 'B', 'evidence': []}]})
        self.assertEqual(result['score'], 87.5)
        self.assertEqual(result['totalPoints'], 100)

    def test_ordering_question_preserves_blocks_and_accepts_numbered_order(self):
        s = self.session('ordering')
        prepared = s.call({'action': 'request', 'selection': {'courseId': 'course-1'},
                           'settings': {'count': 1, 'types': ['ordering'], 'delivery': 'web'}})
        q = self.question('short')
        q.update(type='ordering', typeLabel='순서 배열형', responseFormat='ordering',
                 blocks=['범위 확인', '중간값 계산', '왼쪽 또는 오른쪽 선택'],
                 answer=json.dumps([0, 1, 2], ensure_ascii=False), rubric=['올바른 절차'])
        self.extract_questions(s, prepared, [q]); s.call({'action': 'assemble', 'candidateIds': ['c1']})
        public = s.call({'action': 'web_status'})
        self.assertEqual(public['questions'][0]['responseFormat'], 'ordering')
        self.assertEqual(public['questions'][0]['blocks'], q['blocks'])
        draft = s.call({'action': 'draft', 'examId': public['examId'], 'revision': 0,
                        'answers': {'q1': '[2,1,0]'}})
        self.assertEqual(draft['drafts']['q1'], '[2,1,0]')

    def test_confusion_mark_is_saved_separately_from_grading_feedback(self):
        s = self.make_exam(); public = s.call({'action': 'web_status'})
        marked = s.call({'action': 'confusion_toggle', 'examId': public['examId'], 'questionId': 'q2', 'confused': True})
        self.assertEqual(marked['confusedIds'], ['q2'])
        self.assertEqual(s.call({'action': 'web_status'})['confusedIds'], ['q2'])
        cleared = s.call({'action': 'confusion_toggle', 'examId': public['examId'], 'questionId': 'q2', 'confused': False})
        self.assertEqual(cleared['confusedIds'], [])

    def test_web_clock_records_elapsed_time_and_optional_limit(self):
        s = self.make_exam(time_limit=50)
        public = s.call({'action': 'web_status'})
        self.assertEqual(public['timeLimitSeconds'], 3000)
        self.assertIsInstance(public['elapsedSeconds'], int)
        self.assertGreaterEqual(public['remainingSeconds'], 0)
        answers = {q['id']: '' for q in public['questions']}
        submitted = s.call({'action': 'web_submit', 'examId': public['examId'], 'revision': 0, 'answers': answers})
        self.assertEqual(submitted['status'], 'grading')
        self.assertIsInstance(submitted['elapsedSeconds'], int)
        self.assertEqual(submitted['timeLimitSeconds'], 3000)
        self.assertEqual(submitted['remainingSeconds'] + submitted['overtimeSeconds'],
                         max(submitted['elapsedSeconds'], 3000))
        self.assertEqual(s.call({'action': 'web_status'})['elapsedSeconds'], submitted['elapsedSeconds'])

    def test_question_timestamps_accumulate_and_skip_unvisited_questions(self):
        from features.study import StudySession
        self.assertEqual(StudySession._question_times({'questionTimeline': [
            {'questionId': 'q2', 'at': '2026-10-07T00:00:00+00:00'},
            {'questionId': 'q1', 'at': '2026-10-07T00:00:05+00:00'},
            {'questionId': 'q2', 'at': '2026-10-07T00:00:08+00:00'}],
            'submittedAt': '2026-10-07T00:00:12+00:00'}), {'q2': 9, 'q1': 3})
        s = self.make_exam(); public = s.call({'action': 'web_status'})
        s.call({'action': 'question_focus', 'examId': public['examId'], 'questionId': 'q2'})
        s.call({'action': 'question_focus', 'examId': public['examId'], 'questionId': 'q1'})
        answers = {q['id']: '' for q in public['questions']}
        submitted = s.call({'action': 'web_submit', 'examId': public['examId'], 'revision': 0, 'answers': answers})
        self.assertEqual(set(submitted['questionTimes']), {'q1', 'q2'})
        self.assertNotIn('q3', submitted['questionTimes'])

    def test_previous_exam_retains_answers_after_new_request_and_is_read_only(self):
        from features.study import StudySession
        s = self.make_exam()
        original = s.call({'action': 'web_status'})
        s.call({'action': 'draft', 'examId': original['examId'], 'revision': 0, 'answers': {'q1': '1'}})
        s.call({'action': 'request', 'selection': {'courseId': 'course-1'}, 'settings': {'count': 6, 'choices': 3}})
        s.call({'action': 'assemble', 'candidateIds': [f'c{i}' for i in range(1, 7)]})
        history = s.call({'action': 'exam_history'})
        self.assertEqual(len(history['exams']), 2)
        self.assertEqual(len(self.cli('exam-history')['exams']), 2)
        self.assertTrue(all('answer' not in entry and 'questions' not in entry for entry in history['exams']))
        viewer = StudySession(s.path, s.user, 'another-chat', s.provider, s.files_root, exam_id=original['examId'])
        review = viewer.call({'action': 'web_status'})
        self.assertTrue(review['readOnly'])
        self.assertEqual(review['drafts']['q1'], '1')
        self.assertEqual(review['questions'], original['questions'])
        self.assertNotIn('rubric', json.dumps(review))
        with self.assertRaises(ValueError):
            viewer.call({'action': 'draft', 'examId': original['examId'], 'revision': 1, 'answers': {}})
        other = StudySession(s.path, 'another-user', 'another-chat', s.provider, s.files_root, exam_id=original['examId'])
        with self.assertRaises(ValueError):
            other.call({'action': 'web_status'})
        server, url = create_exam_server(viewer)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base, token = url.split('#')
        headers = {'X-Exam-Token': token, 'Content-Type': 'application/json'}
        with urlopen(Request(base + 'api/exam', headers=headers), timeout=3) as response:
            self.assertTrue(json.loads(response.read())['readOnly'])
        with self.assertRaises(HTTPError) as exc:
            urlopen(Request(base + 'api/draft', headers=headers,
                data=json.dumps({'examId': original['examId'], 'revision': 1, 'answers': {}}).encode()), timeout=3)
        self.assertEqual(exc.exception.code, 409)

    def test_saved_exam_can_start_a_shuffled_web_copy(self):
        s = self.make_exam()
        original = s.call({'action': 'web_status'})
        shuffled = s.call({'action': 'shuffle_exam', 'examId': original['examId'], 'count': 4})
        self.assertTrue(shuffled['needsWeb'])
        self.assertEqual(shuffled['total'], 4)
        self.assertNotEqual(shuffled['examId'], original['examId'])
        current = s.call({'action': 'web_status'})
        self.assertEqual(len(current['questions']), 4)
        self.assertTrue({q['question'] for q in current['questions']} <= {q['question'] for q in original['questions']})
        self.assertNotIn('answer', json.dumps(current, ensure_ascii=False))
        from features.study import StudySession
        original_view = StudySession(s.path, s.user, 'original-review', s.provider, s.files_root, exam_id=original['examId'])
        self.assertEqual(original_view.call({'action': 'web_status'})['questions'], original['questions'])
        history = s.call({'action': 'exam_history'})
        self.assertEqual(len(history['exams']), 2)

    def test_saved_exam_shuffle_can_follow_area_quotas(self):
        from features.study import StudySession
        source = {'questions': [
            {'id': 'a', 'type': 'short', 'unitId': 'u1', 'evidence': [{'resourceId': 'r1'}]},
            {'id': 'b', 'type': 'short', 'unitId': 'u1', 'evidence': [{'resourceId': 'r1'}]},
            {'id': 'c', 'type': 'short', 'unitId': 'u2', 'evidence': [{'resourceId': 'r2'}]},
        ], 'settings': {}, 'selection': {}, 'sources': []}
        state = StudySession.shuffled_state(source, {'examId': 'source', 'sections': [
            {'unitId': 'u1', 'count': 1}, {'resourceId': 'r2', 'count': 1}], 'count': 2})
        self.assertEqual(len(state['questions']), 2)
        self.assertEqual({q['unitId'] for q in state['questions']}, {'u1', 'u2'})

    def test_saved_exam_can_be_split_into_selectable_web_sets(self):
        s = self.make_exam()
        original = s.call({'action': 'web_status'})
        selector = s.call({'action': 'set_collection_start', 'examId': original['examId'], 'setSize': 2})
        self.assertEqual(selector['status'], 'set_selector')
        self.assertEqual(selector['totalSets'], 3)
        self.assertEqual(selector['sets'][0]['questionCount'], 2)
        chosen = s.call({'action': 'set_select', 'collectionId': selector['collectionId'], 'setId': 'set-2'})
        self.assertTrue(chosen['needsWeb'])
        self.assertEqual(chosen['total'], 2)
        self.assertEqual(s.call({'action': 'sets_status'})['completedSets'], 0)
        self.assertNotEqual(chosen['examId'], original['examId'])

    def test_set_selector_server_opens_one_set_as_exam(self):
        s = self.make_exam()
        original = s.call({'action': 'web_status'})
        selector = s.call({'action': 'set_collection_start', 'examId': original['examId'], 'setSize': 2})
        server, url = create_exam_server(s, view='sets')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base, token = url.split('#')
        headers = {'X-Exam-Token': token}
        with urlopen(Request(base, headers=headers), timeout=3) as response:
            self.assertIn('문제 세트', response.read().decode('utf-8'))
        with urlopen(Request(base + 'api/sets', headers=headers), timeout=3) as response:
            self.assertEqual(json.loads(response.read())['totalSets'], 3)
        request = Request(base + 'api/sets/select', headers={**headers, 'Content-Type': 'application/json'},
                          data=json.dumps({'collectionId': selector['collectionId'], 'setId': 'set-1'}).encode())
        with urlopen(request, timeout=3) as response:
            self.assertEqual(json.loads(response.read())['total'], 2)
        with urlopen(Request(base + 'exam', headers=headers), timeout=3) as response:
            self.assertIn('문제 세트 목록으로 돌아가기', response.read().decode('utf-8'))
        with urlopen(Request(base + 'api/exam', headers=headers), timeout=3) as response:
            self.assertEqual(json.loads(response.read())['setIndex'], 1)

    def test_concept_match_board_grades_pairs_and_preserves_source_exam(self):
        from features.study import StudySession
        s = self.make_exam()
        original = s.call({'action': 'web_status'})
        started = s.call({'action': 'match_start', 'examId': original['examId']})
        self.assertEqual(started['status'], 'matching')
        self.assertEqual(len(started['leftTiles']), 6)
        self.assertEqual(len(started['rightTiles']), 6)
        self.assertNotEqual([item['id'] for item in started['leftTiles']], [item['id'] for item in started['rightTiles']])
        board = started
        with sqlite3.connect(s.path) as db:
            saved = json.loads(db.execute('SELECT state FROM study_sessions WHERE conversation=?', ('exam',)).fetchone()[0])
        wrong_left, wrong_right = saved['match']['pairs'][0]['leftId'], saved['match']['pairs'][1]['rightId']
        board = s.call({'action': 'match_pick', 'matchId': board['matchId'],
                        'leftId': wrong_left, 'rightId': wrong_right})
        self.assertFalse(board['correct'])
        self.assertTrue(board['reshuffled'])
        while board['status'] != 'finished':
            unmatched_left = [item for item in board['leftTiles'] if not item['matched']]
            unmatched_right = [item for item in board['rightTiles'] if not item['matched']]
            for left in unmatched_left:
                for right in unmatched_right:
                    board = s.call({'action': 'match_pick', 'matchId': board['matchId'],
                                    'leftId': left['id'], 'rightId': right['id']})
                    if board['status'] == 'finished' or board.get('correct'):
                        break
                if board['status'] == 'finished' or board.get('correct'):
                    break
        self.assertEqual(board['matched'], 6)
        self.assertIn('score', board)
        original_view = StudySession(s.path, s.user, 'match-source-review', s.provider, s.files_root, exam_id=original['examId'])
        self.assertTrue(original_view.call({'action': 'web_status'})['readOnly'])

    def test_match_server_serves_dedicated_board(self):
        s = self.make_exam()
        original = s.call({'action': 'web_status'})
        s.call({'action': 'match_start', 'examId': original['examId']})
        server, url = create_exam_server(s, view='match')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base, token = url.split('#')
        headers = {'X-Exam-Token': token}
        with urlopen(Request(base, headers=headers), timeout=3) as response:
            self.assertIn('개념 매칭', response.read().decode('utf-8'))
        with urlopen(Request(base + 'api/match', headers=headers), timeout=3) as response:
            self.assertEqual(json.loads(response.read())['status'], 'matching')

    def test_concept_match_uses_all_available_pairs_by_default(self):
        from features.study import StudySession
        source = {'questions': [{'concept': f'개념 {i}', 'answer': f'설명 {i}', 'explanation': f'설명 {i}'} for i in range(21)]}
        state = StudySession.matching_state(source, {})
        self.assertEqual(state['match']['count'], 21)
        self.assertEqual(len(state['match']['pairs']), 21)

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

    def test_idle_browser_preconnection_does_not_block_styles_or_questions(self):
        server, url = create_exam_server(self.make_exam())
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base, token = url.split('#')
        with socket.create_connection(server.server_address, timeout=3):
            # Leave a browser-like connection idle while other requests load the exam.
            with urlopen(base + 'exam.css', timeout=3) as response:
                self.assertIn(b'grid-template-columns', response.read())
            with urlopen(Request(base + 'api/exam', headers={'X-Exam-Token': token}), timeout=3) as response:
                self.assertEqual(len(json.loads(response.read())['questions']), 6)

    def test_launcher_serves_styled_exam_under_windows_pipe_encoding(self):
        self.make_exam()
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'exam_web.py'
        process = subprocess.Popen([sys.executable, str(script), '--conversation', 'exam', '--no-open'],
            env={**self.env, 'PYTHONIOENCODING': 'cp1252'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
            first = lines.get(timeout=10)
            self.assertTrue(first, process.stderr.read().decode('utf-8', errors='replace') if process.poll() is not None else 'No URL returned')
            result = json.loads(first.decode('utf-8'))
            self.assertIn('시험지', result['answer'])
            base, token = result['url'].split('#')
            with urlopen(base, timeout=3) as response:
                html = response.read().decode('utf-8')
            self.assertIn('class="paper"', html)
            self.assertIn('/exam.css', html)
            for asset, content_type in [('exam.css', 'text/css'), ('exam.js', 'text/javascript')]:
                with urlopen(base + asset, timeout=3) as response:
                    self.assertTrue(response.headers['Content-Type'].startswith(content_type))
                    self.assertTrue(response.read())
            with urlopen(Request(base + 'api/exam', headers={'X-Exam-Token': token}), timeout=3) as response:
                data = json.loads(response.read())
            self.assertEqual(len(data['questions']), 6)
        finally:
            process.terminate()
            process.communicate(timeout=5)

    def test_browser_launch_failure_keeps_live_exam_server(self):
        import exam_web as launcher
        self.make_exam()
        live = []
        def launch(url):
            with urlopen(url.split('#')[0], timeout=3) as response:
                live.append(response.status)
            raise OSError('browser unavailable')
        # main() owns this server, and must close it after the simulated stop.
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        def join(worker):
            if worker.is_alive():
                raise KeyboardInterrupt
        with patch.object(launcher, 'DB_PATH', self.db_path), patch.object(launcher, 'USER_ID', 'fixture-user'), patch.object(launcher, 'database', return_value=self.session('exam').provider), patch.object(sys, 'argv', ['exam_web', '--conversation', 'exam']), patch.object(launcher.webbrowser, 'open', side_effect=launch), patch.object(threading.Thread, 'join', join), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                launcher.main()
        self.assertEqual(live, [200])

    def test_custom_type_contract_and_choices(self):
        self.assertEqual(settings({'delivery': 'web', 'count': 4})['types'], ['auto'] * 4)
        self.assertEqual(len(settings({'delivery': 'web', 'count': 50})['types']), 50)
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
        self.assertTrue(prepared['needsAssembly'])
        self.assertEqual(prepared['reusedFiles'], 1)
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
