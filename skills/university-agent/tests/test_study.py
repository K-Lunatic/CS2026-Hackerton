"""Offline study-flow checks. Generated text is an explicit fixture, never a real AI result."""
from pathlib import Path
import json
import sqlite3
import unittest

from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
from features import analysis_records
from features.concept_insights import build_insights
from features.study import StudySession, study_intent, event_from_text


class StudyFlowTests(ProjectTestBase):
    def setUp(self):
        super().setUp()
        self.files = self.db_path.parent / 'files'
        self.file = self.files / 'course-1' / 'w3.txt'
        self.file.parent.mkdir(parents=True)
        self.file.write_text('이진 탐색은 정렬된 배열에서 탐색한다.\n반복마다 탐색 범위를 절반으로 줄인다.\n', encoding='utf-8')
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data['courses'][0]['name'] = '자료구조'
        data['resources'] = [dict(data['resources'][0], title='3주차 탐색', fileName='w3.txt', extension='txt', localPath=str(self.file))]
        upsert(db, 'fixture-user', data)
        db.close()
        self.addCleanup(lambda: None)

    def session(self, conversation='a', user='fixture-user'):
        db = LocalDatabase(self.db_path, read_only=True)
        self.addCleanup(db.close)
        return StudySession(self.db_path.parent / 'study-sessions.db', user, conversation, db, self.files)

    @staticmethod
    def question(kind='mcq', index=1):
        q = {'id': f'q{index}', 'type': kind, 'question': '자료의 핵심 개념은 무엇인가?',
             'options': ['정렬된 배열에서 탐색', '정렬 없이 탐색', '선형 검색', '무작위 검색'] if kind == 'mcq' else [],
             'answer': '정렬된 배열에서 탐색', 'explanation': '자료에 명시되어 있습니다.',
             'concept': '이진 탐색', 'hint': '탐색 전에 배열 조건을 생각해보세요.',
             'rubric': ['정렬 조건'],
             'evidence': [{'resourceId': 'resource-1', 'location': '줄 1', 'quote': '정렬된 배열에서 탐색한다.'}]}
        if kind == 'short': q['acceptedAnswers'] = ['정렬된 배열에서 탐색', '정렬된 리스트를 탐색']
        return q

    def test_intent_offer_direct_request_and_unrelated_short_reply(self):
        self.assertTrue(self.cli('ask', '--text', '지난 시험 문제 보여줘', '--conversation', 'history-request')['needsExamHistory'])
        self.assertTrue(self.cli('ask', '--text', '지난 시험 문제 다시 풀고 싶어', '--conversation', 'shuffle-request')['needsExamShuffle'])
        self.assertTrue(self.cli('ask', '--text', '개념 매칭으로 복습하고 싶어', '--conversation', 'match-request')['needsConceptMatch'])
        self.assertTrue(self.cli('ask', '--text', '문제 세트를 하나씩 골라서 풀고 싶어', '--conversation', 'sets-request')['needsExamSets'])
        self.assertTrue(self.cli('ask', '--text', '개념부터 차근차근 공부해서 수준에 맞춰 문제 내줘', '--conversation', 'adaptive-request')['needsAdaptiveStudy'])
        self.assertTrue(self.cli('ask', '--text', '강의 원본 파일 보내줘')['needsOriginalFile'])
        self.assertEqual(study_intent('운영체제 시험 준비해야 하는데'), 'request')
        self.assertEqual(event_from_text('운영체제 시험 준비해야 하는데', [])['settings']['mode'], 'concepts')
        self.assertEqual(event_from_text('객관식 문제 만들어줘', [])['settings']['types'], ['mcq'] * 10)
        self.assertEqual(event_from_text('모든 유형을 섞어서 문제 만들어줘', [])['settings']['types'],
                         ['mcq', 'short', 'essay', 'code_fix', 'code_output', 'ordering', 'mcq', 'short', 'essay', 'code_fix'])
        self.assertEqual(event_from_text('50분 시간 제한으로 문제 만들어줘', [])['settings']['timeLimitMinutes'], 50)
        self.assertEqual(study_intent('자료구조 객관식 5문제 만들어줘'), 'request')
        self.assertEqual(study_intent('자료구조 주요 내용 학습시켜줘'), 'request')
        self.assertEqual(study_intent('중간고사는 손코딩일까?'), None)
        self.assertEqual(study_intent('중간고사는 손코딩이라고 하셨어'), 'save_context_note')
        self.assertIsNone(study_intent('수업자료 목록 보여줘'))
        s = self.session()
        offer = s.call({'action': 'offer', 'selection': {'courseId': 'course-1'}})
        self.assertEqual(offer['status'], 'offered')
        self.assertNotIn('question', str(offer))
        with self.assertRaisesRegex(ValueError, '직전 학습 제안'):
            s.call({'action': 'accept', 'replyTo': '다른 질문'})
        self.assertFalse(s.call({'action': 'offer'})['offered'])
        s.call({'action': 'observe'})
        self.assertEqual(s.call({'action': 'offer'})['status'], 'idle')
        other = self.session('other')
        with self.assertRaises(ValueError): other.call({'action': 'accept', 'replyTo': offer['offerId']})

    def test_cli_routes_study_without_changing_academic_lookup(self):
        lookup = self.cli('ask', '--text', '수업자료 목록 보여줘')
        self.assertEqual(lookup['toolCalls'], ['get_resources'])
        prepared = self.cli('ask', '--text', '자료구조 공부 좀 해야겠다', '--conversation', 'cli-study')
        self.assertEqual(prepared['status'], 'prepared')
        self.assertEqual(prepared['hostOnly']['settings']['mode'], 'concepts')
        self.assertEqual(prepared['hostOnly']['SOURCE'][0]['courseId'], 'course-1')
        direct = self.cli('ask', '--text', '자료구조 3주차 자료로 객관식 5문제 만들어줘', '--conversation', 'direct-study')
        self.assertEqual(direct['status'], 'prepared')
        self.assertEqual(direct['hostOnly']['settings']['types'], ['mcq'] * 5)
        self.assertEqual(direct['hostOnly']['SOURCE'][0]['name'], '3주차 탐색')
        unrelated = self.cli('ask', '--text', '과제 알려줘', '--conversation', 'unrelated-study')
        self.assertNotEqual(unrelated['toolCalls'], ['study'])
        old_offer = self.session('unrelated-study').call({'action': 'offer'})
        self.cli('ask', '--text', '과제 알려줘', '--conversation', 'unrelated-study')
        with self.assertRaises(ValueError):
            self.session('unrelated-study').call({'action': 'accept', 'replyTo': old_offer['offerId']})

    def test_exam_context_note_is_detected_saved_separately_and_reused(self):
        text = '자료구조 중간고사는 AI 이슈 때문에 손코딩을 시킨다고 하셨어. 간단하게 작성할 수 있고 50분 주신다고 하셨어.'
        self.assertEqual(study_intent(text), 'save_context_note')
        parsed = event_from_text(text, [{'id': 'course-1', 'name': '자료구조'}])
        self.assertEqual(parsed['action'], 'save_context_note')
        self.assertEqual(parsed['note']['lessonKey'], None)
        self.assertEqual(parsed['selection']['courseId'], 'course-1')
        session = self.session('context-note')
        saved = session.call(parsed)
        self.assertEqual(saved['status'], 'context_saved')
        self.assertIn('정보를 저장했어요', saved['answer'])
        self.assertEqual(saved['note']['tags'], ['hand_coding', 'time_limit_50m', 'short_answer'])
        db = sqlite3.connect(self.db_path.parent / 'study-sessions.db')
        rows = db.execute('SELECT course_id,lesson_key,exam_type,note_text FROM study_context_notes').fetchall()
        self.assertEqual(rows, [('course-1', None, 'midterm', text)])
        db.close()
        listed = session.call({'action': 'list_context_notes', 'selection': {'courseId': 'course-1'}})
        self.assertEqual(listed['status'], 'context_notes')
        self.assertEqual(listed['notes'][0]['note'], text)
        prepared = self.session('uses-context').call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertEqual(prepared['hostOnly']['studyContext'][0]['tags'], ['hand_coding', 'time_limit_50m', 'short_answer'])
        self.assertEqual(prepared['hostOnly']['studyContext'][0]['note'], text)

    def test_context_note_can_be_limited_to_a_lesson(self):
        session = self.session('lesson-note')
        saved = session.call({'action': 'save_context_note', 'selection': {'courseId': 'course-1'},
            'note': {'text': '3주차는 연결 리스트 삽입 과정을 중심으로 복습한다.', 'lessonKey': '3주차', 'examType': 'course'}})
        self.assertEqual(saved['status'], 'context_saved')
        matching = self.session('lesson-match').call({'action': 'request', 'selection': {
            'courseId': 'course-1', 'resourceName': '3주차 탐색'}})
        self.assertEqual(len(matching['hostOnly']['studyContext']), 1)
        other = self.session('lesson-other').call({'action': 'request', 'selection': {
            'courseId': 'course-1', 'resourceName': '1주차'}})
        self.assertEqual(other['status'], 'selecting')

    def test_saved_concept_priority_is_available_to_question_generation(self):
        source = {'resourceId': 'resource-1', 'courseId': 'course-1', 'name': '3주차 탐색',
                  'location': '줄 1', 'quote': '이진 탐색은 정렬된 배열에서 탐색한다.'}
        analysis_records.save(self.db_path.parent, 'fixture-user', [source], {}, 'concepts', {
            'concepts': [{'concept': '이진 탐색', 'explanation': '정렬된 배열을 절반씩 좁혀 탐색합니다.',
                          'evidence': [source]}]
        })
        self.assertEqual(build_insights(self.db_path.parent, 'fixture-user', snapshot()['resources'], course_ids={'course-1'})['concepts'][0]['concept'], '이진 탐색')
        prepared = self.session('learning-focus').call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertIn('hostOnly', prepared, prepared)
        self.assertEqual(prepared['hostOnly']['learningFocus'][0]['concept'], '이진 탐색')

    def test_cli_exposes_saved_concept_priorities(self):
        source = {'resourceId': 'resource-1', 'courseId': 'course-1', 'name': '3주차 탐색',
                  'location': '줄 1', 'quote': '이진 탐색은 정렬된 배열에서 탐색한다.'}
        analysis_records.save(self.db_path.parent, 'fixture-user', [source], {}, 'concepts', {
            'concepts': [{'concept': '이진 탐색', 'explanation': '정렬된 배열을 절반씩 좁혀 탐색합니다.',
                          'evidence': [source]}]
        })
        result = self.cli('study-insights', '--course', '자료구조')
        self.assertEqual(result['toolCalls'], ['get_concept_insights'])
        self.assertEqual(result['data']['concepts'][0]['concept'], '이진 탐색')

    def test_context_note_requires_a_real_course_and_does_not_need_conversation(self):
        self.assertEqual(event_from_text('손코딩 시험 방식 저장해줘', [])["action"], 'save_context_note')
        result = self.cli('ask', '--text', '자료구조 중간고사는 손코딩이고 50분이라고 하셨어')
        self.assertEqual(result['status'], 'context_saved')
        self.assertNotIn('study_sessions', result.get('data', {}))

    def test_new_exams_force_web_even_if_chat_requested(self):
        from features.study import settings
        for delivery in ('batch', 'single', 'web'):
            self.assertEqual(settings({'delivery': delivery})['delivery'], 'web')
            s = self.session(delivery)
            prepared = s.call({'action': 'request', 'selection': {'courseId': 'course-1'},
                              'settings': {'count': 1, 'delivery': delivery}})
            self.assertTrue(prepared['needsExtraction'])
            with self.assertRaises(ValueError):
                s.call({'action': 'generate', 'requestId': prepared['requestId'],
                        'data': {'questions': [self.question()]}})
        parsed = event_from_text('자료구조 한 문제씩 내줘', [])
        self.assertEqual(parsed['settings']['delivery'], 'web')

    def test_wrong_course_scope_missing_source_and_invalid_generation(self):
        s = self.session()
        response = s.call({'action': 'request', 'selection': {'courseId': 'missing'}})
        self.assertEqual(response['status'], 'selecting')
        prepared = s.call({'action': 'select', 'selection': {'courseId': 'course-1', 'resourceIds': ['resource-1'], 'locations': {'resource-1': ['줄 2']}}})
        self.assertEqual([x['location'] for x in prepared['hostOnly']['SOURCE']], ['줄 2'])
        with self.assertRaises(ValueError):
            s.call({'action': 'generate', 'requestId': prepared['requestId'], 'data': {'questions': [self.question()], 'shortageReason': '부족'}})
        s.call({'action': 'request', 'selection': {'courseId': 'course-1', 'resourceIds': ['resource-1'], 'locations': {'resource-1': ['줄 9']}}})
        self.assertEqual(s.call({'action': 'status'})['status'], 'assembling')
        with self.assertRaises(ValueError):
            s.call({'action': 'select', 'selection': {'courseId': 'course-1', 'resourceIds': ['foreign-id']}})

    def test_missing_and_prohibited_content_never_prepares(self):
        self.file.unlink()
        s = self.session()
        result = s.call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertEqual(result['status'], 'assembling')
        self.assertIn('로컬 파일이 없습니다', result['failures'][0]['reason'])
        self.assertEqual(result['totalCandidates'], 0)
        with self.assertRaises(ValueError): s.call({'action': 'generate', 'requestId': 'fake', 'data': {}})
        other = self.session('b', 'another-user')
        self.assertEqual(other.call({'action': 'request'})['status'], 'selecting')

    def test_prohibited_school_file_is_not_opened_even_when_present(self):
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data['courses'][0]['name'] = '자료구조'
        data['resources'] = [dict(data['resources'][0], title='금지 자료', fileName='w3.txt',
                                  extension='txt', localPath=str(self.file), downloadStatus='PROHIBITED')]
        upsert(db, 'fixture-user', data)
        db.close()
        result = self.session().call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertEqual(result['status'], 'assembling')
        self.assertEqual(result['totalCandidates'], 0)

    def test_viewer_page_failure_is_shown_without_claiming_readable_slides(self):
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data['courses'][0]['name'] = '컴퓨터구조'
        data['resources'] = [dict(data['resources'][0], title='1장 강의슬라이드 파일', fileName='view.php', extension='', mimeType='text/html', localPath=None, downloadStatus='PROHIBITED', downloadReason='TLS가 실제 파일 대신 문서 뷰어 페이지를 반환해 본문을 가져오지 않았습니다.')]
        upsert(db, 'fixture-user', data)
        db.close()
        result = self.session().call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertIn('자료를 보는 화면', result['failures'][0]['reason'])
        self.assertNotIn('다운로드 금지', result['failures'][0]['reason'])
        self.assertEqual(result['totalCandidates'], 0)

    def test_revoked_source_cannot_resume_from_in_progress_chunks(self):
        session = self.session('withdrawn')
        prepared = session.call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertTrue(prepared['needsExtraction'])
        db = LocalDatabase(self.db_path)
        db.connection.execute("UPDATE resources SET download_status='PROHIBITED'")
        db.connection.commit()
        db.close()
        for event in ({'action': 'status'}, {'action': 'extract', 'requestId': prepared['requestId'], 'data': {}}):
            with self.subTest(event=event['action']), self.assertRaisesRegex(ValueError, '자료 이용 상태'):
                session.call(event)
        session.call({'action': 'cancel'})
        result = session.call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertEqual(result['totalCandidates'], 0)

    def test_insufficient_evidence_never_fills_the_requested_count(self):
        s = self.session()
        prepared = s.call({'action': 'request', 'selection': {'courseId': 'course-1'},
                          'settings': {'count': 5}})
        s.call({'action': 'extract', 'requestId': prepared['requestId'], 'data': {
            'context': '탐색 수업', 'learning': [{'concept': '탐색', 'explanation': '정렬 조건',
            'evidence': self.question()['evidence']}], 'types': [], 'questions': [],
            'shortageReason': '출제 근거 부족'}})
        result = s.call({'action': 'assemble', 'candidateIds': [],
                         'shortageReason': '출제 근거 부족'})
        self.assertEqual(result['status'], 'insufficient')
        self.assertNotIn('question', result)

    def test_concept_followup_is_cleared_by_topic_change(self):
        s = self.session()
        p = s.call({'action': 'request', 'selection': {'courseId': 'course-1'}, 'settings': {'mode': 'concepts'}})
        c = s.call({'action': 'generate', 'requestId': p['requestId'], 'data': {'concepts': [{
            'concept': '탐색', 'explanation': '자료 설명', 'evidence': self.question()['evidence']}]}})
        s.call({'action': 'observe'})
        with self.assertRaises(ValueError): s.call({'action': 'accept', 'replyTo': c['offerId']})

    def test_attachment_concepts_and_cancel(self):
        attachment = self.db_path.parent / 'attachments' / 'uploaded.txt'
        attachment.parent.mkdir()
        attachment.write_text('운영체제는 자원을 관리한다.\n', encoding='utf-8')
        s = self.session()
        result = s.call({'action': 'request', 'selection': {'attachmentPath': str(attachment)}, 'settings': {'mode': 'concepts'}})
        self.assertEqual(result['status'], 'prepared')
        source = result['hostOnly']['SOURCE'][0]
        generated = s.call({'action': 'generate', 'requestId': result['requestId'], 'data': {'concepts': [{
            'concept': '자원 관리', 'explanation': '자료 설명', 'evidence': [{'resourceId': source['resourceId'],
            'location': source['location'], 'quote': '운영체제는 자원을 관리한다.'}]}]}})
        self.assertEqual(generated['status'], 'concepts')
        reused = self.session('new-chat').call({'action': 'request', 'selection': {'attachmentPath': str(attachment)}, 'settings': {'mode': 'concepts'}})
        self.assertTrue(reused['reusedAnalysis'])
        self.assertEqual(reused['concepts'], generated['concepts'])
        self.assertEqual(s.call({'action': 'cancel'})['status'], 'idle')
        self.assertFalse(s.call({'action': 'offer'})['offered'])

if __name__ == '__main__': unittest.main()
