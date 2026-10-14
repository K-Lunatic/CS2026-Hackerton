import io
import sys
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rebuild_moodle_backup import rebuild_backup
from scripts.validate_moodle_backup import validate_backup


class RebuildMoodleBackupTests(unittest.TestCase):
    def make_template(self, path: Path) -> None:
        files = {
            "moodle_backup.xml": '<moodle_backup><information><contents><activities><activity><moduleid>30</moduleid></activity></activities><sections><section><sectionid>20</sectionid><title>old</title><directory>sections/section_20</directory></section></sections></contents><settings><setting><level>root</level><name>activities</name><value>1</value></setting><setting><level>activity</level><activity>quiz_30</activity><name>quiz_30_included</name><value>1</value></setting><setting><level>section</level><section>section_20</section><name>section_20_included</name><value>1</value></setting></settings></information></moodle_backup>'.encode(),
            "course/course.xml": '<course id="10"><fullname>2026년 2학기 자율강좌</fullname><numsections>1</numsections></course>'.encode(),
            "files.xml": b'<files><file id="1"/></files>',
            "questions.xml": '<question_categories><question_category><name>기존</name><questions><question><name>기존 문항</name></question></questions></question_category></question_categories>'.encode(),
            "sections/section_20/section.xml": '<section id="20"><number>1</number><name>기존</name><sequence>30</sequence></section>'.encode(),
            "sections/section_20/inforef.xml": b'<inforef/>',
            "activities/quiz_30/module.xml": b'<module id="30"/>',
            "files/1/blob": b'old file',
            "gradebook.xml": b'<gradebook><old/></gradebook>',
        }
        with tarfile.open(path, "w:gz") as archive:
            for name, payload in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

    def test_rebuild_removes_old_content_and_creates_subject_topics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template, output = root / "base.mbz", root / "clean.mbz"
            self.make_template(template)
            report = rebuild_backup(template, output, ["자료구조", "고급웹프로그래밍"])
            self.assertEqual(report["activityCount"], 0)
            self.assertEqual(report["questionCount"], 0)
            self.assertFalse(report["hasFiles"])
            self.assertEqual(validate_backup(output)["sectionCount"], 3)
            with tarfile.open(output, "r:*") as archive:
                names = archive.getnames()
                self.assertFalse(any(name.startswith("activities/") for name in names))
                self.assertFalse(any(name.startswith("files/") for name in names))
                sections = ET.fromstring(archive.extractfile("moodle_backup.xml").read())
                titles = [node.findtext("title") for node in sections.findall("./information/contents/sections/section")]
                self.assertEqual(titles, ["강의 개요", "자료구조", "고급웹프로그래밍"])
                settings = sections.findall("./information/settings/setting")
                self.assertFalse(any(item.findtext("level") in {"activity", "section"} for item in settings))
                self.assertEqual(next(item.findtext("value") for item in settings if item.findtext("name") == "activities"), "0")

    def test_banned_subject_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "base.mbz"
            self.make_template(template)
            with self.assertRaisesRegex(ValueError, "과거 시험 유출"):
                rebuild_backup(template, root / "clean.mbz", ["자료구조 기출"])


if __name__ == "__main__":
    unittest.main()
