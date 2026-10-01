"""Offline staged extraction, bounded context and persisted resume checks."""
import json
import sqlite3
from unittest.mock import patch
import test_exam_web
from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
from features.study_pipeline import CHUNK_CHARS


class FilePipelineTests(ProjectTestBase):
    session = test_exam_web.ExamWebTests.session
    question = staticmethod(test_exam_web.ExamWebTests.question)
    extract_questions = test_exam_web.ExamWebTests.extract_questions

    def setUp(self):
        super().setUp()
        self.files = self.db_path.parent / 'files'
        self.files.mkdir()
        data = snapshot()
        data['resources'] = []
        for index in range(4):
            path = self.files / f'{index}.txt'
            path.write_text('이진 탐색은 정렬된 배열에서 탐색한다.\n', encoding='utf-8')
            data['resources'].append(dict(snapshot()['resources'][0], id=f'r{index}',
                localPath=str(path), fileName=path.name, extension='txt'))
        db = LocalDatabase(self.db_path)
        upsert(db, 'fixture-user', data)
        db.close()

    def request(self):
        return self.session('pipeline').call({'action': 'request',
            'selection': {'courseId': 'course-1', 'allFiles': True},
            'settings': {'count': 4, 'delivery': 'web'}})

    def candidate(self, prepared):
        q = self.question()
        source = prepared['hostOnly']['SOURCE'][0]
        q['evidence'] = [{'resourceId': source['resourceId'], 'location': source['location'],
                          'quote': source['text'][:30]}]
        return q

    def test_all_files_must_finish_resume_and_private_question_bank(self):
        prepared = self.request()
        for index in range(4):
            session = self.session('pipeline')
            with self.assertRaises(ValueError):
                session.call({'action': 'assemble', 'candidateIds': []})
            with self.assertRaises(ValueError):
                session.call({'action': 'generate', 'requestId': prepared['requestId'], 'data': {'questions': []}})
            self.assertEqual(prepared['progress']['processedFiles'], index)
            original = prepared['requestId']
            q = self.candidate(prepared)
            prepared = self.extract_questions(session, prepared, [q])
            with self.assertRaises(ValueError):
                session.call({'action': 'extract', 'requestId': original, 'data': {}})
            prepared = self.session('pipeline').call({'action': 'status'})
        self.assertEqual(prepared['totalCandidates'], 4)
        page = session.call({'action': 'catalog', 'limit': 1})
        self.assertEqual(len(page['hostOnly']['candidates']), 1)
        self.assertEqual(page['nextOffset'], 1)
        unit = session.call({'action': 'unit', 'unitId': 'u1'})['hostOnly']
        self.assertEqual(unit['context'], '정렬된 배열에서 이진 탐색하는 수업')
        self.assertEqual(unit['types'][0]['type'], 'mcq')
        session.call({'action': 'assemble', 'candidateIds': ['c1', 'c2', 'c3', 'c4']})
        public = session.call({'action': 'web_status'})
        self.assertEqual(len(public['questions']), 4)
        self.assertNotIn('rubric', json.dumps(public))
        with sqlite3.connect(self.db_path.parent / 'study-sessions.db') as db:
            saved = json.loads(db.execute('SELECT state FROM study_sessions').fetchone()[0])
        self.assertEqual(len(saved['pipeline']['units']), 4)

    def test_long_file_chunk_bound_and_bad_evidence_does_not_advance(self):
        (self.files / '0.txt').write_text('정렬 조건 ' * 4000, encoding='utf-8')
        p = self.request()
        self.assertGreater(p['progress']['totalParts'], 1)
        total = p['progress']['totalParts']
        for part in range(total):
            self.assertLessEqual(sum(len(s['text']) for s in p['hostOnly']['SOURCE']), CHUNK_CHARS)
            q = self.candidate(p)
            q['evidence'][0]['quote'] = '원문에 없는 인용'
            with self.assertRaises(ValueError):
                self.extract_questions(self.session('pipeline'), p, [q])
            resumed = self.session('pipeline').call({'action': 'status'})
            self.assertEqual(resumed['requestId'], p['requestId'])
            p = self.extract_questions(self.session('pipeline'), p, [self.candidate(p)])
        self.assertEqual(p['progress']['processedFiles'], 1)

    def test_prohibited_file_never_opened_and_exclusion_requires_consent(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE resources SET download_status='PROHIBITED', download_reason='다운로드 금지' WHERE id='r0'")
        with patch('features.study_materials.Path.read_text', side_effect=AssertionError('must not read forbidden file')):
            # Metadata restriction is checked before the text reader is called.
            from features.study_materials import study_materials
            db = LocalDatabase(self.db_path, read_only=True)
            try:
                result = study_materials(db.get_courses('fixture-user'), db.get_resources('fixture-user'),
                    files_root=self.files, course_id='course-1', resource_ids=['r0'])
                self.assertIn('금지', result['data']['materials'][0]['error'])
            finally:
                db.close()
        p = self.request()
        self.assertEqual(len(p['failures']), 1)
        for _ in range(3):
            p = self.extract_questions(self.session('pipeline'), p, [self.candidate(p)])
        with self.assertRaisesRegex(ValueError, '동의'):
            self.session('pipeline').call({'action': 'assemble', 'candidateIds': ['c1']})
        self.session('pipeline').call({'action': 'assemble', 'candidateIds': ['c1'],
            'acceptExclusions': True, 'shortageReason': '다운로드 금지 자료 제외'})
