import sys
from pathlib import Path
import tempfile
import unittest
import json
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import analysis_records, material_cache


class AnalysisRecordsTest(unittest.TestCase):
    def test_retry_idempotent_but_different_payload_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [{'resourceId': 'r', 'courseId': 'c', 'name': '자료', 'location': '1', 'text': '본문'}]
            first = analysis_records.save(directory, 'u', sources, {}, 'concepts', {'concepts': []}, operation_id='request')
            self.assertEqual(first, analysis_records.save(directory, 'u', sources, {}, 'concepts', {'concepts': []}, operation_id='request'))
            with self.assertRaises(ValueError):
                analysis_records.save(directory, 'u', sources, {}, 'concepts', {'concepts': ['changed']}, operation_id='request')
            self.assertNotEqual(first, analysis_records.save(directory, 'u', sources, {}, 'concepts', {'concepts': []}, operation_id='rebuild'))
    def test_source_stored_once_and_history_uses_index(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [{'resourceId': 'r', 'courseId': 'c', 'name': '자료', 'location': '1', 'text': 'ONLY_ONCE_TEXT'}]
            identifier = analysis_records.save(directory, 'u', sources, {}, 'exam_analysis',
                {'units': [{'id': 'u1', 'sources': sources}], 'candidates': [], 'candidateCount': 0})
            with sqlite3.connect(Path(directory) / 'analysis-records.db') as db:
                value = db.execute('SELECT value FROM analyses').fetchone()[0]
                self.assertEqual(value.count('ONLY_ONCE_TEXT'), 1)
                plan = db.execute('EXPLAIN QUERY PLAN SELECT metadata FROM analyses WHERE user=? AND (resource IN (?) OR resource LIKE ?) ORDER BY created DESC,id DESC LIMIT 20', ('u', 'r', 'attachment-%')).fetchall()
                self.assertNotIn('TEMP B-TREE', str(plan))
                # Earlier records remain readable, without rewriting their payload.
                old = json.loads(value)
                old['schemaVersion'] = 1
                old['data']['units'][0]['sources'] = sources
                db.execute('UPDATE analyses SET value=?', (json.dumps(old),))
            self.assertEqual(analysis_records.read(directory, 'u', [{'id': 'r'}], identifier)['sources'], sources)

    def test_fingerprint_reuses_and_detects_same_size_change(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.txt'
            path.write_text('one')
            digest = material_cache.fingerprint(directory, path)
            with patch('pathlib.Path.open', side_effect=AssertionError('unchanged file read')):
                self.assertEqual(material_cache.fingerprint(directory, path), digest)
            timestamp = path.stat().st_mtime_ns
            path.write_text('two')
            if os.name != 'nt':  # Windows ctime is creation time, not the POSIX change-time signal.
                os.utime(path, ns=(timestamp, timestamp))
            self.assertNotEqual(material_cache.fingerprint(directory, path), digest)

    def test_revisions_private_readonly_and_restrictions(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [{'resourceId': 'r1', 'courseId': 'c1', 'name': '배열',
                        'location': 'p1', 'text': '배열은 연속된 공간이다.'}]
            resources = [{'id': 'r1', 'downloadStatus': 'DOWNLOADED'}]
            ids = [analysis_records.save(directory, 'alice', sources, {'difficulty': '보통'},
                'exam_analysis', {'units': [{'learning': ['배열']}],
                                  'candidates': [{'answer': '비공개'}], 'candidateCount': 1}) for _ in range(2)]
            self.assertNotEqual(*ids)
            listing = analysis_records.read(directory, 'alice', resources, limit=1)
            self.assertEqual(listing['nextOffset'], 1)
            self.assertEqual(len(analysis_records.read(directory, 'alice', resources)['records']), 2)
            record = analysis_records.read(directory, 'alice', resources, ids[0])
            self.assertEqual(record['sources'], sources)
            self.assertNotIn('candidates', record['data'])
            self.assertEqual(record['validation']['semanticAccuracy'], 'not_verified')
            for user, access in [('bob', resources), ('alice', []),
                                 ('alice', [{'id': 'r1', 'downloadStatus': 'PROHIBITED'}])]:
                self.assertEqual(analysis_records.read(directory, user, access)['records'], [])
                with self.assertRaises(ValueError):
                    analysis_records.read(directory, user, access, ids[0])
            with self.assertRaises(ValueError):
                analysis_records.read(directory, 'alice', resources, limit=200)


if __name__ == '__main__':
    unittest.main()
