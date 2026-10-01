"""Offline study-flow checks. Generated text is an explicit fixture, never a real AI result."""
from pathlib import Path
import json
import unittest

from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
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
        self.assertEqual(study_intent('운영체제 시험 준비해야 하는데'), 'offer')
        self.assertEqual(study_intent('자료구조 객관식 5문제 만들어줘'), 'request')
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
        offer = self.cli('ask', '--text', '자료구조 공부 좀 해야겠다', '--conversation', 'cli-study')
        self.assertEqual(offer['status'], 'offered')
        self.assertNotIn('question', offer)
        prepared = self.cli('study', '--conversation', 'cli-study', '--event-json', json.dumps({
            'action': 'accept', 'replyTo': offer['offerId']}, ensure_ascii=False))
        self.assertEqual(prepared['status'], 'prepared')
        self.assertEqual(prepared['hostOnly']['SOURCE'][0]['courseId'], 'course-1')
        direct = self.cli('ask', '--text', '자료구조 3주차 자료로 객관식 5문제 만들어줘', '--conversation', 'direct-study')
        self.assertEqual(direct['status'], 'prepared')
        self.assertEqual(direct['hostOnly']['settings']['types'], ['mcq'] * 5)
        self.assertEqual(direct['hostOnly']['SOURCE'][0]['name'], '3주차 탐색')
        unrelated = self.cli('ask', '--text', '과제 알려줘', '--conversation', 'unrelated-study')
        self.assertNotEqual(unrelated['toolCalls'], ['study'])
        old_offer = self.cli('ask', '--text', '자료구조 공부해야겠다', '--conversation', 'unrelated-study')
        self.cli('ask', '--text', '과제 알려줘', '--conversation', 'unrelated-study')
        with self.assertRaises(ValueError):
            self.session('unrelated-study').call({'action': 'accept', 'replyTo': old_offer['offerId']})

    def test_grounded_question_flow_and_no_early_answer(self):
        s = self.session()
        offer = s.call({'action': 'offer', 'selection': {'courseId': 'course-1'}})
        prepared = s.call({'action': 'accept', 'replyTo': offer['offerId']})
        self.assertEqual(prepared['status'], 'prepared')
        self.assertEqual(prepared['hostOnly']['settings']['types'], ['mcq', 'mcq', 'mcq', 'short', 'essay'])
        self.assertEqual(prepared['hostOnly']['SOURCE'][0]['location'], '줄 1')
        mocked = {'questions': [self.question(t, i) for i, t in enumerate(['mcq', 'mcq', 'mcq', 'short', 'essay'], 1)]}
        leaking = {'questions': [dict(q) for q in mocked['questions']]}
        leaking['questions'][0]['hint'] = '정답은 정렬된 배열에서 탐색입니다.'
        with self.assertRaisesRegex(ValueError, '힌트'):
            s.call({'action': 'generate', 'requestId': prepared['requestId'], 'data': leaking})
        result = s.call({'action': 'generate', 'requestId': prepared['requestId'], 'data': mocked})
        self.assertEqual(result['status'], 'question')
        for secret in ('자료에 명시되어 있습니다.', '정렬된 배열에서 탐색한다.'):
            self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
        self.assertNotIn('evidence', result['question'])
        self.assertNotIn('answer', result['question'])
        self.assertEqual(s.call({'action': 'hint', 'questionId': 'q1'})['hintUsed'], True)
        feedback = s.call({'action': 'answer', 'questionId': 'q1', 'text': '1'})
        self.assertEqual(feedback['outcome'], 'correct')
        self.assertEqual(feedback['evidence'][0]['name'], '3주차 탐색')
        self.assertEqual(s.call({'action': 'next'})['question']['id'], 'q2')
        self.assertEqual(s.call({'action': 'skip', 'questionId': 'q2'})['outcome'], 'skipped')
        s.call({'action': 'next'})
        self.assertEqual(s.call({'action': 'reveal', 'questionId': 'q3'})['outcome'], 'revealed')
        s.call({'action': 'next'})
        grade = s.call({'action': 'answer', 'questionId': 'q4', 'text': '정렬된 리스트를 탐색'})
        self.assertEqual(grade['status'], 'grading')
        with self.assertRaises(ValueError):
            s.call({'action': 'hint', 'questionId': 'q4'})
        short = s.call({'action': 'grade', 'questionId': 'q4', 'gradeId': grade['gradeId'],
                        'criteria': [{'criterion': '정렬 조건', 'met': True, 'feedback': '정렬 조건을 충족했습니다.'}]})
        self.assertEqual(short['outcome'], 'correct')
        s.call({'action': 'next'})
        essay = s.call({'action': 'answer', 'questionId': 'q5', 'text': '분할하여 탐색합니다'})
        final_feedback = s.call({'action': 'grade', 'questionId': 'q5', 'gradeId': essay['gradeId'],
                'criteria': [{'criterion': '정렬 조건', 'met': False, 'feedback': '정렬 조건을 빠뜨렸습니다.'}]})
        finished = final_feedback['summary']
        self.assertEqual(finished['counts']['correct'], 2)
        self.assertEqual(finished['selfCorrect'], 1)
        self.assertEqual(finished['counts']['skipped'], 1)
        self.assertEqual(finished['counts']['revealed'], 1)

    def test_wrong_course_scope_missing_source_and_invalid_generation(self):
        s = self.session()
        response = s.call({'action': 'request', 'selection': {'courseId': 'missing'}})
        self.assertEqual(response['status'], 'selecting')
        prepared = s.call({'action': 'select', 'selection': {'courseId': 'course-1', 'resourceIds': ['resource-1'], 'locations': {'resource-1': ['줄 2']}}})
        self.assertEqual([x['location'] for x in prepared['hostOnly']['SOURCE']], ['줄 2'])
        with self.assertRaises(ValueError):
            s.call({'action': 'generate', 'requestId': prepared['requestId'], 'data': {'questions': [self.question()], 'shortageReason': '부족'}})
        s.call({'action': 'request', 'selection': {'courseId': 'course-1', 'resourceIds': ['resource-1'], 'locations': {'resource-1': ['줄 9']}}})
        self.assertEqual(s.call({'action': 'status'})['status'], 'selecting')
        with self.assertRaises(ValueError):
            s.call({'action': 'select', 'selection': {'courseId': 'course-1', 'resourceIds': ['foreign-id']}})

    def test_missing_and_prohibited_content_never_prepares(self):
        self.file.unlink()
        s = self.session()
        result = s.call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertEqual(result['status'], 'selecting')
        self.assertIn('읽은 본문이 없어', result['answer'])
        self.assertNotIn('hostOnly', result)
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
        self.assertEqual(result['status'], 'selecting')
        self.assertNotIn('hostOnly', result)

    def test_viewer_page_failure_is_shown_without_claiming_readable_slides(self):
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data['courses'][0]['name'] = '컴퓨터구조'
        data['resources'] = [dict(data['resources'][0], title='1장 강의슬라이드 파일', fileName='view.php', extension='', mimeType='text/html', localPath=None, downloadStatus='PROHIBITED', downloadReason='TLS가 실제 파일 대신 문서 뷰어 페이지를 반환해 본문을 가져오지 않았습니다.')]
        upsert(db, 'fixture-user', data)
        db.close()
        result = self.session().call({'action': 'request', 'selection': {'courseId': 'course-1'}})
        self.assertIn('문서 뷰어 페이지', result['answer'])
        self.assertNotIn('hostOnly', result)

    def test_insufficient_evidence_never_fills_the_requested_count(self):
        s = self.session()
        prepared = s.call({'action': 'request', 'selection': {'courseId': 'course-1'},
                           'settings': {'count': 5}})
        self.assertEqual(prepared['status'], 'prepared')
        result = s.call({'action': 'generate', 'requestId': prepared['requestId'],
                         'data': {'questions': [], 'shortageReason': '출제 근거가 부족함'}})
        self.assertEqual(result['status'], 'insufficient')
        self.assertNotIn('question', result)

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
        self.assertEqual(s.call({'action': 'cancel'})['status'], 'idle')
        self.assertFalse(s.call({'action': 'offer'})['offered'])

if __name__ == '__main__': unittest.main()
