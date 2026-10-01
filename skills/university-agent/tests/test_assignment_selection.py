"""Name-based checkpoint UX with isolated fixtures and no real account access."""
import json
from unittest.mock import patch

from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
from features.assignment_selection import find_assignments, find_similar_tls_assignments, selection_guidance
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

    def test_short_save_request_searches_without_writing(self):
        result = self.cli('ask', '--text', '과제 저장', '--assignment-query', 'Ex06')
        self.assertEqual([item['title'] for item in result['data']['candidates']],
                         ['(과제) Ex06-Customer.java'])
        self.assertFalse(result['data']['performed'])
        self.assertEqual(self.checkpoints(), [])
        no_clue = self.cli('ask', '--text', '과제 저장')
        self.assertTrue(no_clue['needsAssignmentQuery'])

    def test_exercise_number_does_not_expand_to_other_exercises(self):
        for query in ('Ex06', 'Ex06 Java 과제', 'Ex06 Unique.java 중복 제거', 'ex6'):
            with self.subTest(query=query):
                items = find_similar_tls_assignments(self.db, 'fixture-user', query)
                self.assertEqual([item['id'] for item in items], ['private-ex06'])
        self.assertEqual(find_similar_tls_assignments(self.db, 'fixture-user', 'Ex99 Java 과제'), [])

    def test_filename_does_not_match_unrelated_generic_array_task(self):
        self.db.connection.execute('UPDATE assignments SET title=? WHERE id=?',
                                   ('연습과제 - 배열, 구조체, 포인터', 'private-cpp'))
        self.db.connection.commit()
        self.assertEqual(find_similar_tls_assignments(
            self.db, 'fixture-user', 'Unique.java 중복 제거 HashSet 배열 List'), [])

    def test_multiple_problem_summaries_remain_separate_after_selection(self):
        # The host owns chat interpretation; exercise the summaries it supplies
        # through search/refinement/final-command/save/load, never a real DB.
        postfix = dict(progress='Postfix.java 입력 처리와 숫자 스택 저장 작성',
                       blocker='대화에서 확인되지 않음', nextAction='AI 제안: 연산자 처리 구현')
        unique = dict(progress='Unique.java 배열 선언과 HashSet 중복 제거까지 작성',
                      completedItems=['배열 출력', 'HashSet 변환'],
                      blocker='대화에서 확인되지 않음', nextAction='AI 제안: 배열 재변환과 결과 출력')
        self.cli('ask', '--text', 'save "Ex05"', '--checkpoint-json', json.dumps(postfix))
        self.cli('ask', '--text', '과제 저장', '--assignment-query', 'Unique.java')
        selected = self.cli('assignment-find', '--query', 'Ex06', '--source', 'tls')
        command = selected['data']['candidates'][0]['command']
        saved = self.cli('ask', '--text', command, '--checkpoint-json', json.dumps(unique))
        self.assertEqual(saved['data']['assignmentTitle'], '(과제) Ex06-Customer.java')
        self.assertEqual(saved['data']['progress'], unique['progress'])
        self.assertEqual(self.cli('ask', '--text', 'load "Ex05"')['data']['progress'], postfix['progress'])
        self.assertEqual(self.cli('ask', '--text', 'load "Ex06"')['data']['progress'], unique['progress'])
        self.assertEqual(len(self.checkpoints()), 2)

    def test_similar_tls_search_shows_all_title_and_description_matches(self):
        self.db.connection.execute("UPDATE assignments SET description=? WHERE id=?", ('ExceptionAssignment.java 예외 처리', 'private-ex05'))
        self.db.connection.execute("UPDATE assignments SET title=? WHERE id=?", ('(과제) Ex09-ExceptionAssignment.java', 'private-ex06'))
        self.db.connection.commit()
        result = self.cli('assignment-find', '--query', 'ExceptionAssignment.java', '--source', 'tls')
        self.assertEqual([item['title'] for item in result['data']['candidates']],
                         ['(과제) Ex09-ExceptionAssignment.java', '(과제) Ex05-ClubMember.java'])
        self.assertTrue(result['needsInput'])
        self.assertEqual(result['nextCommands'], [item['command'] for item in result['data']['candidates']])
        self.assert_public(result)

    def test_no_similar_tls_match_asks_which_route_applies(self):
        result = self.cli('assignment-find', '--query', 'CompletelyUnrelated', '--source', 'tls')
        self.assertEqual(result['data']['candidates'], [])
        self.assertEqual(result['data']['stage'], 'not_found')
        self.assertIn('단어를 하나 더', result['answer'])
        self.assertIn('새 이름:', result['answer'])
        self.assertIn('학교 과제 목록에 없는 과제인가요', result['answer'])
        self.assertIn('TLS에 표시된 제목이 다른가요', result['answer'])
        self.assertNotIn('save new', result['nextCommands'])
        self.assertEqual(result['nextCommands'], [])
        self.assertEqual(self.checkpoints(), [])

    def test_save_search_refinement_and_fenced_command_round_trip(self):
        title = '연습과제 - 배열, 구조체, 포인터'
        self.db.connection.execute('UPDATE assignments SET title=? WHERE id=?', (title, 'private-ex05'))
        self.db.connection.commit()
        missing = self.cli('ask', '--text', '과제 저장해줘', '--assignment-query', '복소수')
        self.assertEqual(missing['data']['stage'], 'not_found')
        found = self.cli('assignment-find', '--query', '구조체 배열', '--source', 'tls')
        self.assertEqual([item['title'] for item in found['data']['candidates']], [title])
        self.assertEqual(found['data']['dataSource'], 'local')
        self.assertTrue(found['data']['lastSyncedAt'])
        self.assertEqual(self.checkpoints(), [])
        command = found['data']['candidates'][0]['command']
        saved = self.cli('ask', '--text', '```text\n' + command + '\n```', '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], title)
        loaded = self.cli('ask', '--text', '```\nload "' + title + '"\n```')
        self.assertEqual(loaded['data'], saved['data'])
        self.assert_public(found)

    def test_natural_search_never_saves_even_with_summary(self):
        result = self.cli('ask', '--text', '과제 저장해줘', '--assignment-query', '자바', '--checkpoint-json', self.payload)
        self.assertEqual(len(result['data']['candidates']), 2)
        self.assertFalse(result['data']['performed'])
        self.assertEqual(self.checkpoints(), [])
        refined = self.cli('assignment-find', '--query', '자바 Ex06', '--source', 'tls')
        self.assertEqual(refined['data']['candidates'][0]['title'], '(과제) Ex06-Customer.java')
        # Ranking is only a suggestion; even a high-ranked match needs selection.
        self.assertEqual(self.checkpoints(), [])

    def test_custom_title_path_after_empty_search_reuses_task(self):
        missing = self.cli('ask', '--text', '과제 저장해줘', '--assignment-query', '복소수')
        self.assertEqual(missing['data']['stage'], 'not_found')
        command = 'save new "복소수 구조체 과제"'
        preview = self.cli('ask', '--text', command)
        self.assertTrue(preview['needsSummary'])
        self.assertFalse(any(item['source'] == 'manual' for item in self.db.get_assignments('fixture-user')))
        saved = self.cli('ask', '--text', command, '--checkpoint-json', self.payload)
        self.cli('ask', '--text', command, '--checkpoint-json', self.payload)
        self.assertEqual(len([item for item in self.db.get_assignments('fixture-user') if item['source'] == 'manual']), 1)
        self.assertEqual(self.cli('ask', '--text', 'load "복소수 구조체 과제"')['data']['progress'], saved['data']['progress'])

    def test_completed_candidate_explains_recovery_before_save(self):
        self.db.connection.execute("UPDATE assignment_submissions SET submission_status='SUBMITTED' WHERE assignment_id='private-ex05'")
        self.db.connection.commit()
        found = self.cli('assignment-find', '--query', 'ClubMember', '--source', 'tls')
        self.assertEqual(found['data']['stage'], 'completed')
        self.assertIsNone(found['data']['candidates'][0]['command'])
        self.assertEqual(found['nextCommands'], [])
        self.assertIn('새로고침', found['answer'])
        blocked = self.cli('ask', '--text', 'save "Ex05"', '--checkpoint-json', self.payload)
        self.assertEqual(blocked['data']['stage'], 'completed')
        self.assertEqual(self.checkpoints(), [])
        saved = self.cli('ask', '--text', 'save new "복소수 복습"', '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], '복소수 복습')
        self.assertEqual(next(item for item in self.db.get_assignments('fixture-user') if item['id'] == 'private-ex05')['submissionStatus'], 'SUBMITTED')

    def test_cpp_search_works_and_exact_title_command_selects_report(self):
        self.db.connection.execute('UPDATE assignments SET title=? WHERE id=?', ('보고서 초안', 'private-ex06'))
        self.db.connection.commit()
        found = self.cli('assignment-find', '--query', 'C++', '--source', 'tls')
        self.assertEqual([item['title'] for item in found['data']['candidates']], ['보고서'])
        self.assertEqual(found['data']['candidates'][0]['command'], 'save "보고서"')
        exact = self.cli('ask', '--text', 'save "보고서"', '--checkpoint-json', self.payload)
        self.assertEqual(exact['data']['assignmentTitle'], '보고서')
        saved = self.cli('ask', '--text', found['data']['candidates'][0]['command'], '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], '보고서')

    def test_wrapped_prose_multiple_commands_and_empty_titles_never_save(self):
        for text in ('save ""', '```\nsave "Ex05"\nload\n```', '참고\n```\nsave "Ex05"\n```',
                     '```\nsave "Ex05"\n```\n추가 설명', 'save this chat'):
            with self.subTest(text=text):
                self.cli('ask', '--text', text, '--checkpoint-json', self.payload)
        self.assertEqual(self.checkpoints(), [])

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
            self.assertTrue(candidate['command'].startswith('save "'))
            self.assertNotIn('--course', candidate['command'])
            saved = self.cli('ask', '--text', candidate['command'], '--checkpoint-json', self.payload)
            self.assertEqual(saved['data']['assignmentTitle'], candidate['title'])
        self.assertEqual(len(self.checkpoints()), 2)

    def test_single_tls_match_offers_title_only_command_without_saving(self):
        title = '연습과제 - 배열, 구조체, 포인터'
        self.db.connection.execute('UPDATE assignments SET title=? WHERE id=?', (title, 'private-ex05'))
        self.db.connection.commit()
        result = self.cli('assignment-find', '--query', '구조체 배열', '--source', 'tls')
        command = f'save "{title}"'
        self.assertEqual([item['command'] for item in result['data']['candidates']], [command])
        self.assertIn(command, result['answer'])
        self.assertEqual(result['nextCommands'], [command])
        for option in ('--course', '--title', '--due'):
            self.assertNotIn(option, result['answer'])
        self.assertEqual(self.checkpoints(), [])
        saved = self.cli('ask', '--text', command, '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], title)

    def test_title_only_tls_command_prefers_exact_title_over_longer_match(self):
        self.db.connection.execute('UPDATE assignments SET title=? WHERE id=?', ('실습 보고서', 'private-ex05'))
        self.db.connection.execute('UPDATE assignments SET title=? WHERE id=?', ('실습 보고서 초안', 'private-ex06'))
        self.db.connection.commit()
        result = self.cli('assignment-find', '--query', '실습 보고서', '--source', 'tls')
        candidates = [item for item in result['data']['candidates'] if item['title'].startswith('실습 보고서')]
        self.assertEqual(len(candidates), 2)
        for candidate in candidates:
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
            self.assertIn('관련 과제를 TLS에서 찾지 못했습니다.', result['answer'])
            self.assertIn('TLS에 표시된 제목이 다른가요', result['answer'])
            self.assertIn('목록에 없다면 저장할 제목을 정해 주세요.', result['answer'])
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

    def test_screenshot_assignment_save_list_and_complete(self):
        title = '캡처 문제 풀이'
        command = '과제 저장 --새과제 "' + title + '"'
        proposed = self.cli('ask', '--text', command)
        self.assertTrue(proposed['needsSummary'])
        self.assertEqual(self.cli('ask', '--text', '과제 목록 불러오기')['data'], [])
        saved = self.cli('ask', '--text', command, '--checkpoint-json', self.payload)
        self.assertEqual(saved['data']['assignmentTitle'], title)
        listed = self.cli('ask', '--text', '과제 목록 불러오기')
        self.assertEqual(len(listed['data']), 1)
        self.assertEqual(listed['data'][0]['assignmentTitle'], title)
        self.assertEqual(self.cli('ask', '--text', '과제 불러오기 캡처 문제')['data'], saved['data'])
        manual = next(item for item in self.db.get_assignments('fixture-user') if item['title'] == title)
        self.assertTrue(self.cli('assignment-complete', '--id', manual['id'], '--submission-answer', '예')['data']['completed'])
        self.assertEqual(self.cli('ask', '--text', '과제 목록 불러오기')['data'], [])
        self.assertEqual(self.cli('ask', '--text', '과제 불러오기 캡처 문제')['data'], None)
        self.assertEqual(self.checkpoints(), [])

    def test_list_paraphrases_never_read_records(self):
        for phrase in ('과제 목록 불러와', '과제 목록 불러오기 ', '저장한 과제 목록 보여줘', '저장한 과제 전부 보여줘', '과제 불러오기 목록'):
            with patch.object(run_agent, 'list_unfinished_context_bookmarks', side_effect=AssertionError('must not read')):
                result = run_agent.ask(phrase)
            self.assertFalse(result['data']['performed'])
            self.assertIn('list', result['answer'])
        self.assertEqual(parse_context_command('과제 목록 불러오기')['operation'], 'list')

    def test_english_commands_distinguish_tls_and_manual_assignments(self):
        self.db.add_manual_assignment('fixture-user', '(과제) Ex05-ClubMember.java')
        saved_tls = self.cli('ask', '--text', 'save "Ex05"', '--checkpoint-json', self.payload)
        self.assertEqual(saved_tls['data']['assignmentTitle'], '(과제) Ex05-ClubMember.java')
        self.assertEqual(self.checkpoints()[0]['assignment_id'], 'private-ex05')
        self.assertIn('list', saved_tls['nextCommands'])

        no_tls_match = self.cli('ask', '--text', 'save "개인 실습"', '--checkpoint-json', self.payload)
        self.assertFalse(no_tls_match['data']['performed'])
        self.assertEqual(len(self.checkpoints()), 1)

        saved_manual = self.cli('ask', '--text', 'save new "개인 실습"', '--checkpoint-json', self.payload)
        self.assertEqual(saved_manual['data']['assignmentTitle'], '개인 실습')
        manual_count = len([item for item in self.db.get_assignments('fixture-user') if item['source'] == 'manual'])
        self.cli('ask', '--text', 'save new "개인 실습"', '--checkpoint-json', self.payload)
        self.assertEqual(len([item for item in self.db.get_assignments('fixture-user') if item['source'] == 'manual']), manual_count)
        self.assertEqual(self.cli('ask', '--text', 'load "개인 실습"')['data']['progress'], '자료 정리 완료')

        listed = self.cli('ask', '--text', 'list')
        self.assertEqual(len(listed['data']), 2)
        self.assertIn('load "개인 실습"', listed['nextCommands'])
        self.assertIn('다음 명령:', listed['answer'])

    def test_new_commands_keep_paraphrases_read_only(self):
        self.assertEqual(parse_context_command('save "new"')['values'], {'query': 'new', 'source': 'tls'})
        for message in ('과제 진행 저장해줘', '저장한 과제 목록 보여줘', 'postfix 불러와줘', 'list '):
            result = self.cli('ask', '--text', message)
            self.assertFalse(result['data']['performed'])
            self.assertIn('nextCommands', result)
        self.assertEqual(self.checkpoints(), [])

    def test_english_save_accepts_unquoted_multiword_tls_title(self):
        command = 'save 연습과제 - 배열, 구조체, 포인터'
        parsed = parse_context_command(command)
        self.assertEqual(parsed['values'], {'query': '연습과제 - 배열, 구조체, 포인터', 'source': 'tls'})

        self.db.connection.execute("UPDATE assignments SET title=? WHERE id=?", ('연습과제 - 배열, 구조체, 포인터', 'private-ex05'))
        self.db.connection.commit()
        for text in (command, 'save "연습과제 - 배열, 구조체, 포인터"', '```\nsave "연습과제 - 배열, 구조체, 포인터"\n```'):
            saved = self.cli('ask', '--text', text, '--checkpoint-json', self.payload)
            self.assertEqual(saved['data']['assignmentTitle'], '연습과제 - 배열, 구조체, 포인터')
        self.assertEqual(self.checkpoints()[0]['assignment_id'], 'private-ex05')

    def test_list_shows_every_unfinished_assignment_once(self):
        self.cli('ask', '--text', '과제 저장 자바 Ex05', '--checkpoint-json', self.payload)
        self.cli('ask', '--text', '과제 저장 자바 Ex05', '--checkpoint-json', json.dumps(dict(progress='수정 완료', blocker='없음', nextAction='AI 제안: 검토')))
        self.cli('ask', '--text', '과제 저장 자바 Ex06', '--checkpoint-json', self.payload)
        listed = self.cli('ask', '--text', '과제 목록 불러오기')['data']
        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0]['assignmentTitle'], '(과제) Ex06-Customer.java')
        self.assertEqual(listed[1]['progress'], '수정 완료')

    def test_tls_submission_removes_saved_checkpoint(self):
        self.cli('ask', '--text', '과제 저장 자바 Ex05', '--checkpoint-json', self.payload)
        self.assertEqual(len(self.checkpoints()), 1)
        data = snapshot()
        data['courses'] = [{'id': 'course-java', 'name': 'Java프로그래밍2 (8234)', 'source': 'tls'}]
        data['assignments'] = [dict(data['assignments'][0], id='private-ex05', courseId='course-java', title='(과제) Ex05-ClubMember.java', submissionStatus='SUBMITTED')]
        data['lectures'] = data['notices'] = data['resources'] = []
        upsert(self.db, 'fixture-user', data)
        self.assertEqual(self.checkpoints(), [])
        self.assertEqual(self.cli('ask', '--text', '과제 목록 불러오기')['data'], [])

    def test_manual_completion_requires_exact_submission_answer(self):
        self.cli('ask', '--text', 'save new "캡처 과제"', '--checkpoint-json', self.payload)
        manual = next(item for item in self.db.get_assignments('fixture-user') if item['title'] == '캡처 과제')
        command = ('assignment-complete', '--id', manual['id'])

        for answer in (None, '아니요', '응', '예 ', 'YES'):
            result = self.cli(*command, *(() if answer is None else ('--submission-answer', answer)))
            self.assertFalse(result['data']['completed'])
            self.assertEqual(len(self.checkpoints()), 1)
            self.assertEqual(len(self.cli('ask', '--text', 'list')['data']), 1)
        self.assertFalse(self.db.complete_manual_assignment('fixture-user', manual['id'], submission_answer='응'))
        self.assertEqual(len(self.checkpoints()), 1)

        confirmed = self.cli(*command, '--submission-answer', '예')
        self.assertTrue(confirmed['data']['completed'])
        self.assertEqual(self.checkpoints(), [])
        self.assertEqual(self.cli('ask', '--text', 'list')['data'], [])

    def test_tls_checkpoint_is_kept_until_sync_confirms_submission(self):
        self.cli('ask', '--text', 'save "Ex05"', '--checkpoint-json', self.payload)
        data = snapshot()
        data['courses'] = [{'id': 'course-java', 'name': 'Java프로그래밍2 (8234)', 'source': 'tls'}]
        data['lectures'] = data['notices'] = data['resources'] = []
        for status in ('NOT_SUBMITTED', 'UNKNOWN', 'LATE'):
            data['assignments'] = [dict(data['assignments'][0], id='private-ex05', courseId='course-java',
                                        title='(과제) Ex05-ClubMember.java', submissionStatus=status)]
            upsert(self.db, 'fixture-user', data)
            self.assertEqual(len(self.checkpoints()), 0 if status == 'LATE' else 1)
