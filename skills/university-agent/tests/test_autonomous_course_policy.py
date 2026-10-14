import unittest

from features.autonomous_course_policy import (
    AutonomousCoursePolicyError,
    managed_course_id,
    semester_course_name,
    subject_topic_name,
    course_external_id_from_url,
    verify_course_reference,
    validate_course_name,
    validate_visible_name,
)


class AutonomousCoursePolicyTests(unittest.TestCase):
    def test_course_name_and_subject_topic_are_neutral(self):
        self.assertEqual(semester_course_name(2026, 2), "2026년 2학기 자율강좌")
        self.assertEqual(subject_topic_name("자료구조"), "자료구조 · 모의시험")

    def test_course_name_must_use_standard_title(self):
        with self.assertRaises(AutonomousCoursePolicyError):
            validate_course_name("자료구조 시험 대비")

    def test_leak_like_visible_names_are_rejected(self):
        for value in ("자료구조 족보", "기출문제 모음", "출제 예상"):
            with self.subTest(value=value):
                with self.assertRaises(AutonomousCoursePolicyError):
                    validate_visible_name(value)

    def test_only_explicitly_managed_course_counts_toward_limit(self):
        courses = [
            {"id": "old-course", "role": "운영자"},
            {"id": "managed-course", "turtleneckManaged": True},
        ]
        self.assertEqual(managed_course_id(courses), "managed-course")
        with self.assertRaises(AutonomousCoursePolicyError):
            managed_course_id(courses + [{"id": "second", "turtleneckManaged": True}])

    def test_course_url_matches_local_enrollment_without_claiming_owner_role(self):
        url = 'https://tls.kku.ac.kr/course/view.php?id=65513'
        self.assertEqual(course_external_id_from_url(url), '65513')
        result = verify_course_reference([{'id': 'tls-course-65513', 'externalId': '65513', 'name': '자율강좌'}], url)
        self.assertTrue(result['verified'])
        self.assertFalse(result['ownerRoleVerified'])
        self.assertFalse(verify_course_reference([], url)['verified'])
        with self.assertRaises(AutonomousCoursePolicyError):
            course_external_id_from_url('https://example.com/course/view.php?id=65513')


if __name__ == "__main__":
    unittest.main()
