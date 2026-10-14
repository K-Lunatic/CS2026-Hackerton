import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.moodle_provider import MoodleTLSProvider, parse_autonomous_courses, parse_course_structure


PAGE = """
<table><tbody>
<tr><th>번호</th><th>공개여부</th><th>분류</th><th>자율강좌 명</th><th>역할</th><th>상태</th><th>비고</th></tr>
<tr><td>2</td><td>비공개</td><td>공학</td>
  <td><a href="/course/view.php?id=65513">2026년 2학기 자율강좌</a><br/><span>2026년 6월 15일 ~ 2026년 6월 15일</span></td>
  <td>운영자</td><td>종료</td><td><a href="/local/ubeclass/request.php?id=1376">수정</a><span class="hidden">sesskey=secret</span></td></tr>
<tr><td>1</td><td>공개</td><td>기타</td>
  <td>일반 과정<br/>2026년 1월 1일 ~ 2026년 2월 1일</td>
  <td>회원</td><td>진행중</td><td><button>수강취소</button></td></tr>
</tbody></table>
<script><a href="/course/view.php?id=99999">숨은 과정</a></script>
"""

COURSE_PAGE = """
<div class="course-content">
 <ul><li id="section-0" class="section" aria-label="강의 개요">
  <h3>강의 개요</h3><ul class="section">
   <li class="activity ubboard modtype_ubboard"><a href="/mod/ubboard/view.php?id=773865"><span>과목공지</span><span class="accesshide"> 게시판</span></a></li>
  </ul>
 </li><li id="section-1" class="section" aria-label="자료구조">
  <h3>자료구조</h3><ul class="section">
   <li class="activity quiz modtype_quiz"><a href="/mod/quiz/view.php?id=799872">자료구조 중간고사 대비</a></li>
  </ul>
 </li></ul>
</div>
"""


class FakeSession:
    def __init__(self, html):
        self.html = html
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        return self.html


class AutonomousCourseTests(unittest.TestCase):
    def test_parser_reads_visible_rows_and_keeps_role_unverified(self):
        courses = parse_autonomous_courses(PAGE)
        self.assertEqual(len(courses), 2)
        self.assertEqual(courses[0]["externalId"], "65513")
        self.assertEqual(courses[0]["name"], "2026년 2학기 자율강좌")
        self.assertTrue(courses[0]["ownerControlsVisible"])
        self.assertFalse(courses[0]["ownerRoleVerified"])
        self.assertEqual(courses[1]["name"], "일반 과정")
        self.assertIsNone(courses[1]["externalId"])
        self.assertNotIn("sesskey", str(courses))
        self.assertNotIn("99999", str(courses))

    def test_provider_uses_read_only_autonomous_listing_once(self):
        session = FakeSession(PAGE)
        provider = MoodleTLSProvider(session)
        first = provider.get_autonomous_courses("user")
        second = provider.get_autonomous_courses("user")
        self.assertEqual(first, second)
        self.assertEqual(session.paths, ["/local/ubeclass/my.php"])

    def test_course_structure_keeps_topics_and_visible_activity_names(self):
        blueprint = parse_course_structure(COURSE_PAGE)
        self.assertEqual([section["name"] for section in blueprint["sections"]], ["강의 개요", "자료구조"])
        self.assertEqual(blueprint["sections"][0]["activities"][0]["name"], "과목공지")
        self.assertEqual(blueprint["sections"][1]["activities"][0]["type"], "quiz")
        self.assertEqual(blueprint["sections"][1]["activities"][0]["externalId"], "799872")


if __name__ == "__main__":
    unittest.main()
