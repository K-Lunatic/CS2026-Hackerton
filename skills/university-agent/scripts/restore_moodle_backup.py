#!/usr/bin/env python3
"""Prepare or execute a verified Moodle backup restore for one owned TLS course."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.autonomous_course_policy import AutonomousCoursePolicyError, course_external_id_from_url
from providers.credentials import CredentialInputRequired, resolve, save
from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import LoginError, MoodleSession
from scripts.tls_backup_download import _course_context_id
from scripts.validate_moodle_backup import validate_backup


class MoodleBackupRestoreError(ValueError):
    pass


class _RestorePageParser(HTMLParser):
    """Read only the first restore form and its file-picker bootstrap object."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.form: dict[str, object] | None = None
        self.in_form = False
        self.script = False
        self.scripts: list[str] = []
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and self.form is None:
            self.form = {"action": values.get("action") or "", "method": values.get("method") or "get", "hidden": {}}
            self.in_form = True
        if tag == "script":
            self.script = True
            self._script_parts = []
        if self.in_form and tag == "input" and self.form is not None:
            name = values.get("name") or ""
            if name and (values.get("type") or "").lower() == "hidden":
                self.form["hidden"][name] = values.get("value") or ""
            if name == "submitbutton":
                self.form["submit"] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_form:
            self.in_form = False
        if tag == "script" and self.script:
            self.scripts.append("".join(self._script_parts))
            self.script = False
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self.script:
            self._script_parts.append(data)


class _WizardFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, object]] = []
        self.form: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.form = {"action": values.get("action") or "", "inputs": []}
            self.forms.append(self.form)
        elif self.form is not None and tag in {"input", "button"}:
            self.form["inputs"].append(values)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.form = None


