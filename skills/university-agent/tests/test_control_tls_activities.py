import json
import tempfile
import unittest
from pathlib import Path

from scripts.control_tls_activities import ActivityControlError, _payload, _plan_items
from scripts.inspect_tls_activity_forms import _FormParser


class ControlTLSActivitiesTests(unittest.TestCase):
    def test_plan_rejects_protected_fields(self):
        with self.assertRaises(ActivityControlError):
            _plan_items({"courseId": "65513", "items": [{"section": 1, "module": "label", "fields": {"sesskey": "x"}}]}, "65513")

    def test_payload_preserves_observed_defaults_and_only_known_fields(self):
        parser = _FormParser()
        parser.feed('<form action="/course/modedit.php" method="post">'
                    '<input type="hidden" name="sesskey" value="secret">'
                    '<input name="name" value="">'
                    '<input type="submit" name="submitbutton" value="저장">'
                    '</form>')
        payload = _payload(parser, {"name": "학습 자료"})
        self.assertEqual(payload["sesskey"], "secret")
        self.assertEqual(payload["name"], "학습 자료")
        self.assertEqual(payload["submitbutton"], "저장")

    def test_plan_reads_multiple_modules(self):
        plan = {"courseId": "65513", "items": [
            {"section": 3, "module": "label", "fields": {"introeditor[text]": "자료"}},
            {"section": 3, "module": "quiz", "fields": {"name": "모의시험"}},
        ]}
        self.assertEqual(len(_plan_items(plan, "65513")), 2)

    def test_repeated_activity_fields_are_preserved(self):
        parser = _FormParser()
        parser.feed('<form action="/course/modedit.php" method="post">'
                    '<input type="hidden" name="option[0]" value="첫 항목">'
                    '<input type="hidden" name="option[0]" value="두 번째 항목">'
                    '<input type="submit" name="submitbutton" value="저장">'
                    '</form>')
        payload = _payload(parser, {"option[0]": ["두 번째 항목", "세 번째 항목"]})
        self.assertEqual(payload["option[0]"], ["두 번째 항목", "세 번째 항목"])


if __name__ == "__main__":
    unittest.main()
