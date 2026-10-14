import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tls_backup_download import _BackupLinkParser, _CourseContextParser, _looks_like_moodle_backup


class TLSBackupDownloadTests(unittest.TestCase):
    def test_context_id_is_read_from_course_body_class(self):
        parser = _CourseContextParser()
        parser.feed('<body class="two-column context-1465280"><main>course</main></body>')
        self.assertEqual(parser.context_id, "1465280")

    def test_parser_only_returns_visible_matching_course_backup(self):
        parser = _BackupLinkParser("65513")
        parser.feed(
            '<table><tr><td>backup-moodle2-course-65513-demo-20261014-nu.mbz</td>'
            '<td><a href="/pluginfile.php/1/user/backup/demo.mbz?forcedownload=1">다운로드</a></td></tr>'
            '<tr><td>backup-moodle2-course-7-other.mbz</td>'
            '<td><a href="/pluginfile.php/2/user/backup/other.mbz?forcedownload=1">다운로드</a></td></tr></table>'
        )
        self.assertEqual(len(parser.links), 1)
        self.assertEqual(parser.links[0][1], "backup-moodle2-course-65513-demo-20261014-nu.mbz")

    def test_accepts_moodle_archive_signatures(self):
        self.assertTrue(_looks_like_moodle_backup(b"PK\x03\x04..."))
        self.assertTrue(_looks_like_moodle_backup(b"\x1f\x8b\x08..."))
        self.assertTrue(_looks_like_moodle_backup(b".ARCHIVE..."))
        self.assertFalse(_looks_like_moodle_backup(b"<html>"))


if __name__ == "__main__":
    unittest.main()
