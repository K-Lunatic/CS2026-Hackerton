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

from scripts.assemble_moodle_backup import assemble_backup
from scripts.validate_moodle_backup import validate_backup


def xml(value: str) -> bytes:
    return value.strip().encode("utf-8")


class AssembleMoodleBackupTests(unittest.TestCase):
    def make_template(self, path: Path, *, banned: bool = False) -> None:
        files = {
            "moodle_backup.xml": xml("""
                <moodle_backup><information><contents><activities>
                  <activity><moduleid>30</moduleid><sectionid>20</sectionid><modulename>quiz</modulename><title>기존 시험</title><directory>activities/quiz_30</directory></activity>
                </activities></contents></information></moodle_backup>
            """),
            "course/course.xml": xml('<course id="10" contextid="11"><fullname>2026년 2학기 자율강좌</fullname></course>'),
            "files.xml": xml('<files></files>'),
            "questions.xml": xml('<question_categories><question_category><contextlevel>50</contextlevel><contextinstanceid>10</contextinstanceid><questions>' + ('<question><name>족보 문항</name></question>' if banned else '') + '</questions></question_category></question_categories>'),
            "sections/section_20/section.xml": xml('<section id="20"><name>자료구조</name><sequence>30</sequence></section>'),
            "activities/quiz_30/module.xml": xml('<module id="30"><sectionid>20</sectionid></module>'),
            "activities/quiz_30/quiz.xml": xml('<activity id="30" moduleid="30" contextid="40"><quiz id="30"><name>기존 시험</name><sumgrades>1</sumgrades><grade>1</grade><question_instances><question_instance id="31"><questionid>32</questionid></question_instance></question_instances></quiz></activity>'),
        }
        with tarfile.open(path, "w:gz") as archive:
            for name, payload in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

    def make_questions(self, path: Path) -> None:
        path.write_bytes(xml("""
            <quiz><question type="multichoice"><name><text>배열 핵심 개념</text></name>
              <questiontext format="html"><text>배열의 접근 시간은?</text></questiontext>
              <defaultgrade>1</defaultgrade><penalty>0</penalty><single>true</single><shuffleanswers>true</shuffleanswers><answernumbering>abc</answernumbering>
              <answer fraction="100"><text>O(1)</text></answer><answer fraction="0"><text>O(n)</text></answer>
            </question></quiz>
        """))

    def test_assembles_quiz_and_revalidates_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template, questions, output = root / "base.mbz", root / "questions.xml", root / "new.mbz"
            self.make_template(template)
            self.make_questions(questions)
            report = assemble_backup(template, questions, output, section_name="자료구조", quiz_name="자료구조 · 모의시험")
            self.assertTrue(report["localOnly"])
            self.assertEqual(report["questionCount"], 1)
            self.assertEqual(validate_backup(output)["activityCount"], 2)
            with tarfile.open(output, "r:*") as archive:
                activities = ET.fromstring(archive.extractfile("moodle_backup.xml").read())
                titles = [node.findtext("title") for node in activities.findall("./information/contents/activities/activity")]
                self.assertIn("자료구조 · 모의시험", titles)
                section = ET.fromstring(archive.extractfile("sections/section_20/section.xml").read())
                sequence = section.findtext("sequence")
                self.assertRegex(sequence, r"^30,[1-9][0-9]*$")

    def test_banned_quiz_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template, questions = root / "base.mbz", root / "questions.xml"
            self.make_template(template)
            self.make_questions(questions)
            with self.assertRaisesRegex(ValueError, "과거 시험 유출"):
                assemble_backup(template, questions, root / "new.mbz", section_name="자료구조", quiz_name="자료구조 기출")

    def test_banned_existing_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template, questions = root / "base.mbz", root / "questions.xml"
            self.make_template(template, banned=True)
            self.make_questions(questions)
            with self.assertRaisesRegex(ValueError, "기존 백업의 금지된 표시 이름"):
                assemble_backup(template, questions, root / "new.mbz", section_name="자료구조", quiz_name="자료구조 · 모의시험")


if __name__ == "__main__":
    unittest.main()
