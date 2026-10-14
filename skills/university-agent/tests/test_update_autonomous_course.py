import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_autonomous_course import CourseUpdateError, _parse_form, _validate_updates


class AutonomousCourseUpdateTests(unittest.TestCase):
    def test_parse_observed_settings_form(self):
        action, fields = _parse_form('''<form action="/local/ubion/setting/action.php">
          <input type="hidden" name="type" value="course_setting">
          <input type="hidden" name="id" value="65513">
          <input type="hidden" name="sesskey" value="secret">
          <select name="numsections"><option value="8" selected>8</option></select>
          <input type="radio" name="course_background" value="0" checked>
        </form>''')
        self.assertEqual(action, '/local/ubion/setting/action.php')
        self.assertEqual(fields['numsections'], '8')
        self.assertEqual(fields['course_background'], '0')

    def test_rejects_unapproved_setting(self):
        with self.assertRaises(CourseUpdateError):
            _validate_updates({'format': 'topics'})


if __name__ == '__main__':
    unittest.main()
