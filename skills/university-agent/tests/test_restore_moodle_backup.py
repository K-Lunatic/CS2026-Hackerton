import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.restore_moodle_backup import _WizardFormParser, _multipart, _restore_page, _wizard_fields


RESTORE_PAGE = '''
<body class="context-1465280">
<form action="/backup/restorefile.php" method="post">
<input type="hidden" name="contextid" value="1465280">
<input type="hidden" name="sesskey" value="hidden-value">
<input type="hidden" name="_qf__course_restore_form" value="restore">
<input type="hidden" name="backupfile" value="987">
<input name="submitbutton" value="복구" type="submit">
</form>
<script>M.form_filepicker.init(Y, {"itemid":"987","client_id":"client","repositories":{"3":{"type":"upload","name":"파일 첨부"}}});</script>
</body>
'''


class FakeSession:
    def __init__(self):
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        return '<body class="context-1465280"></body>'

    def get_page(self, path):
        self.paths.append(path)
        return RESTORE_PAGE, 'https://tls.kku.ac.kr' + path


class RestoreBackupTests(unittest.TestCase):
    def test_restore_page_reads_upload_repository_without_exposing_values(self):
        page, context_id, hidden, meta = _restore_page(FakeSession(), '65513')
        self.assertEqual(context_id, '1465280')
        self.assertEqual(meta['repoId'], '3')
        self.assertEqual(meta['itemId'], '987')
        self.assertEqual(hidden['backupfile'], '987')
        self.assertNotIn('hidden-value', str(meta))

    def test_multipart_contains_fields_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clean.mbz'
            path.write_bytes(b'backup-bytes')
            payload, content_type = _multipart({'action': 'upload'}, 'repo_upload_file', path)
        self.assertIn(b'name="action"', payload)
        self.assertIn(b'name="repo_upload_file"', payload)
        self.assertIn(b'backup-bytes', payload)
        self.assertIn('multipart/form-data; boundary=', content_type)

    def test_wizard_fields_keep_checked_choice_and_hidden_values(self):
        parser = _WizardFormParser()
        parser.feed('''<form action="/backup/restore.php"><input type="hidden" name="keep" value="0">
            <input type="checkbox" name="keep" value="1" checked><input type="submit" value="다음"></form>''')
        self.assertEqual(_wizard_fields(parser.forms[0]), {'keep': '1'})


if __name__ == '__main__':
    unittest.main()
