"""Local policy for Turtleneck-owned Moodle autonomous courses."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from urllib.parse import parse_qs, urlsplit


class AutonomousCoursePolicyError(ValueError):
    pass


_COURSE_NAME = re.compile(r"^20\d{2}년 [12]학기 자율강좌$")
_BANNED_VISIBLE_NAME = re.compile(
    r"(?:족보|기출(?:문제)?|출제(?:문제)?|past\s*exam|question\s*bank)",
    re.IGNORECASE,
)


def validate_visible_name(value: object, *, field: str = "표시 이름") -> str:
    name = str(value or "").strip()
    if not name:
        raise AutonomousCoursePolicyError(f"{field}을(를) 비워 둘 수 없습니다.")
    if _BANNED_VISIBLE_NAME.search(name):
        raise AutonomousCoursePolicyError(
            f"{field}에 과거 시험 유출로 오해될 수 있는 표현을 사용할 수 없습니다."
        )
    return name


def semester_course_name(year: int, term: int) -> str:
    if type(year) is not int or not 2000 <= year <= 2099:
        raise AutonomousCoursePolicyError("연도는 2000~2099 사이의 정수여야 합니다.")
    if term not in (1, 2):
        raise AutonomousCoursePolicyError("학기는 1 또는 2만 사용할 수 있습니다.")
    return f"{year}년 {term}학기 자율강좌"


def validate_course_name(value: object) -> str:
    name = validate_visible_name(value, field="자율강좌 이름")
    if not _COURSE_NAME.fullmatch(name):
        raise AutonomousCoursePolicyError(
            "자율강좌 이름은 'YYYY년 N학기 자율강좌' 형식만 사용할 수 있습니다."
        )
    return name


def managed_course_id(courses: Iterable[Mapping[str, object]]) -> str | None:
    """Return the one course explicitly marked as managed by Turtleneck.

    Remote course listings do not identify ownership by this product, so an
    explicit local marker is required before a course counts toward the limit.
    """
    ids = [str(course["id"]) for course in courses
           if course.get("turtleneckManaged") is True and course.get("id")]
    if len(ids) > 1:
        raise AutonomousCoursePolicyError("터틀넥 자율강좌는 계정당 1개만 등록할 수 있습니다.")
    return ids[0] if ids else None


def subject_topic_name(subject: object) -> str:
    subject_name = validate_visible_name(subject, field="과목 토픽 이름")
    return validate_visible_name(f"{subject_name} · 모의시험", field="과목 토픽 이름")


def course_external_id_from_url(value: object) -> str:
    """Accept only an explicitly supplied KKU TLS course URL."""
    parsed = urlsplit(str(value or '').strip())
    if parsed.scheme != 'https' or parsed.netloc != 'tls.kku.ac.kr' or parsed.path != '/course/view.php':
        raise AutonomousCoursePolicyError('자율강좌 주소는 tls.kku.ac.kr의 course/view.php 주소만 사용할 수 있습니다.')
    values = parse_qs(parsed.query).get('id', [])
    if len(values) != 1 or not re.fullmatch(r'\d+', values[0]):
        raise AutonomousCoursePolicyError('자율강좌 주소에서 숫자형 course id를 확인하지 못했습니다.')
    return values[0]


def verify_course_reference(courses: Iterable[Mapping[str, object]], url: object) -> dict:
    """Match a supplied URL to locally enrolled data; this does not prove owner role."""
    external_id = course_external_id_from_url(url)
    matches = [dict(course) for course in courses
               if str(course.get('externalId', '')) == external_id or str(course.get('id', '')) == f'tls-course-{external_id}']
    if len(matches) > 1:
        raise AutonomousCoursePolicyError('같은 자율강좌가 로컬 데이터에 여러 번 있어 먼저 동기화를 확인해야 합니다.')
    if not matches:
        return {'verified': False, 'externalId': external_id,
                'reason': '현재 기기에서 해당 강좌의 수강·강좌 정보를 확인하지 못했습니다.'}
    course = matches[0]
    return {'verified': True, 'externalId': external_id, 'course': course,
            'ownerRoleVerified': False,
            'reason': '로컬 수강 목록과 주소가 일치했습니다. 운영자 권한은 아직 확인하지 않았습니다.'}
