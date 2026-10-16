#!/usr/bin/env python3
"""Inspect and create Moodle question-bank items through observed TLS forms.

The form supplies defaults and hidden fields; the plan only overrides fields
that were present on that exact question-type form. No endpoint is guessed and
the default mode is read-only inspection.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.moodle_session import LoginError
from scripts.inspect_tls_activity_forms import _FormParser, authenticated_session


QUESTION_TYPES = (
    "multichoiceset", "truefalse", "calculatedmulti", "calculated",
    "calculatedsimple", "randomsamatch", "multianswer", "essay",
    "multichoice", "numerical", "shortanswer", "match", "description",
)
PROTECTED = {"sesskey", "category", "cmid", "id", "returnurl", "qtype", "wizardpage", "scrollpos"}


class QuizQuestionError(ValueError):
    pass


def _visible_errors(page_html: str) -> str:
    """Return only short, visible form validation text; never hidden fields."""
    blocks = re.findall(
        r'<(?:div|span|p)[^>]+class=["\'][^"\']*(?:alert|error|invalid)[^"\']*["\'][^>]*>(.*?)</(?:div|span|p)>',
        page_html, re.I | re.S,
    )
    messages = []
    for block in blocks:
        text = re.sub(r'<[^>]+>', ' ', html.unescape(block))
        text = re.sub(r'\s+', ' ', text).strip()
        if text and text not in messages:
            messages.append(text[:300])
    return ' '.join(messages[:5])


def _visible_text(page_html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html.unescape(page_html))
    return re.sub(r'\s+', ' ', text).strip()


def _quiz_contains(session, quiz_id: str, name: str) -> bool:
    page, _ = session.get_page(f"/mod/quiz/edit.php?cmid={quiz_id}")
    return bool(name.strip() and name.strip() in _visible_text(page))


def _attach_from_question_bank(session, quiz_id: str, name: str) -> bool:
    """Use the exact add link returned by the quiz's observed question-bank AJAX."""
    if _quiz_contains(session, quiz_id, name):
        return True
    payload, _ = session.get_page(f"/mod/quiz/questionbank.ajax.php?cmid={quiz_id}")
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as error:
        raise QuizQuestionError("TLS 문제은행 응답을 읽지 못했습니다.") from error
    if result.get('status') != 'OK' or not isinstance(result.get('contents'), str):
        raise QuizQuestionError("TLS 문제은행을 열지 못했습니다.")
    matches = []
    for row in re.findall(r'<tr\b.*?</tr>', result['contents'], re.I | re.S):
        if name not in _visible_text(row):
            continue
        for raw in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>', row, re.I | re.S):
            href = html.unescape(raw)
            parts = urlsplit(href)
            if 'addquestion' in dict(parse_qsl(parts.query, keep_blank_values=True)):
                matches.append(href)
    if len(matches) != 1:
        raise QuizQuestionError("문제은행에서 대상 문항의 퀴즈 추가 링크를 하나로 확인하지 못했습니다.")
    href = urlsplit(matches[0])
    response_html, response = session._request(href.path + ('?' + href.query if href.query else ''))
    if '/login' in urlsplit(response.geturl()).path or re.search(r'name=["\']username["\']', response_html, re.I):
        raise LoginError("TLS session expired; sign in again")
    return _quiz_contains(session, quiz_id, name)


def _category(session, quiz_id: str) -> str:
    try:
        page_html, _ = session.get_page(f"/mod/quiz/edit.php?cmid={quiz_id}")
    except HTTPError as error:
        error.close()
        raise QuizQuestionError("퀴즈 편집 화면을 열 수 없습니다.") from None
    normalized = html.unescape(page_html)
    matches = re.findall(r"(?:[?&])category=([1-9][0-9]*)", normalized)
    if not matches:
        matches = re.findall(r'name=["\']category["\'][^>]*value=["\']([1-9][0-9]*)', normalized, re.I)
    if not matches:
        raise QuizQuestionError("퀴즈의 문제은행 범주를 화면에서 확인하지 못했습니다.")
    return matches[0]


def _form(session, quiz_id: str, category: str, qtype: str, *, returnurl: str | None = None):
    query = {'category': category, 'cmid': quiz_id, 'qtype': qtype}
    if returnurl:
        query['returnurl'] = returnurl
    try:
        html, page_url = session.get_page('/question/question.php?' + urlencode(query))
    except HTTPError as error:
        error.close()
        raise QuizQuestionError(f"{qtype} 문항 양식을 열 수 없습니다.") from None
    parser = _FormParser()
    parser.feed(html)
    action = urlsplit(page_url)._replace(query="", fragment="").path
    actual = urlsplit(page_url)
    form_action = urlsplit(urljoin(page_url, parser.action or page_url))
    if (form_action.scheme and form_action.hostname != actual.hostname) or form_action.path != "/question/question.php":
        raise QuizQuestionError(f"{qtype} 문항 저장 주소가 TLS 화면과 다릅니다.")
    if parser.method != "POST":
        raise QuizQuestionError(f"{qtype} 문항 저장 방식이 POST가 아닙니다.")
    return parser, page_url


def inspect_question_forms(quiz_id: str) -> list[dict[str, object]]:
    if not re.fullmatch(r"[1-9][0-9]*", quiz_id):
        raise QuizQuestionError("quiz id must be numeric")
    session = authenticated_session()
    category = _category(session, quiz_id)
    result = []
    for qtype in QUESTION_TYPES:
        try:
            parser, _ = _form(session, quiz_id, category, qtype)
            result.append({"type": qtype, "categoryConfirmed": True,
                           "method": parser.method, "action": "/question/question.php",
                           "fieldNames": sorted(parser.fields)})
        except (HTTPError, QuizQuestionError) as error:
            result.append({"type": qtype, "available": False, "error": str(error)})
    return result