def _wizard_fields(form: dict[str, object]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in form["inputs"]:
        name = item.get("name") or ""
        kind = (item.get("type") or "").lower()
        if not name or kind in {"submit", "button", "reset"}:
            continue
        if kind in {"radio", "checkbox"} and "checked" not in item:
            continue
        fields[str(name)] = str(item.get("value") or "")
    return fields


def _wizard_button(form: dict[str, object], label: str) -> bool:
    return any((item.get("type") or "").lower() in {"submit", "button"} and
               (item.get("value") or "") == label for item in form["inputs"])


def _post_wizard(session: MoodleSession, page_url: str, html: str, button: str,
                 overrides: dict[str, str] | None = None,
                 required: dict[str, str] | None = None) -> tuple[str, str, dict[str, str]]:
    parser = _WizardFormParser()
    parser.feed(html)
    form = next((form for form in parser.forms
                 if _wizard_button(form, button) and
                 all(_wizard_fields(form).get(key) == value for key, value in (required or {}).items())), None)
    if form is None:
        raise MoodleBackupRestoreError(f"TLS 복구 단계에서 '{button}' 버튼을 찾지 못했어요.")
    action = urljoin(page_url, str(form["action"]))
    parsed = urlsplit(action)
    expected = urlsplit(session.base_url)
    if parsed.scheme != expected.scheme or parsed.hostname != expected.hostname or parsed.path != "/backup/restore.php":
        raise MoodleBackupRestoreError("예상하지 못한 TLS 복구 주소라 중단했어요.")
    fields = _wizard_fields(form)
    fields.update(overrides or {})
    fields["submitbutton"] = button
    response_html, response = session._request(parsed.path + (f"?{parsed.query}" if parsed.query else ""),
                                               data=urlencode(fields).encode(), referer=page_url)
    return response_html, response.geturl(), fields


def _balanced_object(text: str, marker: str) -> dict[str, object]:
    start = text.find(marker)
    if start < 0:
        raise MoodleBackupRestoreError("TLS 복구 화면의 파일 선택기 설정을 찾지 못했어요.")
    start = text.find("{", start)
    if start < 0:
        raise MoodleBackupRestoreError("TLS 복구 화면의 파일 선택기 설정이 비어 있어요.")
    depth = 0
    quote: str | None = None
    escaped = False
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise MoodleBackupRestoreError("TLS 복구 화면의 파일 선택기 설정을 읽지 못했어요.")
    try:
        value = json.loads(text[start:end])
    except json.JSONDecodeError as error:
        raise MoodleBackupRestoreError("TLS 복구 화면의 파일 선택기 설정 형식이 달라요.") from error
    if not isinstance(value, dict):
        raise MoodleBackupRestoreError("TLS 복구 화면의 파일 선택기 설정이 올바르지 않아요.")
    return value


def _restore_page(session: MoodleSession, course_id: str) -> tuple[str, str, dict[str, str], dict[str, object]]:
    context_id = _course_context_id(session, course_id)
    path = f"/backup/restorefile.php?contextid={context_id}"
    html, page_url = session.get_page(path)
    parser = _RestorePageParser()
    parser.feed(html)
    if not parser.form:
        raise MoodleBackupRestoreError("TLS 복구 화면의 복구 양식을 찾지 못했어요.")
    form = parser.form
    form_action = urlsplit(urljoin(page_url, str(form["action"]))).path
    if form_action != "/backup/restorefile.php":
        raise MoodleBackupRestoreError("예상하지 못한 복구 주소라 업로드를 멈췄어요.")
    config = _balanced_object("\n".join(parser.scripts), "M.form_filepicker.init")
    options = config.get("options") if isinstance(config.get("options"), dict) else {}
    item_id = config.get("itemid") or options.get("itemid")
    client_id = config.get("client_id") or options.get("client_id")
    repositories = config.get("repositories")
    if not isinstance(repositories, dict):
        raise MoodleBackupRestoreError("TLS 업로드 저장소 목록을 확인하지 못했어요.")
    upload_repo = next((repo_id for repo_id, repo in repositories.items()
                        if isinstance(repo, dict) and repo.get("type") == "upload"), None)
    if not item_id or not client_id or not upload_repo:
        raise MoodleBackupRestoreError("TLS 업로드에 필요한 파일 선택기 정보가 부족해요.")
    hidden = {str(key): str(value) for key, value in form["hidden"].items()}
    hidden["backupfile"] = str(item_id)
    return page_url, context_id, hidden, {
        "itemId": str(item_id), "clientId": str(client_id), "repoId": str(upload_repo),
        "formAction": form_action, "submitLabel": str(form.get("submit") or "복구"),
    }


def _multipart(fields: dict[str, str], field_name: str, path: Path) -> tuple[bytes, str]:
    boundary = "----Turtleneck" + secrets.token_hex(12)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend((f"--{boundary}\r\n".encode(),
                       f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                       str(value).encode(), b"\r\n"))
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend((f"--{boundary}\r\n".encode(),
                   f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode(),
                   f"Content-Type: {content_type}\r\n\r\n".encode(),
                   path.read_bytes(), b"\r\n",
                   f"--{boundary}--\r\n".encode()))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _upload(session: MoodleSession, page_url: str, meta: dict[str, str], backup: Path, sesskey: str) -> dict[str, object]:
    body, content_type = _multipart({
        "action": "upload", "repo_id": meta["repoId"], "itemid": meta["itemId"],
        "sesskey": sesskey, "client_id": meta["clientId"], "savepath": "/",
    }, "repo_upload_file", backup)
    html, _ = session.post_bytes("/repository/repository_ajax.php", body,
                                 content_type=content_type, referer=page_url,
                                 allowed_origin="tls.kku.ac.kr")
    try:
        result = json.loads(html)
    except json.JSONDecodeError as error:
        raise MoodleBackupRestoreError("TLS 파일 업로드 응답을 읽지 못했어요.") from error
    if not isinstance(result, dict) or result.get("error"):
        raise MoodleBackupRestoreError("TLS가 백업 파일 업로드를 거부했어요.")
    return {"uploaded": True, "filename": backup.name, "bytes": backup.stat().st_size}


def restore_backup(backup: Path, course_url: str, *, apply: bool = False,
                   execute: bool = False) -> dict[str, object]:
    backup = backup.expanduser().resolve()
    try:
        course_id = course_external_id_from_url(course_url)
    except AutonomousCoursePolicyError as error:
        raise MoodleBackupRestoreError(str(error)) from error
    report = validate_backup(backup)
    try:
        username, password = resolve(os.environ.get("TLS_USERNAME"))
    except CredentialInputRequired as error:
        raise MoodleBackupRestoreError(str(error)) from error
    session = MoodleSession(os.environ.get("TLS_BASE_URL", "https://tls.kku.ac.kr"))
    session.login(username, password)
    save(username, password)
    courses = MoodleTLSProvider(session).get_autonomous_courses(username)
    target = next((course for course in courses if str(course.get("externalId")) == course_id), None)
    if not target or target.get("role") not in {"운영자", "교수"}:
        raise MoodleBackupRestoreError("내 운영 권한이 확인된 자율강좌가 아니어서 복구하지 않았어요.")
    page_url, context_id, hidden, meta = _restore_page(session, course_id)
    plan = {"courseId": course_id, "courseName": target.get("name"),
            "contextId": context_id, "backup": report, "restoreForm": meta,
            "apply": bool(apply)}
    if execute and not apply:
        raise MoodleBackupRestoreError("최종 복구는 --apply와 --execute를 함께 지정해야 해요.")
    if not apply:
        return {**plan, "localOnly": True, "status": "ready"}
    sesskey = hidden.get("sesskey")
    if not sesskey:
        raise MoodleBackupRestoreError("TLS 복구 폼의 보안 키를 확인하지 못했어요.")
    upload = _upload(session, page_url, meta, backup, sesskey)
    restore_fields = {**hidden, "backupfile": meta["itemId"], "submitbutton": meta["submitLabel"]}
    html, response = session._request("/backup/restorefile.php", data=urlencode(restore_fields).encode(), referer=page_url)
    wizard_url = response.geturl()
    html, wizard_url, _ = _post_wizard(session, wizard_url, html, "계속")
    html, wizard_url, _ = _post_wizard(
        session, wizard_url, html, "계속", {"target": "0", "targetid": course_id},
        {"targetid": course_id},
    )
    root_settings = {name: "0" for name in _wizard_fields(_first_wizard_form(html, "다음"))
                     if name.startswith("setting_root_")}
    html, wizard_url, _ = _post_wizard(session, wizard_url, html, "다음", root_settings)
    html, wizard_url, _ = _post_wizard(session, wizard_url, html, "다음", {
        "setting_course_keep_roles_and_enrolments": "1",
        "setting_course_keep_groups_and_groupings": "1",
        "setting_course_overwrite_conf": "1",
    })
    review_form = _first_wizard_form(html, "복구 실행")
    review_fields = _wizard_fields(review_form)
    if review_fields.get("setting_root_activities") != "0":
        raise MoodleBackupRestoreError("복구 검토 단계에서 활동 포함이 해제되지 않아 중단했어요.")
    if report.get("activityCount") != 0 or report.get("hasFiles"):
        raise MoodleBackupRestoreError("비어 있지 않은 백업은 이 전체 교체 흐름에 사용할 수 없어요.")
    ready = {**plan, "upload": upload, "status": "ready_to_restore", "wizard": "review"}
    if not execute:
        return ready
    _final_html, final_response = _post_wizard(session, wizard_url, html, "복구 실행")[:2]
    structure = MoodleTLSProvider(session).get_course_structure(course_id)
    sections = structure.get("sections", [])
    section_names = [str(section.get("name") or "") for section in sections]
    expected_topics = max(int(report.get("sectionCount", 1)) - 1, 0)
    if len(sections) != expected_topics or any(re.fullmatch(r"토픽 [1-9][0-9]*", name) for name in section_names):
        raise MoodleBackupRestoreError("복구 후 TLS에서 과목 토픽 이름을 확인하지 못했어요.")
    if sum(len(section.get("activities", [])) for section in sections) != 0:
        raise MoodleBackupRestoreError("복구 후 TLS에 기존 활동이 남아 있어 결과를 확정하지 않았어요.")
    return {**ready, "status": "restored", "completedPath": urlsplit(final_response).path,
            "sectionNames": section_names}


def _first_wizard_form(html: str, button: str) -> dict[str, object]:
    parser = _WizardFormParser()
    parser.feed(html)
    form = next((form for form in parser.forms if _wizard_button(form, button)), None)
    if form is None:
        raise MoodleBackupRestoreError(f"TLS 복구 단계에서 '{button}' 양식을 찾지 못했어요.")
    return form


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare or upload a verified Moodle course backup")
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--course-url", required=True)
    parser.add_argument("--apply", action="store_true", help="파일을 TLS 복구 마법사에 전송")
    parser.add_argument("--execute", action="store_true", help="검증된 대상 강좌의 기존 내용을 삭제 후 복구")
    args = parser.parse_args()
    try:
        print(json.dumps(restore_backup(args.backup, args.course_url, apply=args.apply, execute=args.execute), ensure_ascii=False))
    except (MoodleBackupRestoreError, LoginError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
