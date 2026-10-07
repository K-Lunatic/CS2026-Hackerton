#!/usr/bin/env python3
"""Preview/confirm Everytime changes using the official web's request contracts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from everytime import Client, EverytimeError, PUBLIC_URL, parse_timetable, xml_response
from everytime_cache import LocalCache
from session_store import load, save

# These paths/fields come from board.index.js, timetable.tablesave.js,
# timetable.index.js and timetable.customsubjects.js, not guessed endpoints.
FIELDS = {
    "table.create": {"year", "semester", "name"},
    "table.rename": {"tableId", "year", "semester", "name"},
    "table.primary": {"tableId", "year", "semester"},
    "table.delete": {"tableId", "year", "semester"},
    "table.add_subject": {"tableId", "year", "semester", "subjectId"},
    "table.remove_subject": {"tableId", "year", "semester", "subjectId"},
    "table.add_custom": {"tableId", "year", "semester", "name", "professor", "meetings"},
    "table.edit_custom": {"tableId", "year", "semester", "subjectId", "name", "professor", "meetings"},
    "article.create": {"boardId", "title", "text", "anonymous", "question", "categoryId"},
    "article.edit": {"boardId", "articleId", "title", "text", "anonymous", "question", "categoryId"},
    "article.delete": {"boardId", "articleId"},
    "article.like": {"boardId", "articleId"},
    "article.scrap": {"boardId", "articleId"},
    "article.unscrap": {"boardId", "articleId"},
    "comment.create": {"boardId", "articleId", "commentId", "text", "anonymous"},
    "comment.delete": {"boardId", "articleId", "commentId"},
    "comment.like": {"boardId", "articleId", "commentId"},
}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def numeric(value, *, signed=False) -> str:
    if isinstance(value, bool) or not re.fullmatch(r"-?[1-9]\d*" if signed else r"[1-9]\d*", str(value)):
        raise EverytimeError("대상 ID를 확인해 주세요.")
    return str(value)


def ticks(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d|24:00", value):
        raise EverytimeError("시간은 HH:MM으로 입력해 주세요.")
    hour, minute = map(int, value.split(":"))
    if minute % 5:
        raise EverytimeError("에타 시간표는 5분 단위로 입력해 주세요.")
    return hour * 12 + minute // 5


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict) or spec.get("action") not in FIELDS:
        raise EverytimeError("지원하는 변경 작업을 선택해 주세요.")
    action = spec["action"]
    if set(spec) - FIELDS[action] - {"action"}:
        raise EverytimeError("지원하지 않는 입력 항목이 있습니다. 인증값이나 임의 요청 주소를 넣지 마세요.")
    spec = dict(spec)
    for key in ("tableId", "subjectId", "boardId", "articleId", "commentId", "categoryId"):
        if key in spec:
            spec[key] = numeric(spec[key], signed=(key == "subjectId"))
    if action.startswith("table."):
        if not re.fullmatch(r"\d{4}", str(spec.get("year", ""))) or spec.get("semester") not in {"1", "2", "여름", "겨울"}:
            raise EverytimeError("변경할 시간표의 연도·학기를 지정해 주세요.")
        spec["year"] = str(spec["year"])
        if action != "table.create" and "tableId" not in spec:
            raise EverytimeError("시간표 ID가 필요합니다.")
        if action in {"table.add_subject", "table.remove_subject", "table.edit_custom"} and "subjectId" not in spec:
            raise EverytimeError("과목 또는 일정 ID가 필요합니다.")
        if action == "table.add_subject" and int(spec["subjectId"]) < 0:
            raise EverytimeError("기존 직접 추가 일정을 다른 시간표에 복사하는 기능은 지원하지 않습니다.")
        if action == "table.edit_custom" and int(spec["subjectId"]) >= 0:
            raise EverytimeError("학교 개설 과목의 시간을 직접 바꾸지 않습니다. 직접 추가 일정만 수정할 수 있어요.")
    else:
        if "boardId" not in spec or (action != "article.create" and "articleId" not in spec):
            raise EverytimeError("게시판·글 ID가 필요합니다.")
        if action in {"comment.delete", "comment.like"} and "commentId" not in spec:
            raise EverytimeError("댓글 ID가 필요합니다.")
    for key in ("name", "title", "text", "professor"):
        if key in spec and (not isinstance(spec[key], str) or "\x00" in spec[key] or len(spec[key]) > 10000):
            raise EverytimeError("입력 내용을 확인해 주세요. 각 문자열은 최대 10,000자입니다.")
    required_text = ({"name"} if action in {"table.create", "table.rename", "table.add_custom", "table.edit_custom"} else
                     {"text"} if action in {"article.create", "article.edit", "comment.create"} else set())
    if any(not spec.get(key, "").strip() for key in required_text):
        raise EverytimeError("이름 또는 내용을 비워둘 수 없습니다.")
    if "name" in spec and "/" in spec["name"]:
        raise EverytimeError("시간표·일정 이름에 / 기호를 사용할 수 없습니다.")
    for key in ("anonymous", "question"):
        if key in spec and not isinstance(spec[key], bool):
            raise EverytimeError("익명·질문 설정은 true 또는 false여야 합니다.")
    if action in {"article.create", "comment.create"}:
        spec.setdefault("anonymous", True)
    if action == "article.create":
        spec.setdefault("question", False)
    if action in {"table.add_custom", "table.edit_custom"}:
        meetings = spec.get("meetings")
        if not isinstance(meetings, list) or not 1 <= len(meetings) <= 50:
            raise EverytimeError("직접 추가 일정에는 1~50개 시간 블록이 필요합니다.")
        normalized = []
        for item in meetings:
            if not isinstance(item, dict) or set(item) - {"dayIndex", "startTime", "endTime", "room"}:
                raise EverytimeError("일정은 dayIndex·startTime·endTime·room으로 입력해 주세요.")
            day = item.get("dayIndex")
            if type(day) is not int or not 0 <= day <= 6:
                raise EverytimeError("요일은 월요일 0부터 일요일 6까지입니다.")
            start, end = ticks(item.get("startTime")), ticks(item.get("endTime"))
            room = item.get("room", "")
            if start >= end or not isinstance(room, str) or len(room) > 200 or "\x00" in room:
                raise EverytimeError("시작·종료 시간과 장소를 확인해 주세요.")
            if any(day == m["dayIndex"] and start < ticks(m["endTime"]) and end > ticks(m["startTime"]) for m in normalized):
                raise EverytimeError("추가 일정의 시간 블록이 서로 겹칩니다.")
            normalized.append({"dayIndex": day, "startTime": item["startTime"], "endTime": item["endTime"], "room": room})
        spec["meetings"] = normalized
        spec.setdefault("professor", "")
    return spec


def snapshot(client: Client, spec: dict) -> dict:
    action = spec["action"]
    if action.startswith("table."):
        term = {"year": spec["year"], "semester": spec["semester"]}
        root = xml_response(client.post("/find/timetable/table/list/semester", term, referer=f"{PUBLIC_URL}/timetable"))
        tables = [{key: node.get(key) for key in ("id", "name", "is_primary", "updated_at")} for node in root.findall("table")]
        if action == "table.create":
            terms = xml_response(client.post("/find/timetable/subject/semester/list", {}, referer=f"{PUBLIC_URL}/timetable"))
            if not any(node.get("year") == term["year"] and node.get("semester") == term["semester"] for node in terms.findall("semester")):
                raise EverytimeError("에타에 등록된 학기가 아닙니다.")
            if any(table["name"] == spec["name"].strip() for table in tables):
                raise EverytimeError("같은 이름의 시간표가 이미 있습니다.")
            return {"tables": tables}
        if not any(table["id"] == spec["tableId"] for table in tables):
            raise EverytimeError("내 시간표 목록에서 대상을 찾지 못했어요.")
        root = xml_response(client.post("/find/timetable/table", {"id": spec["tableId"]}, referer=f"{PUBLIC_URL}/timetable"))
        table = root.find("table")
        if table is None or table.get("id") != spec["tableId"] or table.get("year") != term["year"] or table.get("semester") != term["semester"]:
            raise EverytimeError("시간표 대상이나 학기가 달라요.")
        result = parse_timetable(root, term=term, courses=[])
        result["tables"] = tables
        if action in {"table.remove_subject", "table.edit_custom"} and not any(item["subjectId"] == spec["subjectId"] for item in result["items"]):
            raise EverytimeError("시간표에 해당 과목·일정이 없습니다.")
        if action == "table.add_subject":
            if any(item["subjectId"] == spec["subjectId"] for item in result["items"]):
                raise EverytimeError("이미 시간표에 들어있는 과목입니다.")
            # The catalogue cannot search by subject ID; prepare() verifies the exact offered record.
        return result
    referer = f"{PUBLIC_URL}/{spec['boardId']}"
    params = {"id": spec.get("articleId", spec["boardId"]), "moiminfo": "true"}
    path = "/find/board/comment/list" if "articleId" in spec else "/find/board/article/list"
    params.update({"limit_num": -1, "articleInfo": "true"} if "articleId" in spec else {"limit_num": 1, "start_num": 0})
    root = xml_response(client.post(path, params, referer=referer))
    moim = root.find("moim")
    if moim is None or moim.get("id") != spec["boardId"] or moim.get("is_accessible") != "1":
        raise EverytimeError("게시판 대상·접근 권한을 확인하지 못했어요.")
    board = {key: moim.get(key) for key in ("id", "name", "type", "is_writable", "is_commentable", "is_questionable",
                                          "priv_anonym", "priv_comment_anonym", "is_commercial", "placeholder")}
    article = root.find("article") if "articleId" in spec else None
    if "articleId" in spec and (article is None or article.get("id") != spec["articleId"]):
        raise EverytimeError("대상 글을 확인하지 못했어요.")
    if action == "article.edit":
        if article.get("user_id") is None or article.get("is_question") is None:
            raise EverytimeError("기존 익명·질문 설정을 확인하지 못했어요. 공식 웹에서 확인해 주세요.")
        spec.setdefault("anonymous", article.get("user_id") == "0")
        spec.setdefault("question", article.get("is_question") == "1")
    if action in {"article.create", "article.edit"}:
        if board["is_writable"] != "1" or (board["type"] == "2" and not spec.get("title", "").strip()):
            raise EverytimeError("글을 작성할 수 없거나 필요한 제목이 비어있어요.")
        if spec["anonymous"] and board["priv_anonym"] == "1":
            raise EverytimeError("익명 작성을 허용하지 않는 게시판입니다.")
        if spec["question"] and board["is_questionable"] != "1":
            raise EverytimeError("질문 글을 허용하지 않는 게시판입니다.")
    if action == "comment.create" and (board["is_commentable"] != "1" or (spec["anonymous"] and board["priv_comment_anonym"] == "1")):
        raise EverytimeError("현재 설정으로 댓글을 작성할 수 없는 게시판입니다.")
    if "categoryId" in spec and not any(node.get("id") == spec["categoryId"] for node in root.findall(".//category")):
        raise EverytimeError("게시판 분류를 확인하지 못했어요.")
    if action == "article.create":
        return {"board": board}
    allowed = ("id", "title", "text", "raw_title", "raw_text", "created_at", "is_mine", "is_question", "is_scrapped", "comment_count")
    attrs = {key: article.get(key) for key in allowed}
    attrs["anonymous"] = article.get("user_id") == "0"
    if action in {"article.edit", "article.delete"} and attrs["is_mine"] != "1":
        raise EverytimeError("내가 쓴 글만 수정·삭제할 수 있어요.")
    if action == "article.like" and attrs["is_mine"] == "1":
        raise EverytimeError("내 글을 공감할 수 없습니다.")
    if action in {"article.edit", "article.delete"} and attrs["is_question"] == "1" and int(attrs["comment_count"] or 0) > 0:
        raise EverytimeError("댓글이 달린 질문 글은 수정·삭제할 수 없습니다.")
    if action == "article.edit" and article.findall("attach"):
        raise EverytimeError("첨부가 있는 글은 첨부 보존 검증 전까지 공식 웹에서 수정해 주세요.")
    comments = [{key: node.get(key) for key in ("id", "parent_id", "text", "is_mine")} for node in root.findall("comment")]
    if "commentId" in spec:
        comment = next((item for item in comments if item["id"] == spec["commentId"]), None)
        if comment is None or (action == "comment.delete" and comment["is_mine"] != "1"):
            raise EverytimeError("대상 댓글 또는 삭제 권한을 확인하지 못했어요.")
        if action == "comment.like" and comment["is_mine"] == "1":
            raise EverytimeError("내 댓글을 공감할 수 없습니다.")
    return {"board": board, "article": attrs, "comments": comments, "url": f"{referer}/v/{spec['articleId']}"}


def requests(spec: dict, before: dict) -> list[dict]:
    action = spec["action"]
    def request(path, params):
        return {"path": path, "params": params, "referer": f"{PUBLIC_URL}/timetable" if action.startswith("table.") else f"{PUBLIC_URL}/{spec['boardId']}"}
    if action == "table.create":
        return [request("/save/timetable/table", {"data": f"{spec['name'].strip()}/{spec['year']}/{spec['semester']}/0/"})]
    if action in {"table.rename", "table.primary", "table.delete"}:
        path, params = {
            "table.rename": ("/update/timetable/table/name", {"data": f"{spec['tableId']}/{spec.get('name', '').strip()}"}),
            "table.primary": ("/update/timetable/table/primary", {"id": spec["tableId"]}),
            "table.delete": ("/remove/timetable/table", {"id": spec["tableId"]}),
        }[action]
        return [request(path, params)]
    if action.startswith("table."):
        ids = [item["subjectId"] for item in before["items"]]
        operations = []
        if action == "table.remove_subject":
            ids.remove(spec["subjectId"])
        elif action == "table.add_subject":
            ids.append(spec["subjectId"])
        else:
            custom = {"name": spec["name"].strip(), "professor": spec["professor"].strip(),
                      "time_place": [{"day": item["dayIndex"], "starttime": ticks(item["startTime"]),
                                      "endtime": ticks(item["endTime"]), "place": item["room"].strip()} for item in spec["meetings"]]}
            path = "/save/timetable/subject/custom" if action == "table.add_custom" else "/update/timetable/subject/custom"
            params = {"data": json.dumps(custom, ensure_ascii=False)}
            if action == "table.edit_custom":
                params["subject_id"] = str(-int(spec["subjectId"]))
                ids.remove(spec["subjectId"])
            operations.append(request(path, params))
            ids.append("-{customId}")
        data = "/".join([before["timetableName"], spec["year"], spec["semester"], spec["tableId"], *ids, ""])
        operations.append(request("/save/timetable/table", {"data": data}))
        return operations
    if action in {"article.create", "article.edit"}:
        params = {"id": spec["boardId"], "text": spec["text"], "is_anonym": int(spec["anonymous"]), "is_question": int(spec["question"])}
        for source, target in (("title", "title"), ("articleId", "article_id"), ("categoryId", "category_id")):
            if source in spec:
                params[target] = spec[source]
        return [request("/save/board/article", params)]
    if action == "comment.create":
        return [request("/save/board/comment", {"comment_id" if "commentId" in spec else "id": spec.get("commentId", spec["articleId"]),
                                               "text": spec["text"], "is_anonym": int(spec["anonymous"])})]
    path, params = {
        "article.delete": ("/remove/board/article", {"id": spec["articleId"]}),
        "article.like": ("/save/board/article/vote", {"id": spec["articleId"], "vote": 1}),
        "article.scrap": ("/save/board/article/scrap", {"article_id": spec["articleId"]}),
        "article.unscrap": ("/remove/board/article/scrap", {"article_id": spec["articleId"]}),
        "comment.delete": ("/remove/board/comment", {"id": spec.get("commentId")}),
        "comment.like": ("/save/board/comment/vote", {"id": spec.get("commentId"), "vote": 1}),
    }[action]
    return [request(path, params)]


def prepare(spec: dict, *, client: Client, db: Path | None = None) -> dict:
    spec = validate(spec)
    before = snapshot(client, spec)
    with LocalCache(db) as cache:
        if spec["action"] == "table.add_subject":
            # Only a verified current-term catalogue search result may be added.
            rows = cache.connection.execute("SELECT payload_json FROM cache_entries WHERE area='timetable'").fetchall()
            candidate = next((item for row in rows for payload in [json.loads(row[0])]
                              if str(payload.get("year")) == spec["year"] and payload.get("semester") == spec["semester"]
                              for item in payload.get("items", []) if item.get("subjectId") == spec["subjectId"] and item.get("classCode")), None)
            if candidate is None:
                raise EverytimeError("먼저 --search-subjects로 이번 학기 개설 과목을 확인해 주세요. 강의실 ID를 대신 쓰지 않습니다.")
            # Confirm the catalogue still lists exactly this offering before approving it.
            fresh = client.search_subjects(candidate["classCode"], year=spec["year"], semester=spec["semester"], field="code", limit=100)
            offered = next((item for item in fresh["items"] if item["subjectId"] == spec["subjectId"]), None)
            if offered is None:
                raise EverytimeError("현재 개설 목록에서 해당 과목을 확인하지 못했어요.")
            before["candidate"] = offered
        if spec["action"] in {"table.add_custom", "table.edit_custom", "table.add_subject"}:
            meetings = before["candidate"]["meetings"] if spec["action"] == "table.add_subject" else spec["meetings"]
            for meeting in meetings:
                for item in before["items"]:
                    if item["subjectId"] == spec.get("subjectId"):
                        continue
                    if any(meeting["dayIndex"] == old["dayIndex"] and ticks(meeting["startTime"]) < ticks(old["endTime"])
                           and ticks(meeting["endTime"]) > ticks(old["startTime"]) for old in item["meetings"]):
                        raise EverytimeError(f"{item['courseName']}과 시간이 겹칩니다. 겹치지 않게 조정해 주세요.")
        plan = {"source": "everytime", "area": "action", "planId": str(uuid.uuid4()), "status": "PREPARED", "spec": spec,
                "before": before, "requests": requests(spec, before), "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
                "message": "미리보기만 준비했습니다. 대상·내용·익명 여부를 사용자에게 보여주고 승인받은 뒤 적용하세요."}
        plan["confirmation"] = digest({key: plan[key] for key in ("spec", "before", "requests", "expiresAt")})
        cache.put("action", {"planId": plan["planId"]}, plan)
    return plan


class Rejected(EverytimeError):
    pass


def response_code(body: bytes, action: str) -> int:
    try:
        root = ET.fromstring(body)
        if root.tag != "response" or len(root):
            raise ValueError("not a scalar response")
        code = int((root.text or "").strip())
    except (ET.ParseError, ValueError) as error:
        raise EverytimeError("변경 응답을 확인하지 못했어요. 중복 실행하지 말고 실제 반영 여부를 확인해 주세요.") from error
    # Un-scrap's zero is a count; other confirmed operations return positive IDs/counts or 1.
    if code < 0 or (code == 0 and action != "article.unscrap"):
        raise Rejected(f"에타가 요청을 거절했어요 (응답 {code}). 권한·질문 글 제한·작성 간격을 확인해 주세요.")
    return code


def apply(plan_id: str, confirmation: str, *, client: Client, db: Path | None = None) -> dict:
    with LocalCache(db) as cache:
        plan = cache.get("action", {"planId": plan_id})
        if plan is None or plan.get("status") != "PREPARED" or confirmation != plan.get("confirmation"):
            raise EverytimeError("승인할 미리보기를 찾지 못했거나 이미 실행한 작업입니다.")
        if confirmation != digest({key: plan[key] for key in ("spec", "before", "requests", "expiresAt")}):
            raise EverytimeError("승인한 내용이 달라졌습니다. 미리보기를 다시 만들어 주세요.")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(plan["expiresAt"]):
            raise EverytimeError("미리보기 시간이 지났습니다. 현재 내용을 다시 확인해 주세요.")
        current = snapshot(client, plan["spec"])
        original = {key: value for key, value in plan["before"].items() if key != "candidate"}
        if digest(current) != digest(original):
            raise EverytimeError("미리보기 뒤 실제 내용이 바뀌었어요. 덮어쓰지 않고 중단합니다.")
        if "candidate" in plan["before"]:
            candidate = plan["before"]["candidate"]
            fresh = client.search_subjects(candidate["classCode"], year=plan["spec"]["year"], semester=plan["spec"]["semester"], field="code", limit=100)
            if not any(item == candidate for item in fresh["items"]):
                raise EverytimeError("추가할 개설 과목이 바뀌었습니다. 다시 확인해 주세요.")
        cache.connection.execute("BEGIN IMMEDIATE")
        locked = cache.get("action", {"planId": plan_id})
        if locked.get("status") != "PREPARED":
            cache.connection.rollback()
            raise EverytimeError("이미 실행 중이거나 실행된 작업입니다. 다시 전송하지 않습니다.")
        plan["status"] = "SENDING"
        cache.put("action", {"planId": plan_id}, plan)  # Claim before any external request; never replay after a crash.
        results = []
        try:
            custom_id = None
            for operation in plan["requests"]:
                params = dict(operation["params"])
                if custom_id is not None and "data" in params and operation["path"] == "/save/timetable/table":
                    params["data"] = params["data"].replace("-{customId}", str(-custom_id))
                body = client.post(operation["path"], params, referer=operation["referer"])
                code = response_code(body, plan["spec"]["action"])
                results.append({"path": operation["path"], "responseCode": code})
                if operation["path"].endswith("/subject/custom"):
                    custom_id = code
                plan["results"] = results
                cache.put("action", {"planId": plan_id}, plan)
            plan["status"] = "APPLIED"
            plan["message"] = "에타가 변경 요청을 수락했어요. 캐시를 비워 다음 조회에서 실제 내용을 다시 확인합니다."
        except Rejected as error:
            plan["status"] = "PARTIAL" if results else "REJECTED"
            plan["message"] = str(error)
        except (EverytimeError, OSError) as error:
            plan["status"] = "UNKNOWN"
            plan["message"] = "전송 결과가 불확실해요. 같은 요청을 다시 보내지 말고 에타의 실제 내용을 확인해 주세요."
        finally:
            plan["results"] = results
            cache.put("action", {"planId": plan_id}, plan)
            areas = ("timetable", "setup") if plan["spec"]["action"].startswith("table.") else ("board",)
            for area in areas:
                cache.connection.execute("DELETE FROM cache_entries WHERE area=?", (area,))
            if plan["spec"]["action"].startswith("table."):
                cache.connection.execute("DELETE FROM cache_entries WHERE area='classroom' AND scope_json=?", ('{"view":"mine"}',))
            cache.connection.commit()
            if isinstance(client, Client):
                try:
                    save(client.cookies)
                except Exception:
                    plan["sessionWarning"] = "갱신된 세션을 보호 저장소에 보관하지 못했어요. 다음 조회에 재로그인이 필요할 수 있습니다."
                    cache.put("action", {"planId": plan_id}, plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="에타 변경 미리보기·승인 후 적용")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--prepare", type=Path, help="변경 요청 JSON 파일; 읽기 검증만 수행")
    target.add_argument("--apply", metavar="PLAN_ID", help="사용자가 승인한 미리보기 적용")
    target.add_argument("--list-actions", action="store_true")
    parser.add_argument("--confirm", help="승인한 미리보기의 confirmation 값")
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    try:
        if args.list_actions:
            result = FIELDS
            print(json.dumps({key: sorted(value) for key, value in result.items()}, ensure_ascii=False, indent=2))
            return 0
        if args.apply and not args.confirm:
            raise EverytimeError("적용하려면 사용자가 승인한 미리보기의 --confirm 값을 지정해 주세요.")
        spec = validate(json.loads(args.prepare.read_text(encoding="utf-8"))) if args.prepare else None
        client = Client(load())
        result = prepare(spec, client=client, db=args.db) if args.prepare else apply(args.apply, args.confirm, client=client, db=args.db)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"PREPARED", "APPLIED"} else 1
    except (EverytimeError, OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        message = str(error) if isinstance(error, EverytimeError) else "입력 파일·저장 위치·로그인 연결을 확인해 주세요."
        print(json.dumps({"source": "everytime", "partial": True, "message": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
