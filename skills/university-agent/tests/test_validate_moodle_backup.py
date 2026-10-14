import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_moodle_backup import validate_backup


class MoodleBackupValidationTests(unittest.TestCase):
    def test_validates_required_course_backup_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "course.mbz"
            with tarfile.open(path, "w:gz") as archive:
                for name, data in {
                    "moodle_backup.xml": b"<moodle_backup />",
                    "course/course.xml": b"<course><fullname>Test</fullname></course>",
                    "files.xml": b"<files />",
                    "sections/section_1/section.xml": b"<section />",
                    "activities/quiz_1/module.xml": b"<module />",
                }.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            result = validate_backup(path)
        self.assertEqual(result["sectionCount"], 1)
        self.assertEqual(result["activityCount"], 1)
        self.assertTrue(result["courseNamePresent"])

    def test_rejects_parent_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.mbz"
            with tarfile.open(path, "w:gz") as archive:
                info = tarfile.TarInfo("../outside.xml")
                info.size = 0
                archive.addfile(info, io.BytesIO())
            with self.assertRaises(ValueError):
                validate_backup(path)


if __name__ == "__main__":
    unittest.main()
