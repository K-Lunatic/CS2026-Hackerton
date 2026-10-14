import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_autonomous_section import _EditModeFormParser, _SectionFormParser, _SectionLinkParser


class AutonomousSectionUpdateTests(unittest.TestCase):
    def test_reads_edit_mode_form(self):
        parser = _EditModeFormParser()
        parser.feed('''<form action="/course/view.php" method="post">
          <input type="hidden" name="id" value="65513">
          <input type="hidden" name="sesskey" value="secret">
          <input type="hidden" name="edit" value="on">
        </form>''')
        self.assertEqual(parser.fields['id'], '65513')
        self.assertEqual(parser.fields['edit'], 'on')

    def test_reads_observed_section_edit_link_with_nested_activity(self):
        parser = _SectionLinkParser()
        parser.feed('''<ul><li id="section-1" aria-label="토픽 1">
          <a href="/course/editsection.php?id=869934&sr=0">편집</a>
          <ul><li id="activity-1">활동</li></ul>
        </li></ul>''')
        self.assertEqual(parser.links['1']['href'], '/course/editsection.php?id=869934&sr=0')

    def test_reads_section_form_defaults(self):
        parser = _SectionFormParser()
        parser.feed('''<form action="/course/editsection.php" method="post">
          <input type="hidden" name="id" value="869934">
          <input type="hidden" name="sesskey" value="secret">
          <input name="name" type="text">
          <input name="usedefaultname" type="checkbox" value="1" checked>
          <textarea name="summary_editor[text]">내용</textarea>
          <input type="submit" name="submitbutton" value="저장">
        </form>''')
        self.assertEqual(parser.fields['id'], '869934')
        self.assertEqual(parser.fields['summary_editor[text]'], '내용')
        self.assertEqual(parser.fields['usedefaultname'], '1')


if __name__ == '__main__':
    unittest.main()
