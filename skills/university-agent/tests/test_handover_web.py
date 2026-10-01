"""HTTP/review checks. Fixture AI is explicit; not a live-model test."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from features.handover import HandoverError
from features.handover_review import local_analysis, make_draft, review_tasks
from handover_web import create_server
from test_handover import NORMAL, SAMPLE


class ReviewTests(unittest.TestCase):
    def test_temporary_analysis_is_not_ai(self):
        data = local_analysis('프로젝트: 학교생활 AI\n' + SAMPLE)
        self.assertEqual(data['analysisSource'], 'local-rules')
        self.assertEqual(data['projectName'], '학교생활 AI')
        self.assertEqual({t['owner'] for t in data['completed']}, {'현우', '민수'})
        self.assertEqual(data['inProgress'][0]['deadline'], '10월 4일')
        self.assertEqual(data['pending'][0]['owner'], '미정')

    def test_missing_and_planned(self):
        with self.assertRaises(HandoverError): local_analysis(' ')
        data = local_analysis('연결 테스트를 할 예정이다.')
        self.assertFalse(data['completed'])
        self.assertEqual(data['pending'][0]['status'], '확인 필요')
        self.assertEqual(data['pending'][0]['deadline'], '미정')

    def test_conflict_and_order(self):
        data = local_analysis('10월 3일: 민수는 테스트 완료.\n10월 3일: 민수는 테스트 미완료.')
        self.assertFalse(data['completed'])
        self.assertTrue(data['checks'])
        data = local_analysis('10월 3일: 민수는 테스트 완료.\n10월 2일: 민수는 테스트 진행 중.')
        self.assertEqual(data['tasks'][0]['status'], '완료')
        self.assertEqual(len(data['tasks'][0]['evidence']), 2)

    def test_year_order_and_resource_evidence(self):
        data = local_analysis('2026년 1월 2일: 민수는 테스트 완료.\n2025년 12월 31일: 민수는 테스트 진행 중.\n관련 자료: /api/auth/login, docs/handover.md')
        self.assertEqual(data['tasks'][0]['status'], '완료')
        self.assertEqual({r['text'] for r in data['resources']}, {'/api/auth/login', 'docs/handover.md'})
        data = local_analysis('2026년 1월 2일: 민수는 테스트 완료.\n12월 31일: 민수는 테스트 미완료.')
        self.assertEqual(data['tasks'][0]['status'], '확인 필요')

    def test_task_subject_is_not_an_owner(self):
        for text, status in [('테스트는 완료.', '완료'), ('API는 진행 중.', '진행 중'),
                             ('테스트는 미완료.', '미완료'), ('테스트는 할 예정이다.', '확인 필요')]:
            with self.subTest(text=text):
                data = local_analysis(text)
                self.assertEqual(len(data['tasks']), 1)
                self.assertEqual(data['tasks'][0]['owner'], '미정')
                self.assertEqual(data['tasks'][0]['title'], text.split('는')[0])
                self.assertEqual(data['tasks'][0]['status'], status)

    def test_unrelated_work_is_not_merged(self):
        data = local_analysis('민수는 DB 담당.\n민수는 화면 구현 완료.')
        self.assertEqual({t['title']: t['status'] for t in data['tasks']},
                         {'DB': '확인 필요', '화면': '완료'})

    def test_multiple_roles_and_ambiguous_abbreviation(self):
        data = local_analysis('민수는 로그인 화면 담당. 민수는 관리자 화면 담당. 민수는 화면 구현 완료.')
        self.assertFalse(data['completed'])
        self.assertTrue(data['checks'])
        self.assertEqual({t['title'] for t in data['tasks']}, {'로그인 화면', '관리자 화면', '화면'})

    def test_inline_dates_match_line_separated_records(self):
        data = local_analysis(SAMPLE.replace('\n', ' '))
        expected = local_analysis(SAMPLE)
        fields = ('title', 'owner', 'status', 'deadline')
        self.assertEqual([{k: t[k] for k in fields} for t in data['tasks']],
                         [{k: t[k] for k in fields} for t in expected['tasks']])
        self.assertEqual(len(data['tasks']), 4)
        self.assertEqual({t['owner'] for t in data['completed']}, {'현우', '민수'})
        for task in data['tasks']:
            for evidence in task['evidence']:
                self.assertIn(evidence['quote'], data['records'][evidence['recordId']])

    def test_inline_dates_conflict_and_deadline_are_separate(self):
        data = local_analysis('10월 3일: 민수는 테스트 완료. 10월 2일: 민수는 테스트 진행 중.')
        self.assertEqual(data['tasks'][0]['status'], '완료')
        data = local_analysis('10월 3일: 민수는 테스트 완료. 10월 3일: 민수는 테스트 미완료.')
        self.assertEqual(data['tasks'][0]['status'], '확인 필요')
        self.assertTrue(data['checks'])
        data = local_analysis('10월 2일 회의: 지연은 API 작업 중이며 완료 목표는 10월 4일. 10월 3일 작업 기록: 지연은 API 완료.')
        self.assertEqual(data['tasks'][0]['status'], '완료')
        self.assertEqual(data['tasks'][0]['deadline'], '10월 4일')

    def test_edits_draft_and_immutable_evidence(self):
        data = local_analysis(SAMPLE)
        before = deepcopy(data)
        edits = [{key: t[key] for key in ('status', 'owner', 'deadline')} for t in data['tasks']]
        index = next(i for i, t in enumerate(data['tasks']) if t['owner'] == '지연')
        edits[index].update(status='완료', owner='수빈', deadline='10월 5일')
        reviewed = review_tasks(data, edits)
        self.assertEqual(reviewed[index]['evidence'], data['tasks'][index]['evidence'])
        self.assertEqual(data, before)
        draft = make_draft(data, edits, '수빈')
        self.assertIn('담당 수빈', draft)
        self.assertIn('기한 10월 5일', draft)
        self.assertIn('지연은 API 작업 중', draft)
        self.assertIn('사용자 수정', draft)
        self.assertIn('담당자, 기한 확인 필요', draft)
        self.assertNotIn('DB 테이블 생성 |', draft)
        self.assertNotIn('로그인 API | 완료', draft.split('이어서 할 일')[1].split('관련 자료')[0])
        edits[index]['evidence'] = []
        with self.assertRaises(HandoverError): review_tasks(data, edits)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def post(self, path, body, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        request = urllib.request.Request(self.url + path, json.dumps(body).encode(), headers, method='POST')
        try:
            with urllib.request.urlopen(request) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as response: return response.code, json.load(response)

    def test_static_and_conversation(self):
        for path in ('/', '/app.js', '/style.css', '/api/config'):
            with urllib.request.urlopen(self.url + path) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
        status, data = self.post('/api/start', {'request': '팀플 정리해줘'})
        self.assertEqual(status, 200); self.assertTrue(data['needsInput'])

    def test_blank_failure_unknown_and_corrected_draft(self):
        self.assertEqual(self.post('/api/analyze', {'text': '', 'mode': 'local'})[0], 422)
        with patch.dict(os.environ, {'TEAM_HANDOVER_API_URL': '', 'TEAM_HANDOVER_MODEL': ''}):
            status, failed = self.post('/api/analyze', {'text': SAMPLE, 'mode': 'ai'})
        self.assertEqual(status, 422); self.assertIn('AI 설정 필요', failed['error'])
        status, result = self.post('/api/analyze', {'text': SAMPLE, 'mode': 'local', 'sourceName': '회의록 A'})
        self.assertEqual(status, 200)
        edits = [{key: t[key] for key in ('status', 'owner', 'deadline')} for t in result['data']['tasks']]
        edits[0].update(owner='수빈', status='진행 중')
        status, draft = self.post('/api/handover', {'analysisId': result['analysisId'], 'edits': edits})
        self.assertEqual(status, 200); self.assertIn('담당 수빈', draft['draft'])
        self.assertIn('회의록 A', draft['draft']); self.assertIn('확인 필요', draft['draft'])
        self.assertEqual(self.post('/api/analyze', {'text': SAMPLE, 'mode': 'local'}, 'https://evil.example')[0], 403)

    def test_explicit_fixture_adapter(self):
        server = create_server(0, ai_call=lambda _: json.dumps(NORMAL))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/analyze', json.dumps({'text': SAMPLE, 'mode': 'ai'}).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                data = json.load(response)['data']
            self.assertEqual(data['analysisSource'], 'injected-provider')
            self.assertEqual(data['suggestions'][0]['kind'], 'AI 제안')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
