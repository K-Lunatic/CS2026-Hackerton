import unittest

from scripts.inspect_tls_activity_forms import _FormParser


class InspectTLSActivityFormsTests(unittest.TestCase):
    def test_parser_keeps_names_only(self):
        parser = _FormParser()
        parser.feed(
            '<form action="/course/modedit.php" method="post">'
            '<input type="hidden" name="sesskey" value="secret">'
            '<input type="file" name="files">'
            '<textarea name="intro">text</textarea>'
            '<select name="format"><option selected value="1">HTML</option></select>'
            '</form>'
        )
        self.assertEqual(parser.action, "/course/modedit.php")
        self.assertEqual(parser.method, "POST")
        self.assertEqual(sorted(parser.fields), ["files", "format", "intro", "sesskey"])
        self.assertEqual(parser.fields["files"]["kind"], "file")


if __name__ == "__main__":
    unittest.main()
