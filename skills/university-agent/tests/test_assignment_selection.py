"""Name-based checkpoint UX with isolated fixtures and no real account access."""
import json
from unittest.mock import patch

from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
from features.assignment_selection import find_assignments, selection_guidance
from features.context_commands import parse_context_command
import run_agent


class AssignmentSelectionTests(ProjectTestBase):
    def setUp(self):
        super().setUp()
        self.db = LocalDatabase(self.db_path)
        self.addCleanup(self.db.close)
        data = snapshot()
        data['courses'] = [
            {'id': 'course-java', 'name': 'Java프로그래밍2 (8234)', 'source': 'tls'},
            {'id': 'course-cpp', 'name': 'C++프로그래밍', 'source': 'tls'},
        ]
        base = data['assignments'][0]
        data['assignments'] = [
            dict(base, id='private-ex05', courseId='course-java', title='(과제) Ex05-ClubMember.java', dueAt='2026-10-08'),
            dict(base, id='private-ex06', courseId='course-java', title='(과제) Ex06-Customer.java', dueAt='2026-10-15'),
            dict(base, id='private-cpp', courseId='course-cpp', title='보고서', dueAt=''),
        ]
        data['lectures'] = data['notices'] = data['resources'] = []
        upsert(self.db, 'fixture-user', data)
        self.payload = json.dumps(dict(progress='자료 정리 완료', blocker='대화에서 확인되지 않음', nextAction='AI 제안: 초안 작성'))

    def checkpoints(self):
        return self.db.connection.execute('SELECT assignment_id FROM context_bookmarks ORDER BY id').fetchall()

    def assert_public(self, result):
        serialized = json.dumps(result, ensure_ascii=False)
        for secret in ('private-ex05', 'private-ex06', 'private-cpp', 'course-java', 'course-cpp', 'assignmentId', 'courseId', '--과제ID'):
            self.assertNotIn(secret, serialized)

    def test_keyword_course_and_alias_matching(self):
        for query in ('자바 Ex05', 'JAVA clubmember', 'Ｊａｖａ Ex05', 'Java 프로그래밍 Ex05'):
            with self.subTest(query=query):
                found = find_assignments(self.db, 'fixture-user', {'query': query})
                self.assertEqual([item['id'] for item in found], ['private-ex05'])
        found = find_assignments(self.db, 'fixture-user', {'query': 'C++'})
        self.assertEqual([item['id'] for item in found], ['private-cpp'])

    def test_save_load_by_names_keep_identifiers_internal(self):
        saved = self.cli('ask', '--text', '과제 저장 자바 Ex05', '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], '(과제) Ex05-ClubMember.java')
        self.assertEqual(self.checkpoints()[0]['assignment_id'], 'private-ex05')
        loaded = self.cli('ask', '--text', '과제 불러오기 Ex05')
        self.assertEqual(loaded['data']['progress'], '자료 정리 완료')
        self.assertEqual(self.cli('ask', '--text', '과제 불러오기')['data'], loaded['data'])
        self.assert_public(saved)
        self.assert_public(loaded)

    def test_ambiguous_requests_do_not_read_or_write_checkpoints(self):
        for operation in ('저장', '불러오기'):
            result = self.cli('ask', '--text', '과제 ' + operation + ' 자바', '--checkpoint-json', self.payload)
            self.assertFalse(result['data']['performed'])
            self.assertEqual(len(result['data']['candidates']), 2)
            self.assert_public(result)
        with patch.object(run_agent, 'database', return_value=self.db), patch.object(run_agent, 'USER_ID', 'fixture-user'), patch.object(run_agent, 'get_context_bookmark', side_effect=AssertionError('must not load')):
            self.assertFalse(run_agent.ask('과제 불러오기 자바')['data']['performed'])
        self.assertEqual(self.checkpoints(), [])

    def test_candidate_commands_round_trip(self):
        result = self.cli('assignment-find', '--query', '자바')
        self.assert_public(result)
        for candidate in result['data']['candidates']:
            saved = self.cli('ask', '--text', candidate['command'], '--checkpoint-json', self.payload)
            self.assertEqual(saved['data']['assignmentTitle'], candidate['title'])
        self.assertEqual(len(self.checkpoints()), 2)

    def test_same_title_in_different_courses_and_missing_deadline_are_selectable(self):
        self.db.add_manual_assignment('fixture-user', '보고서', course_id='course-java')
        self.db.add_manual_assignment('fixture-user', '보고서', course_id='course-cpp', due_at='2026-11-01')
        result = self.cli('assignment-find', '--query', '보고서')
        for candidate in result['data']['candidates']:
            saved = self.cli('ask', '--text', candidate['command'], '--checkpoint-json', self.payload)
            self.assertEqual(saved['data']['courseName'], candidate['courseName'])
        self.assertEqual(len(self.checkpoints()), 3)

    def test_general_task_is_distinguished_from_course_assignment(self):
        self.db.add_manual_assignment('fixture-user', '보고서')
        result = self.cli('assignment-find', '--query', '보고서')
        self.assertEqual(len(result['data']['candidates']), 2)
        for candidate in result['data']['candidates']:
            saved = self.cli('ask', '--text', candidate['command'], '--checkpoint-json', self.payload)
            self.assertEqual(saved['data']['courseName'] or '기타 과제', candidate['courseName'])
        self.assertEqual(len(self.checkpoints()), 2)

    def test_exact_title_option_can_select_shorter_title(self):
        self.db.add_manual_assignment('fixture-user', '보고서 초안', course_id='course-cpp')
        result = self.cli('assignment-find', '--query', '보고서')
        self.assertEqual(len(result['data']['candidates']), 2)
        for candidate in result['data']['candidates']:
            saved = self.cli('ask', '--text', candidate['command'], '--checkpoint-json', self.payload)
            self.assertEqual(saved['data']['assignmentTitle'], candidate['title'])

    def test_no_match_does_not_select_another_assignment(self):
        for query in ('없는과제', '자바 보고서', '...', 'private-ex05'):
            result = self.cli('ask', '--text', '과제 저장 ' + query, '--checkpoint-json', self.payload)
            self.assertFalse(result['data']['performed'])
            self.assertEqual(result['data']['candidates'], [])
            self.assert_public(result)
        self.assertEqual(self.checkpoints(), [])

    def test_matching_respects_current_user_and_manual_assignments(self):
        self.db.add_manual_assignment('fixture-user', "발표 자료 '초안'")
        found = find_assignments(self.db, 'fixture-user', {'query': '발표 초안'})
        self.assertEqual(len(found), 1)
        result = selection_guidance(found)
        saved = self.cli('ask', '--text', result['data']['candidates'][0]['command'], '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], "발표 자료 '초안'")
        self.assertEqual(find_assignments(self.db, 'other-user', {'query': '자바'}), [])
        self.assertEqual(find_assignments(self.db, 'other-user', {'query': '발표'}), [])

    def test_duplicate_names_need_deadline_and_identical_duplicates_stay_ambiguous(self):
        self.db.add_manual_assignment('fixture-user', '보고서', course_id='course-cpp', due_at='2026-11-01')
        result = self.cli('ask', '--text', '과제 저장 C++ 보고서', '--checkpoint-json', self.payload)
        self.assertFalse(result['data']['performed'])
        saved = self.cli('ask', '--text', '과제 저장 --과목 C++ --과제 보고서 --마감 2026-11-01', '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], '보고서')
        self.db.add_manual_assignment('fixture-user', '보고서', course_id='course-cpp', due_at='2026-11-01')
        result = self.cli('ask', '--text', '과제 저장 --과목 C++ --과제 보고서 --마감 2026-11-01', '--checkpoint-json', self.payload)
        self.assertFalse(result['data']['performed'])
        self.assertEqual(len(self.checkpoints()), 1)

    def test_guidance_and_missing_summary_never_save(self):
        for text in ('그럼 지금까지 대화를 저장해줘', '과제 id는 어떻게 넣는데?', '과제 저장 --테스트 저장', '과제 저장', '과제 저장 자바 Ex05'):
            result = self.cli('ask', '--text', text)
            self.assert_public(result)
        self.assertEqual(self.checkpoints(), [])

    def test_selector_parser_rejects_mixed_empty_and_duplicate_options(self):
        for text in ('과제 저장 --과목', '과제 저장 --과제 ""', '과제 저장 자바 --과제 Ex05', '과제 저장 --과목 자바 --과목 C++', '과제 저장 --과제ID private-ex05 --과제 Ex05'):
            self.assertIn('error', parse_context_command(text))
        self.assertEqual(parse_context_command('과제 저장 자바 Ex05')['values'], {'query': '자바 Ex05'})
