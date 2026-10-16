#!/usr/bin/env python3
"""Plan or submit multiple observed TLS activity forms with one login."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError, MoodleSession
from scripts.inspect_tls_activity_forms import MODULES, _FormParser, authenticated_session


class ActivityControlError(ValueError):
    pass


PROTECTED_FIELDS = {
    "sesskey", "course", "section", "sr", "add", "module", "modulename",
    "coursemodule", "instance", "update", "return", "submitbutton", "submitbutton2", "_qf__mod_",
}
PICKER_FIELDS = {
    "files", "package", "packagechoose", "uuid", "econtentscontents",
    "econtentscontentsname", "selectcontentbtn", "uploadcontentbtn",
}


def _protected(name: str) -> bool:
    return name in PROTECTED_FIELDS or name.startswith("_qf__mod_")


def _plan_items(plan: object, course_id: str) -> list[dict[str, object]]:
    if not isinstance(plan, dict) or str(plan.get("courseId", course_id)) != course_id:
        raise ActivityControlError("계획 파일의 courseId가 대상 강좌와 다릅니다.")
    items = plan.get("items")
    if not isinstance(items, list) or not items or len(items) > 40:
        raise ActivityControlError("활동 계획은 1~40개 항목이어야 합니다.")
    checked: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ActivityControlError("활동 계획 항목 형식이 올바르지 않습니다.")
        module = item.get("module")
        section = str(item.get("section", ""))
        fields = item.get("fields", {})
        if module not in MODULES:
            raise ActivityControlError(f"확인되지 않은 활동 유형은 실행하지 않습니다: {module}")
        if not re.fullmatch(r"[1-9][0-9]*", section) or int(section) > 40:
            raise ActivityControlError("section은 1~40 사이 숫자여야 합니다.")
        if not isinstance(fields, dict) or any(not isinstance(key, str) for key in fields):
            raise ActivityControlError("fields는 문자열 키를 가진 객체여야 합니다.")
        for key, value in fields.items():
            if _protected(key):
                raise ActivityControlError(f"보호 필드는 계획에서 직접 바꿀 수 없습니다: {key}")
            if key == "name" and value is not None and not isinstance(value, str):
                raise ActivityControlError("활동 이름은 하나의 문자열이어야 합니다.")
            values = value if isinstance(value, list) else [value]
            if any(item is not None and not isinstance(item, (str, int, float, bool)) for item in values):
                raise ActivityControlError(f"필드 값은 문자열·숫자·불리언·null 또는 그 목록만 지원합니다: {key}")
        checked.append({"module": module, "section": section, "fields": fields})
    return checked


def _form(session: MoodleSession, course_id: str, item: dict[str, object]):
    module, section = item["module"], item["section"]
    path = f"/course/modedit.php?course={course_id}&section={section}&add={module}"
    try:
        html, page_url = session.get_page(path)
    except HTTPError as error:
        code = error.code
        error.close()
        raise ActivityControlError(f"{module} 추가 화면을 열 수 없습니다. HTTP {code}") from None
    parser = _FormParser()
    parser.feed(html)
    action = urlsplit(urljoin(page_url, parser.action))
    base = urlsplit(session.base_url)
    if (action.scheme, action.hostname, action.path) != (base.scheme, base.hostname, "/course/modedit.php"):
        raise ActivityControlError(f"{module} 저장 주소가 실제 TLS 양식과 다릅니다.")
    if parser.method != "POST":
        raise ActivityControlError(f"{module} 저장 방식이 POST가 아닙니다.")
    return parser, page_url, action.path


def _payload(parser: _FormParser, requested: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name, info in parser.fields.items():
        if info.get("kind") in {"file", "submit", "button", "reset"}:
            continue
        values = parser.field_values.get(name, [info.get("value", "")])
        payload[name] = values if len(values) > 1 else (values[0] if values else info.get("value", ""))
    submit = next((name for name, info in parser.fields.items() if name == "submitbutton"), None)
    if submit:
        payload[submit] = parser.fields[submit].get("value", "저장")
    for name, value in requested.items():
        if name not in parser.fields:
            raise ActivityControlError(f"화면에서 확인되지 않은 필드는 보내지 않습니다: {name}")
        if _protected(name):
            raise ActivityControlError(f"보호 필드는 계획에서 직접 바꿀 수 없습니다: {name}")
        if value is None or value is False:
            payload.pop(name, None)
        elif isinstance(value, list):
            payload[name] = [str(item) for item in value if item is not None and item is not False]
        else:
            payload[name] = str(value)
    return payload


def control_activities(course_id: str, plan_path: Path, *, apply: bool = False) -> dict[str, object]:
    if not re.fullmatch(r"[1-9][0-9]*", course_id):
        raise ActivityControlError("course id must be numeric")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ActivityControlError("활동 계획 파일을 읽지 못했습니다.") from error
    items = _plan_items(plan, course_id)
    session = authenticated_session()
    username = plan.get("username") if isinstance(plan, dict) else None
    courses = MoodleTLSProvider(session).get_autonomous_courses(username or "")
    course = next((item for item in courses if str(item.get("externalId")) == course_id), None)
    if not course or course.get("role") not in {"운영자", "교수"}:
        raise ActivityControlError("내 자율강좌로 확인된 강좌가 아니어서 실행하지 않았습니다.")

    prepared: list[dict[str, object]] = []
    requests: list[tuple[str, bytes, str]] = []
    existing = MoodleTLSProvider(session).get_course_structure(course_id)
    existing_names = {
        (str(section.get("id", "")).removeprefix("section-"), activity.get("name", ""))
        for section in existing.get("sections", [])
        for activity in section.get("activities", [])
    }
    planned_names: set[tuple[str, str]] = set()
    for item in items:
        parser, page_url, action = _form(session, course_id, item)
        payload = _payload(parser, item["fields"])
        name_value = payload.get("name", "")
        if not isinstance(name_value, str):
            raise ActivityControlError("활동 이름 필드가 하나의 문자열이 아닙니다.")
        name = name_value.strip()
        identity = (str(item["section"]), name)
        if name and (identity in existing_names or identity in planned_names):
            raise ActivityControlError(f"같은 토픽에 같은 활동명이 이미 있어 중복 저장하지 않습니다: {name}")
        if name:
            planned_names.add(identity)
        requests.append((action, urlencode(payload, doseq=True).encode(), page_url))
        prepared.append({
            "module": item["module"],
            "section": item["section"],
            "endpoint": action,
            "fieldCount": len(payload),
            "pickerFields": sorted(set(parser.fields) & PICKER_FIELDS),
            "name": name,
            "applied": False,
        })
    if apply:
        for index, (action, body, page_url) in enumerate(requests):
            html, response = session._request(action, data=body, referer=page_url)
            if "/login" in urlsplit(response.geturl()).path or re.search(r'name=["\']username["\']', html, re.I):
                raise LoginError("TLS session expired; sign in again")
            if (urlsplit(response.geturl()).path == "/course/modedit.php" and
                    re.search(r"(?:alert-danger|form-control-feedback|error)", html, re.I)):
                raise ActivityControlError("TLS가 활동 저장 양식을 다시 보여줘 저장 실패로 처리했습니다.")
            prepared[index]["applied"] = True
    return {"courseId": course_id, "mode": "apply" if apply else "plan", "items": prepared}


def main() -> None:
    parser = argparse.ArgumentParser(description="Control multiple observed TLS activity forms")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="계획을 실제 TLS에 저장")
    args = parser.parse_args()
    try:
        print(json.dumps(control_activities(args.course_id, args.plan, apply=args.apply), ensure_ascii=False, indent=2))
    except (ActivityControlError, LoginError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
