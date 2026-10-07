"""Offline smoke check for the parser and local cache."""
from __future__ import annotations

import tempfile
import copy
from unittest.mock import patch
from pathlib import Path

from everytime import Client, EverytimeError, initialize, parse_articles, parse_timetable, review_guide, xml_response
from everytime_cache import LocalCache
from everytime_actions import FIELDS, apply, prepare, requests, response_code, validate
from session_store import browser_cookie_jar
from urllib.request import Request


def main() -> None:
    jar = browser_cookie_jar([
        {"name": "fixture-session", "value": "fixture", "domain": ".everytime.kr", "expires": -1},
        {"name": "outside", "value": "fixture", "domain": "noteverytime.kr", "expires": -1},
    ])
    request = Request("https://everytime.kr/lecture")
    jar.add_cookie_header(request)
    assert request.get_header("Cookie") == "fixture-session=fixture"
    assert len(list(jar)) == 1
    xml = '''<response><moim name="정보게시판"/><article id="41" board_id="258604"
        title="자료구조 공지" text="&lt;b&gt;시험 범위&lt;/b&gt; 트리"
        created_at="2026-10-06T01:00:00+09:00" is_notice="true"/></response>'''.encode()
    result = parse_articles(xml, board_url="https://everytime.kr/258604", keywords=["트리"], limit=20)
    assert result["items"][0]["content"] == "시험 범위 트리"
    courses = [{"courseId": "1", "courseName": "자료구조", "professor": "교수", "classCode": "A",
                "url": "https://everytime.kr/lecture/view/1"}]
    table_xml = b'''<response><table id="9" name="current"><subject id="101">
      <internal value="A"/><name value="data structures"/><professor value="prof"/><credit value="3"/>
      <time><data day="0" starttime="126" endtime="144" place="301"/></time></subject>
      <subject id="-1"><name value="work"/><credit value="0"/>
      <time><data day="0" starttime="132" endtime="150"/></time></subject></table></response>'''
    mapped = [{**courses[0], "courseName": "data structures", "professor": "prof"}]
    timetable = parse_timetable(xml_response(table_xml), term={"year": "2026", "semester": "2"}, courses=mapped)
    assert timetable["items"][0]["courseId"] == "1"  # Subject IDs are not lecture IDs.
    assert timetable["items"][0]["meetings"][0]["startTime"] == "10:30"
    assert timetable["items"][1]["isCustom"] and timetable["items"][1]["courseId"] is None
    assert len(timetable["conflicts"]) == 1
    calls = []
    def post(path, params, **kwargs):
        calls.append((path, params))
        if path.endswith("semester/list"):
            return b'''<response><semester year="2999" semester="2" end_date="2999-12-01"/>
              <semester year="3000" semester="1" end_date="3000-06-01"/>
              <semester year="2000" semester="1" end_date="2000-06-01"/></response>'''
        if path.endswith("list/semester"):
            return b'<response><table id="9" is_primary="1"/></response>'
        return table_xml
    reader = Client(jar)
    reader.post = post
    assert reader.timetable(mapped)["year"] == "2999"
    assert calls[1][1] == {"year": "2999", "semester": "2"}
    for invalid in (b"<response>-1</response>", b"<html/>"):
        try:
            xml_response(invalid)
            raise AssertionError("must reject inaccessible timetable")
        except EverytimeError:
            pass
    try:
        parse_timetable(xml_response(table_xml.replace(b'starttime="126"', b'starttime="300"')),
                        term={"year": "2026", "semester": "2"}, courses=mapped)
        raise AssertionError("must reject invalid time")
    except EverytimeError:
        pass

    class FixtureClient:
        def __init__(self):
            self.calls = []
            self.fail = True

        def my_lectures(self):
            self.calls.append("courses")
            return {"items": courses}

        def timetable(self, course_list):
            self.calls.append("timetable")
            return timetable

        def reviews(self, course):
            self.calls.append("reviews")
            if self.fail:
                raise EverytimeError("temporary failure")
            return {**courses[0], "items": [{"reviewId": "9", "text": "시험 없음. 과제는 많다.", "year": 2025, "semester": "2"}],
                    "statistics": [], "partial": True}  # A bounded sample is not a setup failure.

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "cache.db"
        with LocalCache(db) as cache:
            cache.put("board", {"boardId": "258604"}, result)
            assert cache.get("board", {"boardId": "258604"}, max_age=60)["cacheHit"] is True
        client = FixtureClient()
        first = initialize(db=db, client=client, max_age=-1, progress=lambda _: None)
        assert first["status"] == "PARTIAL" and first["courseCount"] == 1
        client.calls.clear()
        client.fail = False
        retry = initialize(db=db, client=client, max_age=-1, progress=lambda _: None)
        assert retry["status"] == "READY" and retry["reviewCount"] == 1
        assert client.calls == ["reviews"]
        # A fully cached setup must not even need access to the OS session store.
        hit = initialize(db=db, max_age=-1, progress=lambda _: None)
        assert hit["status"] == "READY" and all(stage["cacheHit"] for stage in hit["stages"])
        with LocalCache(db) as cache:
            assert cache.get("setup", {"view": "initial"})["status"] == "READY"
            study = cache.get("classroom", {"view": "review-guide", "courseId": "1", "purpose": "study"})
            assert study["items"][0]["text"] == "시험 없음. 과제는 많다."  # Do not erase negation.
            assert study["items"][0]["sourceType"] == "STUDENT_REPORT"
            assert "시험 방식" in study["items"][0]["topics"]
            assert study["partial"] and study["statistics"] == []
        before = {"items": [{"subjectId": "101", "courseName": "course", "meetings": []}],
                  "timetableName": "table", "tables": []}
        candidate = {"subjectId": "102", "classCode": "B", "courseName": "other", "year": "2026"}
        with LocalCache(db) as cache:
            cache.put("timetable", {"view": "search"}, {"year": "2026", "semester": "2", "items": [candidate]})

        class WriterFixture:
            def __init__(self):
                self.calls = []
                self.fail = False
            def post(self, path, params, **kwargs):
                self.calls.append((path, params))
                if self.fail:
                    raise EverytimeError("timeout")
                return b"<response>7</response>"
            def search_subjects(self, *args, **kwargs):
                return {"items": [candidate]}

        writer = WriterFixture()
        with patch("everytime_actions.snapshot", side_effect=lambda *_: copy.deepcopy(before)):
            plan = prepare({"action": "table.rename", "tableId": "9", "year": "2026", "semester": "2", "name": "new"}, client=writer, db=db)
            assert writer.calls == []  # Preview is read-only.
            try:
                apply(plan["planId"], "wrong", client=writer, db=db)
                raise AssertionError("must require exact confirmation")
            except EverytimeError:
                pass
            result = apply(plan["planId"], plan["confirmation"], client=writer, db=db)
            assert result["status"] == "APPLIED" and writer.calls == [("/update/timetable/table/name", {"data": "9/new"})]
            try:
                apply(plan["planId"], plan["confirmation"], client=writer, db=db)
                raise AssertionError("must not send twice")
            except EverytimeError:
                pass
            assert len(writer.calls) == 1
            spec = {"action": "table.add_custom", "tableId": "9", "year": "2026", "semester": "2", "name": "work",
                    "meetings": [{"dayIndex": 1, "startTime": "09:00", "endTime": "10:00"}]}
            custom = prepare(spec, client=writer, db=db)
            custom_result = apply(custom["planId"], custom["confirmation"], client=writer, db=db)
            assert custom_result["status"] == "APPLIED"
            assert writer.calls[-1] == ("/save/timetable/table", {"data": "table/2026/2/9/101/-7/"})
            unknown = prepare(spec, client=writer, db=db)
            writer.fail = True
            assert apply(unknown["planId"], unknown["confirmation"], client=writer, db=db)["status"] == "UNKNOWN"
            call_count = len(writer.calls)
            try:
                apply(unknown["planId"], unknown["confirmation"], client=writer, db=db)
                raise AssertionError("must not retry uncertain writes")
            except EverytimeError:
                pass
            assert len(writer.calls) == call_count
            stale = prepare(spec, client=writer, db=db)
            before["timetableName"] = "changed elsewhere"
            try:
                apply(stale["planId"], stale["confirmation"], client=writer, db=db)
                raise AssertionError("must not overwrite changed timetable")
            except EverytimeError:
                pass
            assert len(writer.calls) == call_count
        assert response_code(b"<response>0</response>", "article.unscrap") == 0
        for body in (b"<html/>", b"<response>-1</response>", b"<response>0</response>"):
            try:
                response_code(body, "article.create")
                raise AssertionError("must not report unknown/refused response as success")
            except EverytimeError:
                pass
        for invalid in ({"action": "article.create", "boardId": "1", "text": "", "url": "https://evil.example"},
                        {"action": "article.create", "boardId": True, "text": "hi"},
                        {**spec, "meetings": [{"dayIndex": 0, "startTime": "10:31", "endTime": "11:00"}]},
                        {**spec, "action": "table.edit_custom", "subjectId": "101"}):
            try:
                validate(invalid)
                raise AssertionError("must reject unsafe/invalid action")
            except EverytimeError:
                pass
        # Compile every supported request contract without sending it.
        example = {"action": "", "tableId": "9", "year": "2026", "semester": "2", "subjectId": "101", "name": "name",
                   "boardId": "1", "articleId": "2", "commentId": "3", "title": "title", "text": "content", "professor": "",
                   "anonymous": True, "question": False, "categoryId": "4",
                   "meetings": [{"dayIndex": 0, "startTime": "09:00", "endTime": "10:00"}]}
        for action, fields in FIELDS.items():
            test_spec = {key: value for key, value in example.items() if key in fields}
            test_spec["action"] = action
            if action == "table.edit_custom":
                test_spec["subjectId"] = "-101"
            if action == "table.add_subject":
                test_spec["subjectId"] = "102"
            original = copy.deepcopy(before)
            if action == "table.edit_custom":
                original["items"][0]["subjectId"] = "-101"
            operations = requests(validate(test_spec), original)
            assert operations and all(op["path"].startswith(("/save/", "/update/", "/remove/")) for op in operations)
    print("everytime self-check: ok")


if __name__ == "__main__":
    main()
