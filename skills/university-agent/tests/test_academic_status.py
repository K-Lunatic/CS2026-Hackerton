"""Deadline and conversation regressions using an isolated database."""
from datetime import datetime
from unittest.mock import patch

from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
from features.assignments import assignment_answer, get_assignments
from features.context import format_current_context, get_current_context
from features.context_commands import detect_context_intent
from features.deadlines import SEOUL, deadline
from features.guidance import guidance_request, usage_guide
import run_agent


class AcademicStatusTests(ProjectTestBase):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 1, 23, 30, tzinfo=SEOUL)
        self.db = LocalDatabase(self.db_path)
        self.addCleanup(self.db.close)
        data = snapshot()
        base = data['assignments'][0]
        dates = {
            'old': '2026-09-21T00:00:00+09:00',
            'monday': '2026-09-28T00:00:00+09:00',
            'today': '2026-10-01',
            'sunday': '2026-10-04T23:59:00+09:00',
            'next-week': '2026-10-04T15:00:00Z',
            'bad-date': 'not a date',
        }
        data['assignments'] = [dict(base, id=key, dueAt=value) for key, value in dates.items()]
        data['assignments'] += [dict(base, id=status, submissionStatus=status) for status in ('SUBMITTED', 'LATE', 'UNKNOWN')]
        data['lectures'][0].update(watchProgress=1.0, watchedSeconds=1, availableUntil='2026-10-01T14:00:00+09:00')
        upsert(self.db, 'fixture-user', data)
        self.db.add_manual_assignment('fixture-user', '마감 없는 직접 등록 과제')

    def test_week_boundaries_upcoming_and_date_precision(self):
        weekly = get_assignments(self.db, 'fixture-user', this_week=True, now=self.now)
        self.assertEqual([item['id'] for item in weekly], ['monday', 'today', 'sunday'])
        future = get_assignments(self.db, 'fixture-user', upcoming=True, now=self.now)
        self.assertEqual([item['id'] for item in future], ['today', 'sunday', 'next-week'])
        past = get_assignments(self.db, 'fixture-user', overdue=True, now=self.now)
        self.assertEqual([item['id'] for item in past], ['old', 'monday'])
        self.assertEqual(future[0]['dueAt'], '2026-10-01')
        self.assertIsNone(deadline('2026-10-01T12:00:00'))
        answer = assignment_answer(self.db, 'fixture-user', '이번 주 미제출 과제 알려줘', now=self.now)
        self.assertIn('2026-09-28~2026-10-04', answer['answer'])
        self.assertIn('마감 확인 필요: 2개', answer['answer'])
        self.assertNotIn('100%', answer['answer'])
        next_monday = self.now.replace(day=5, hour=0, minute=0)
        self.assertEqual([i['id'] for i in get_assignments(self.db, 'fixture-user', upcoming=True, now=next_monday)], ['next-week'])

    def test_context_distinguishes_unknown_overdue_and_one_percent(self):
        data = get_current_context(self.db, 'fixture-user', lambda: [], now=self.now)
        self.assertEqual(len(data['overdueAssignments']), 2)
        self.assertEqual(len(data['upcomingAssignments']), 3)
        self.assertEqual(len(data['undatedAssignments']), 2)
        self.assertEqual(len(data['unknownSubmissionAssignments']), 1)
        self.assertTrue(data['lastSyncedAt'])
        answer = format_current_context(data)
        self.assertIn('시청률 1% · 미완료', answer)
        self.assertNotIn('100%', answer)
        self.assertIn('실시간 TLS 조회 결과가 아닙니다', answer)
        self.assertIn('기한 지남: 테스트 과목 / 테스트 강의', answer)
        todo_ids = [i['id'] for course in self.db.get_todos('fixture-user') for i in course['items']]
        self.assertNotIn('LATE', todo_ids)
        self.assertIn('UNKNOWN', todo_ids)

    def test_conversation_routing_and_checkpoint_guards(self):
        with patch.object(run_agent, 'database', return_value=self.db), patch.object(run_agent, 'USER_ID', 'fixture-user'):
            result = run_agent.ask('나.. 지금은 어때?')
            self.assertEqual(result['toolCalls'], ['get_current_context'])
            self.assertIn('시청률 1%', result['answer'])
            for text in ('이번 주 미제출 과제 알려줘', '앞으로 해야 하는 과제 알려줘'):
                self.assertEqual(run_agent.ask(text)['toolCalls'], ['get_unsubmitted_assignments'])
        self.assertEqual(detect_context_intent('지금까지 진행 상황 저장해줘'), 'save')
        self.assertEqual(detect_context_intent('과제 어디까지 했지?'), 'load')
        self.assertEqual(detect_context_intent('팀플 체크포인트 불러줘'), 'load')
        self.assertIsNone(detect_context_intent('팀플 진행 상황 알려줘'))
        self.assertEqual(self.cli('ask', '--text', '나.. 지금은 어때?')['toolCalls'], ['get_current_context'])

    def test_beginner_guidance_is_read_only_and_routes_examples(self):
        before = self.db.connection.total_changes
        with patch.object(run_agent, 'database', side_effect=AssertionError('Help must not open the database')):
            for text in ('처음인데 어떻게 써?', '뭘 할 수 있어?', '과제 기능 알려줘', '강의 사용법', '할 일 추가해줘', '과제 지워줘', '도와줘'):
                result = run_agent.ask(text)
                self.assertTrue(result['needsInput'])
                self.assertEqual(result['toolCalls'], [])
                self.assertNotIn('--records', result['answer'])
                self.assertNotIn('assignment-add', result['answer'])
        self.assertEqual(guidance_request('할 일 추가해줘')['data']['requiredFields'], ['title'])
        with patch.object(run_agent, 'database', return_value=self.db), patch.object(run_agent, 'USER_ID', 'fixture-user'):
            for text in ('오늘 뭐 해야 해?', '할 일 알려줘', '지금 내 상태 어때?'):
                self.assertEqual(run_agent.ask(text)['toolCalls'], ['get_current_context'])
            for text in usage_guide()['data']['suggestions']:
                result = run_agent.ask(text)
                self.assertTrue(result['toolCalls'] or result.get('needsInput'))
        self.assertEqual(self.db.connection.total_changes, before)
