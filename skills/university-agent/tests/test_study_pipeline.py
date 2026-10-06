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
            pipeline = json.loads(db.execute('SELECT state FROM study_pipelines WHERE user=? AND exam_id=?',
                ('fixture-user', saved['analysisRef'])).fetchone()[0])
        self.assertNotIn('pipeline', saved)
        self.assertEqual(len(pipeline['units']), 4)

    def test_catalog_reads_do_not_rewrite_large_session(self):
        prepared = self.request()
        for _ in range(4):
            prepared = self.extract_questions(self.session('pipeline'), prepared, [self.candidate(prepared)])
        path = self.db_path.parent / 'study-sessions.db'
        with sqlite3.connect(path) as observer:
            before = observer.execute('PRAGMA data_version').fetchone()[0]
            original = observer.execute('SELECT state FROM study_sessions').fetchone()[0]
            self.session('pipeline').call({'action': 'catalog'})
            self.session('pipeline').call({'action': 'unit', 'unitId': 'u1'})
            self.session('pipeline').call({'action': 'candidate', 'candidateId': 'c1'})
            self.assertEqual(observer.execute('PRAGMA data_version').fetchone()[0], before)
            self.assertEqual(observer.execute('SELECT state FROM study_sessions').fetchone()[0], original)

    def test_completed_files_reused_across_chats_and_changed_or_forbidden_files_not_reused(self):
        prepared = self.request()
        for _ in range(4):
            prepared = self.extract_questions(self.session('pipeline'), prepared, [self.candidate(prepared)])
        request = {'action': 'request', 'selection': {'courseId': 'course-1', 'allFiles': True},
                   'settings': {'count': 2, 'delivery': 'web'}}
        with patch('features.study_materials._read_sections', side_effect=AssertionError('must reuse analysis')), \
             patch('features.study_pipeline.load_chunks', side_effect=AssertionError('must not split again')), \
             patch('pathlib.Path.open', side_effect=AssertionError('must not read unchanged originals')):
            reused = self.session('second').call(request)
        self.assertEqual(reused['reusedFiles'], 4)
        self.assertEqual(reused['totalCandidates'], 4)
        from features import analysis_records
        db = LocalDatabase(self.db_path)
        try:
            records = analysis_records.read(self.files.parent, 'fixture-user', db.get_resources('fixture-user'))
        finally:
            db.close()
        self.assertEqual(len(records['records']), 4)  # Reuse does not invent new analysis revisions.
        self.assertEqual({r['resourceId'] for r in records['records']}, {'r0', 'r1', 'r2', 'r3'})
        invalid_range = self.session('empty-range').call({**request,
            'selection': {'courseId': 'course-1', 'resourceIds': ['r0'], 'locations': {'r0': []}}})
        self.assertEqual(len(invalid_range['failures']), 1)
        self.assertEqual(invalid_range['reusedFiles'], 0)
        (self.files / '0.txt').write_text('자료를 새 내용으로 수정했습니다.\n', encoding='utf-8')
        changed = self.session('third').call(request)
        self.assertTrue(changed['needsExtraction'])
        self.assertIn('새 내용', changed['hostOnly']['SOURCE'][0]['text'])
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE resources SET download_status='PROHIBITED', download_reason='다운로드 금지' WHERE id='r0'")
        forbidden = self.session('fourth').call(request)
        self.assertEqual(forbidden['reusedFiles'], 3)
        self.assertEqual(len(forbidden['failures']), 1)

    def test_chunk_boundaries_preserved_without_suffix_copy(self):
        from features.study_pipeline import load_chunks
        text = '가나다' * 10000
        state = {'pipeline': {'units': [], 'candidates': []}, 'settings': {'choices': 4, 'difficulty': '보통', 'types': ['auto']},
                 'selection': {'courseId': 'c'}}
        load_chunks(state, {'id': 'r', 'title': '자료', 'sections': [{'location': '1', 'text': text}, {'location': '2', 'text': '끝'}]})
        chunks = state['pipeline']['chunks']
        self.assertEqual(''.join(s['text'] for part in chunks for s in part), text + '끝')
        self.assertTrue(all(sum(len(s['text']) for s in part) <= CHUNK_CHARS for part in chunks))
        self.assertEqual(chunks[-1][-1]['location'], '2')

    def test_legacy_text_analysis_is_invalidated_after_decoder_change(self):
        from features import material_cache
        from features.study_materials import study_materials
        p = self.request()
        for _ in range(4):
            p = self.extract_questions(self.session('pipeline'), p, [self.candidate(p)])
        session = self.session('pipeline')
        with sqlite3.connect(self.files.parent / 'study-sessions.db') as db:
            state = json.loads(db.execute('SELECT state FROM study_sessions').fetchone()[0])
        material = study_materials(session.provider.get_courses('fixture-user'), session.provider.get_resources('fixture-user'),
            files_root=self.files, course_id='course-1', resource_ids=['r3'], include_ids=True, max_chars=10000000)['data']['materials'][0]
        config = {k: state['settings'][k] for k in ('choices', 'difficulty')}
        config['types'] = sorted(set(state['settings']['types']))
        old_key = material_cache.key(['analysis-v1', CHUNK_CHARS, 'course-1',
            {k: v for k, v in material.items() if k != 'contentKey'}, config])
        root = self.files.parent / 'cache'
        value = material_cache.get(root, 'analysis', 'fixture-user', state['pipeline']['analysisKey'])
        material_cache.put(root, 'analysis', 'fixture-user', old_key, value)
        with sqlite3.connect(root / 'materials.db') as db:
            db.execute('DELETE FROM material_cache WHERE kind=? AND key=?', ('analysis', state['pipeline']['analysisKey']))
        reused = self.session('upgrade').call({'action': 'request', 'selection': {'courseId': 'course-1', 'allFiles': True},
            'settings': {'count': 4, 'delivery': 'web'}})
        self.assertTrue(reused['needsExtraction'])
        self.assertEqual(reused['progress']['processedFiles'], 3)

    def test_failed_cache_save_retry_does_not_duplicate_analysis(self):
        from features import analysis_records
        session = self.session('retry')
        prepared = session.call({'action': 'request', 'selection': {'courseId': 'course-1', 'resourceIds': ['r0']}, 'settings': {'count': 1}})
        question = self.candidate(prepared)
        with patch('features.material_cache.put', side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):
                self.extract_questions(session, prepared, [question])
        self.extract_questions(session, prepared, [question])
        records = analysis_records.read(self.files.parent, 'fixture-user', session.provider.get_resources('fixture-user'))
        self.assertEqual(len(records['records']), 1)

    def test_old_completed_pipeline_migrates_once_and_draft_is_small(self):
        p = self.request()
        for _ in range(4):
            p = self.extract_questions(self.session('pipeline'), p, [self.candidate(p)])
        session = self.session('pipeline')
        session.call({'action': 'assemble', 'candidateIds': ['c1', 'c2', 'c3', 'c4']})
        path = self.files.parent / 'study-sessions.db'
        with sqlite3.connect(path) as db:
            state = json.loads(db.execute('SELECT state FROM study_sessions').fetchone()[0])
            old = json.loads(db.execute('SELECT state FROM study_pipelines').fetchone()[0])
            old['largeOriginal'] = 'x' * 1000000
            state['pipeline'] = old
            db.execute('UPDATE study_sessions SET state=?', (json.dumps(state),))
        session.call({'action': 'draft', 'examId': state['examId'], 'revision': 0, 'answers': {}})
        with sqlite3.connect(path) as db:
            small = db.execute('SELECT state FROM study_sessions').fetchone()[0]
            preserved = db.execute('SELECT state FROM study_pipelines').fetchone()[0]
            self.assertLess(len(small), 50000)
            self.assertIn('largeOriginal', preserved)
            version = db.execute('PRAGMA data_version').fetchone()[0]
            session.call({'action': 'web_status'})
            self.assertEqual(db.execute('PRAGMA data_version').fetchone()[0], version)
            session.call({'action': 'draft', 'examId': state['examId'], 'revision': 1, 'answers': {'q1': '1'}})
            self.assertEqual(db.execute('SELECT state FROM study_pipelines').fetchone()[0], preserved)

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
