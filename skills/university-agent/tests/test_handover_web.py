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