def _plan_questions(plan: object, quiz_id: str) -> list[dict[str, object]]:
    if not isinstance(plan, dict) or str(plan.get("quizId", quiz_id)) != quiz_id:
        raise QuizQuestionError("계획 파일의 quizId가 대상 퀴즈와 다릅니다.")
    questions = plan.get("questions")
    if not isinstance(questions, list) or not questions or len(questions) > 50:
        raise QuizQuestionError("문항 계획은 1~50개여야 합니다.")
    checked = []
    for item in questions:
        if not isinstance(item, dict) or item.get("type") not in QUESTION_TYPES or not isinstance(item.get("fields", {}), dict):
            raise QuizQuestionError("확인된 문항 유형과 fields가 필요합니다.")
        if any(not isinstance(key, str) or key in PROTECTED for key in item["fields"]):
            raise QuizQuestionError("문제은행 보호 필드는 계획에서 바꿀 수 없습니다.")
        for value in item["fields"].values():
            values = value if isinstance(value, list) else [value]
            if any(item is not None and not isinstance(item, (str, int, float, bool)) for item in values):
                raise QuizQuestionError("문항 필드 값은 문자열·숫자·불리언·null 또는 그 목록만 지원합니다.")
        if "name" in item["fields"] and item["fields"]["name"] is not None and not isinstance(item["fields"]["name"], str):
            raise QuizQuestionError("문항 이름은 하나의 문자열이어야 합니다.")
        checked.append({"type": item["type"], "fields": item["fields"]})
    return checked


def _payload(parser: _FormParser, fields: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name, info in parser.fields.items():
        if info.get("kind") in {"file", "submit", "button", "reset"}:
            continue
        values = parser.field_values.get(name, [info.get("value", "")])
        payload[name] = values if len(values) > 1 else next(iter(values), info.get("value", ""))
    for name, value in fields.items():
        if name not in parser.fields:
            raise QuizQuestionError(f"화면에서 확인되지 않은 {name} 필드는 보내지 않습니다.")
        if name in PROTECTED:
            raise QuizQuestionError(f"보호 필드는 계획에서 바꿀 수 없습니다: {name}")
        if value is None or value is False:
            payload.pop(name, None)
        elif isinstance(value, list):
            payload[name] = [str(item) for item in value if item is not None and item is not False]
        else:
            payload[name] = str(value)
    # Moodle forms often expose both a normal save button and a secondary
    # button (for example "save and continue"). Sending both can select an
    # unintended branch. Keep the form's ordinary submit control only.
    submit_names = [name for name, info in parser.fields.items() if info.get("kind") == "submit"]
    submit_name = "submitbutton" if "submitbutton" in submit_names else (submit_names[0] if submit_names else None)
    if submit_name:
        payload[submit_name] = parser.fields[submit_name].get("value", "저장")
    return payload


def control_questions(quiz_id: str, plan_path: Path, *, apply: bool = False) -> dict[str, object]:
    questions = _plan_questions(json.loads(plan_path.read_text(encoding="utf-8")), quiz_id)
    session = authenticated_session()
    category = _category(session, quiz_id)
    prepared, requests = [], []
    returnurl = f"/mod/quiz/edit.php?cmid={quiz_id}&addonpage=0"
    for item in questions:
        parser, page_url = _form(session, quiz_id, category, item["type"], returnurl=returnurl)
        payload = _payload(parser, item["fields"])
        name_value = payload.get("name", "")
        if not isinstance(name_value, str):
            raise QuizQuestionError("문항 이름 필드가 하나의 문자열이 아닙니다.")
        requests.append((page_url, urlencode(payload, doseq=True).encode()))
        prepared.append({"type": item["type"], "endpoint": "/question/question.php",
                         "categoryConfirmed": True, "fieldCount": len(payload),
                         "namePresent": bool(name_value.strip()), "applied": False})
    if apply:
        for index, (referer, body) in enumerate(requests):
            response_html, response = session._request("/question/question.php", data=body, referer=referer)
            location = urlsplit(response.geturl())
            if "/login" in location.path or re.search(r'name=["\']username["\']', response_html, re.I):
                raise LoginError("TLS session expired; sign in again")
            if location.path == "/question/question.php" and re.search(r"(?:alert-danger|form-control-feedback|error)", response_html, re.I):
                detail = _visible_errors(response_html)
                raise QuizQuestionError("TLS가 문항 양식을 다시 보여줘 저장 실패로 처리했습니다." + (f" 사유: {detail}" if detail else ""))
            prepared[index]["applied"] = True
            if match := re.search(r"(?:[?&])id=([1-9][0-9]*)", response.geturl()):
                prepared[index]["questionId"] = match.group(1)
        if apply:
            edit_html, _ = session.get_page(f"/mod/quiz/edit.php?cmid={quiz_id}")
            for item, entry in zip(questions, prepared):
                name = str(item['fields'].get('name', '')).strip()
                entry['attached'] = _attach_from_question_bank(session, quiz_id, name) if name else False
    return {"quizId": quiz_id, "categoryConfirmed": True,
            "mode": "apply" if apply else "plan", "questions": prepared}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or create observed TLS quiz question forms")
    parser.add_argument("--quiz-id", required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect_question_forms(args.quiz_id) if args.plan is None else control_questions(args.quiz_id, args.plan, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, LoginError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
