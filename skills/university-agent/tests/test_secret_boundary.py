import unittest

from features.secret_boundary import redact_output


class SecretBoundaryTests(unittest.TestCase):
    def test_redacts_nested_secret_fields_and_headers(self):
        result = redact_output({
            "courseId": "course-1",
            "content": "Cookie: MoodleSession=visible-only-in-fixture",
            "credentials": {
                "username": "student-1",
                "password": "do-not-show",
                "MoodleSession": "cookie-value",
                "accessToken": "oauth-value",
                "sessionId": "session-value",
            },
            "headers": "Authorization: Bearer bearer-value",
        })
        self.assertEqual(result["courseId"], "course-1")
        self.assertEqual(result["credentials"]["username"], "student-1")
        for key in ("password", "MoodleSession", "accessToken", "sessionId"):
            self.assertEqual(result["credentials"][key], "[REDACTED]")
        self.assertNotIn("visible-only-in-fixture", result["content"])
        self.assertNotIn("bearer-value", result["headers"])

    def test_keeps_normal_academic_values(self):
        value = {"examId": "exam-1", "sessionStatus": "READY", "title": "자료구조"}
        self.assertEqual(redact_output(value), value)


if __name__ == "__main__":
    unittest.main()
