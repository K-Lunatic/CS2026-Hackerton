#!/usr/bin/env python3
"""Deterministic, cache-first Everytime reader for the Everytime Skill."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import hashlib
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.parse import urlencode, urlsplit, unquote
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener
from pathlib import Path
from xml.etree import ElementTree

from everytime_cache import LocalCache
from session_store import load

API_URL = "https://api.everytime.kr/find/board/article/list"
PUBLIC_URL = "https://everytime.kr"


class EverytimeError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain(value: str | None) -> str:
    parser = _Text()
    parser.feed(html.unescape(value or ""))
    return " ".join(" ".join(parser.parts).split())


def meeting(day: int, start: int, end: int, room: str | None) -> dict:
    if not (0 <= day <= 6 and 0 <= start < end <= 288):
        raise EverytimeError("시간표의 요일·시간이 올바르지 않습니다.")
    def clock(ticks):
        minutes = ticks * 5
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    return {"day": "월화수목금토일"[day], "dayIndex": day, "startTime": clock(start), "endTime": clock(end), "room": room or None}


def parse_articles(payload: bytes, *, board_url: str, keywords: list[str], limit: int) -> dict[str, object]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise EverytimeError("게시판 응답을 읽지 못했습니다.") from error
    response = (root.text or "").strip() if root.tag == "response" else ""
    if response in {"0", "-100", "-300", "-400"}:
        raise EverytimeError("게시판 접근 권한이 없거나 로그인이 만료되었습니다.")
    moim = root.find(".//moim")
    items: list[dict[str, object]] = []
    for node in root.findall(".//article"):
        attrs = node.attrib
        title = plain(attrs.get("title"))
        content = plain(attrs.get("text"))
        haystack = f"{title}\n{content}".casefold()
        matched = [word for word in keywords if word.casefold() in haystack]
        if keywords and not matched:
            continue
        article_id = attrs.get("id")
        if not article_id:
            continue
        board_id = attrs.get("board_id") or board_url.rstrip("/").rsplit("/", 1)[-1]
        items.append({
            "articleId": article_id,
            "boardName": attrs.get("board_name") or (moim.attrib.get("name") if moim is not None else None),
            "title": title,
            "content": content,
            "publishedAt": attrs.get("created_at"),
            "isPinned": node.tag == "notice_article" or attrs.get("is_notice") == "true",
            "matchedKeywords": matched,
            "url": f"{PUBLIC_URL}/{board_id}/v/{article_id}",
        })
        if len(items) >= limit:
            break
    return {"source": "everytime", "area": "board", "items": items, "partial": False, "message": None}


class Client:
    def __init__(self, cookies: CookieJar) -> None:
        self.cookies = cookies
        self.opener = build_opener(HTTPCookieProcessor(cookies), NoRedirect())

    def post(self, path: str, params: dict, *, referer: str, json_body: bool = False) -> bytes:
        request = Request("https://api.everytime.kr" + path,
                          data=json.dumps(params).encode() if json_body else urlencode(params).encode(), headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json" if json_body else "application/x-www-form-urlencoded",
            "Origin": PUBLIC_URL,
            "Referer": referer,
        })
        try:
            with self.opener.open(request, timeout=20) as response:
                if urlsplit(response.geturl()).hostname != "api.everytime.kr":
                    raise EverytimeError("로그인 또는 추가 인증이 필요합니다. 공식 로그인 화면에서 확인해 주세요.")
                return response.read()
        except (URLError, OSError) as error:
            raise EverytimeError("에브리타임에 연결하지 못했어요. 잠시 후 다시 확인해 주세요.") from error

    def json_result(self, path: str, params: dict, *, referer: str, json_body: bool = False) -> dict:
        try:
            data = json.loads(self.post(path, params, referer=referer, json_body=json_body))
        except (ValueError, UnicodeError) as error:
            raise EverytimeError("응답을 읽지 못했어요. 로그인 상태나 서비스 변경을 확인해 주세요.") from error
        if not isinstance(data, dict) or data.get("status") != "success" or not isinstance(data.get("result"), dict):
            raise EverytimeError("정보를 확인하지 못했어요. 로그인 또는 접근 권한을 확인해 주세요.")
        return data["result"]

    def my_lectures(self) -> dict[str, object]:
        data = self.json_result("/v2/find/lecture/list/mine", {}, referer=f"{PUBLIC_URL}/lecture", json_body=True)
        if not isinstance(data.get("lectures"), list) or any(
                not isinstance(item, dict) or not item.get("id") or not item.get("name")
                or not isinstance(item.get("class") or {}, dict) for item in data["lectures"]):
            raise EverytimeError("강의실 내 강의를 확인하지 못했습니다. 로그인 상태를 확인하세요.")
        items = [{
            "courseId": str(item["id"]),
            "courseName": item["name"],
            "professor": item.get("professor"),
            "classCode": (item.get("class") or {}).get("code"),
            "url": f"{PUBLIC_URL}/lecture/view/{item['id']}",
        } for item in data["lectures"]]
        return {"source": "everytime", "area": "classroom", "items": items, "partial": False, "message": None}

    def initial_state(self, path: str) -> dict:
        try:
            with self.opener.open(Request(PUBLIC_URL + path, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
                body = response.read().decode("utf-8")
            match = re.search(r'<script[^>]+id="__INITIAL_STATE__"[^>]*>(.*?)</script>', body, re.S)
            state = json.loads(unquote(match[1])) if match else None
            if not isinstance(state, dict) or state.get("isLogged") is not True:
                raise ValueError("unauthenticated page")
            return state
        except (URLError, OSError, ValueError) as error:
            raise EverytimeError("강의실 정보를 확인하지 못했어요. 로그인 상태를 확인해 주세요.") from error

    def course(self, course_id: str) -> dict:
        if not re.fullmatch(r"[1-9]\d*", str(course_id)):
            raise EverytimeError("강의실 과목 ID를 확인해 주세요.")
        lecture = self.initial_state(f"/lecture/view/{course_id}").get("lecture")
        if not isinstance(lecture, dict) or str(lecture.get("id")) != str(course_id) or not lecture.get("name"):
            raise EverytimeError("해당 과목을 확인하지 못했어요.")
        return {"courseId": str(course_id), "courseName": lecture["name"], "professor": lecture.get("professor"),
                "url": f"{PUBLIC_URL}/lecture/view/{course_id}"}

    def search_lectures(self, keyword: str, *, field: str = "name", limit: int = 20) -> dict:
        if not keyword.strip() or field not in {"name", "professor"} or not 1 <= limit <= 100:
            raise EverytimeError("과목명 또는 교수명과 검색 개수(1~100)를 확인해 주세요.")
        campus = self.initial_state("/lecture/search").get("campusId")
        if campus is None:
            raise EverytimeError("현재 캠퍼스를 확인하지 못했어요.")
        data = self.json_result("/find/lecture/list/keyword", {"campusId": campus, "field": field,
                                "keyword": keyword.strip(), "limit": limit, "offset": 0}, referer=f"{PUBLIC_URL}/lecture/search")
        lectures = data.get("lectures")
        if not isinstance(lectures, list) or any(not isinstance(item, dict) or not item.get("id") or not item.get("name") for item in lectures):
            raise EverytimeError("과목 검색 결과를 읽지 못했어요.")
        return {"source": "everytime", "area": "classroom", "campusId": campus,
                "items": [{"courseId": str(item["id"]), "courseName": item["name"], "professor": item.get("professor"),
                           "rate": item.get("rate"), "url": f"{PUBLIC_URL}/lecture/view/{item['id']}"} for item in lectures[:limit]],
                "partial": len(lectures) >= limit, "message": "강의평 등록 과목이며, 해당 학기 개설 여부와는 다릅니다."}

    def search_subjects(self, keyword: str, *, year: str, semester: str, field: str = "name", limit: int = 20) -> dict:
        if not keyword.strip() or field not in {"name", "professor", "code", "place"} or not re.fullmatch(r"\d{4}", str(year)) or semester not in {"1", "2", "여름", "겨울"} or not 1 <= limit <= 100:
            raise EverytimeError("검색어·학기·개수(1~100)를 확인해 주세요.")
        campus = self.initial_state("/lecture/search").get("campusId")
        if campus is None:
            raise EverytimeError("현재 캠퍼스를 확인하지 못했어요.")
        filters = xml_response(self.post("/find/timetable/subject/filter/list", {"year": year, "semester": semester}, referer=f"{PUBLIC_URL}/timetable"))
        if not any(node.get("id") == str(campus) for node in filters.findall("campus")):
            raise EverytimeError("해당 학기의 현재 캠퍼스 개설 목록을 확인하지 못했어요.")
        params = {"campusId": campus, "year": year, "semester": semester, "limitNum": limit, "startNum": 0,
                  "keyword": json.dumps({"type": field, "keyword": keyword.strip()}, ensure_ascii=False)}
        root = xml_response(self.post("/find/timetable/subject/list", params, referer=f"{PUBLIC_URL}/timetable"))
        items = []
        for node in root.findall("subject")[:limit]:
            if not node.get("id") or not node.get("name"):
                raise EverytimeError("개설 과목 형식이 달라 확인하지 못했어요.")
            try:
                meetings = [meeting(int(block.attrib["day"]), int(block.attrib["start"]), int(block.attrib["end"]), block.get("place"))
                            for block in node.findall("timeplace")]
            except (ValueError, KeyError) as error:
                raise EverytimeError("개설 과목의 시간 블록을 읽지 못했어요.") from error
            items.append({"subjectId": node.get("id"), "courseId": node.get("lectureId") if node.get("lectureId") != "0" else None,
                          "courseName": node.get("name"), "professor": node.get("professor"), "classCode": node.get("code"),
                          "credits": node.get("credit"), "time": plain(node.get("time")), "room": node.get("place"), "meetings": meetings,
                          "url": f"{PUBLIC_URL}/timetable/syllabus/{node.get('id')}"})
        return {"source": "everytime", "area": "timetable", "year": year, "semester": semester,
                "items": items, "partial": len(items) >= limit, "message": "에타 개설 목록입니다. 실제 수강신청은 학교 시스템에서 확인하세요."}

    def reviews(self, course: dict) -> dict:
        url = course["url"]
        data = self.json_result("/find/lecture/article/list", {
            "lectureId": course["courseId"], "limit": 20, "offset": 0, "sort": "id",
        }, referer=url)
        articles, rate = data.get("articles"), data.get("rate")
        if not isinstance(articles, list) or not isinstance(rate, dict) or not isinstance(rate.get("count"), int):
            raise EverytimeError("강의평 형식이 달라 내용을 확인하지 못했어요.")
        if any(not isinstance(item, dict) or not item.get("id") or not isinstance(item.get("text"), str) for item in articles):
            raise EverytimeError("일부 강의평 내용을 읽지 못했어요.")
        items = [{"reviewId": str(item["id"]), "year": item.get("year"), "semester": item.get("semester"),
                  "text": plain(item["text"]), "rate": item.get("rate"), "posvote": item.get("posvote"),
                  "sourceType": "STUDENT_REPORT", "url": url} for item in articles[:20]]
        overview = self.json_result("/find/lecture", {"id": course["courseId"]}, referer=url).get("review")
        if not isinstance(overview, dict):
            raise EverytimeError("강의평 집계를 확인하지 못했어요.")
        statistics = []
        for group in ("subjectiveDetails", "objectiveDetails"):
            details = overview.get(group)
            if not isinstance(details, list):
                raise EverytimeError("강의평 집계 형식이 달라 확인하지 못했어요.")
            for detail in details:
                if not isinstance(detail, dict) or not isinstance(detail.get("items"), list) or not isinstance(detail.get("count"), int):
                    raise EverytimeError("강의평 집계 항목을 읽지 못했어요.")
                if any(not isinstance(item, dict) or not isinstance(item.get("count"), int) or not isinstance(item.get("text"), str) for item in detail["items"]):
                    raise EverytimeError("강의평 집계 값을 읽지 못했어요.")
                statistics.append({"name": detail.get("name"), "count": detail["count"],
                                   "items": [{"text": item["text"], "count": item["count"]} for item in detail["items"]]})
        return {"source": "everytime", "area": "classroom", "courseId": course["courseId"],
                "courseName": course["courseName"], "professor": course.get("professor"),
                "rate": {"average": rate.get("average"), "count": rate["count"]}, "items": items, "statistics": statistics,
                "partial": len(items) < rate["count"], "message": "최신 강의평 최대 20개를 저장합니다."}

    def timetable(self, courses: list[dict]) -> dict:
        def read(path, params):
            return xml_response(self.post(path, params, referer=f"{PUBLIC_URL}/timetable"))
        semesters = read("/find/timetable/subject/semester/list", {}).findall(".//semester")
        today = datetime.now(timezone(timedelta(hours=9))).date()
        try:
            # 공식 웹과 동일하게, 종료일이 지나지 않은 학기 중 가장 가까운 것을 선택한다.
            terms = [(datetime.fromisoformat(item.attrib["end_date"]).date(), item) for item in semesters]
            current = min((pair for pair in terms if pair[0] >= today), key=lambda pair: pair[0])[1]
            term = {"year": current.attrib["year"], "semester": current.attrib["semester"]}
        except (ValueError, KeyError) as error:
            raise EverytimeError("현재 학기를 확인하지 못했어요. 학기 목록을 확인해 주세요.") from error
        tables = read("/find/timetable/table/list/semester", term).findall(".//table")
        if not tables:
            return {"source": "everytime", "area": "timetable", **term, "items": [], "conflicts": [],
                    "partial": False, "message": "이번 학기에 저장된 시간표가 없습니다.", "url": f"{PUBLIC_URL}/timetable"}
        table = next((item for item in tables if item.get("is_primary") == "1"), tables[0])
        if not table.get("id"):
            raise EverytimeError("시간표 식별자를 확인하지 못했어요.")
        root = read("/find/timetable/table", {"id": table.get("id")})
        return parse_timetable(root, term=term, courses=courses)

    def board(self, board_id: str, *, limit: int, keywords: list[str]) -> dict[str, object]:
        data = urlencode({"id": board_id, "limit_num": limit, "start_num": 0, "moiminfo": "true"}).encode()
        request = Request(API_URL, data=data, headers={
            "User-Agent": "Mozilla/5.0 EverytimeSkill/1.0",
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": PUBLIC_URL,
            "Referer": f"{PUBLIC_URL}/{board_id}",
        })
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read()
        except Exception as error:
            raise EverytimeError("에브리타임 게시판을 불러오지 못했습니다.") from error
        return parse_articles(body, board_url=f"{PUBLIC_URL}/{board_id}", keywords=keywords, limit=limit)


def xml_response(payload: bytes) -> ElementTree.Element:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise EverytimeError("시간표 응답을 읽지 못했어요.") from error
    if root.tag != "response" or (root.text or "").strip():
        raise EverytimeError("시간표를 확인하지 못했어요. 로그인 또는 접근 권한을 확인해 주세요.")
    return root


def parse_timetable(root: ElementTree.Element, *, term: dict, courses: list[dict]) -> dict:
    table = root.find("table")
    if table is None or not table.get("id") or table.get("is_deleted") == "1":
        raise EverytimeError("저장된 시간표 내용을 확인하지 못했어요.")
    url = f"{PUBLIC_URL}/timetable/{term['year']}/{term['semester']}/{table.get('id')}"
    items, slots = [], []
    try:
        for subject in table.findall("subject"):
            def value(name):
                node = subject.find(name)
                return node.get("value", "") if node is not None else ""
            name, professor, code = value("name"), value("professor"), value("internal")
            if not subject.get("id") or not name:
                raise ValueError("missing subject")
            matches = [course for course in courses if code and str(course.get("classCode")) == code
                       and course["courseName"] == name and (course.get("professor") or "") == professor]
            meetings = []
            for block in subject.findall("time/data"):
                day, start, end = (int(block.attrib[key]) for key in ("day", "starttime", "endtime"))
                meetings.append(meeting(day, start, end, block.get("place") or value("place")))
                slots.append((subject.get("id"), name, day, start, end))
            items.append({"subjectId": subject.get("id"), "courseId": matches[0]["courseId"] if len(matches) == 1 else None,
                          "classCode": code or None, "courseName": name, "professor": professor or None,
                          "credits": float(value("credit")) if value("credit") else None,
                          "isCustom": not bool(code), "isClosed": value("closed") == "1",
                          "meetings": meetings, "url": url})
    except (ValueError, KeyError) as error:
        raise EverytimeError("시간표의 일부 과목이나 시간을 읽지 못했어요.") from error
    # ponytail: 개인 시간표의 적은 블록만 비교한다. 대규모 일정이면 구간 정렬로 바꾼다.
    conflicts = [{"subjectIds": [a[0], b[0]], "courseNames": [a[1], b[1]], "day": "월화수목금토일"[a[2]]}
                 for i, a in enumerate(slots) for b in slots[i + 1:]
                 if a[0] != b[0] and a[2] == b[2] and max(a[3], b[3]) < min(a[4], b[4])]
    return {"source": "everytime", "area": "timetable", **term, "tableId": table.get("id"),
            "timetableName": table.get("name"), "items": items, "conflicts": conflicts,
            "partial": False, "message": None, "url": url}


REVIEW_TOPICS = {
    "시험 방식": ("시험", "중간", "기말", "객관식", "주관식", "서술", "오픈북", "퀴즈"),
    "공부 방법": ("공부", "복습", "교재", "교안", "피피티", "ppt", "족보", "기출", "암기", "실습", "코딩"),
    "과제·팀 활동": ("과제", "팀플", "조모임", "프로젝트", "발표", "보고서"),
    "출석": ("출석", "출결", "지각", "결석"),
    "평가·성적": ("성적", "학점", "평가", "점수", "채점", "가산점"),
    "수업 방식·부담": ("수업", "설명", "진도", "난이도", "부담", "시간", "질문", "피드백"),
}


def review_guide(reviews: dict, purpose: str) -> dict:
    if purpose not in {"study", "enrollment"}:
        raise EverytimeError("강의평 용도는 study 또는 enrollment여야 합니다.")
    topics = ("시험 방식", "공부 방법", "평가·성적", "과제·팀 활동") if purpose == "study" else tuple(REVIEW_TOPICS)
    items = []
    for review in reviews["items"]:
        text = review["text"]
        matches = {topic: [term for term in REVIEW_TOPICS[topic] if term in text.casefold()] for topic in topics}
        # ponytail: 키워드는 근거를 찾는 색인일 뿐, 긍정/부정이나 공부법을 판정하지 않는다.
        items.append({**review, "sourceType": "STUDENT_REPORT", "topics": [key for key, value in matches.items() if value],
                      "matchedTerms": {key: value for key, value in matches.items() if value}})
    clean = {key: value for key, value in reviews.items() if key not in {"cacheHit", "cachedAt", "sourceUrl"}}
    return {"source": "everytime", "area": "classroom", "courseId": reviews.get("courseId"),
            "courseName": reviews.get("courseName"), "professor": reviews.get("professor"), "purpose": purpose,
            "sourceHash": hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "rate": reviews.get("rate"), "statistics": reviews.get("statistics", []), "sampleCount": len(items),
            "topics": [{"name": topic, "reviewIds": [item["reviewId"] for item in items if topic in item["topics"]]} for topic in topics],
            "items": items, "partial": reviews.get("partial", False), "sourceObservedAt": reviews.get("cachedAt"),
            "message": "근거 색인입니다. 과거 수강생 후기이며 현재 시험 범위·성적·개설 여부를 보장하지 않습니다."}


def initialize(*, db: Path | None = None, max_age: int = 900, client: Client | None = None,
               progress=None) -> dict:
    """Cache each successful step immediately; retry missing/expired steps only."""
    if max_age < -1:
        raise EverytimeError("캐시 유효 시간은 -1 또는 0 이상의 초여야 합니다.")
    progress = progress or (lambda message: print(message, file=sys.stderr, flush=True))
    stages = []
    report = {"source": "everytime", "area": "setup", "status": "RUNNING", "partial": False,
              "stages": stages, "courseCount": 0, "scheduleItemCount": 0, "meetingCount": 0, "reviewCount": 0}
    with LocalCache(db) as cache:
        def step(area, scope, title, fetch, url):
            nonlocal client
            progress(f"{title} 확인하고 있어요…")
            stage = {"area": area, "scope": scope, "title": title}
            try:
                result = cache.get(area, scope, max_age=max_age)
                if result is not None and scope.get("view") == "reviews" and "statistics" not in result:
                    result = None
                if result is None:
                    if client is None:
                        try:
                            client = Client(load())
                        except Exception as error:
                            raise EverytimeError("저장된 로그인이 없어요. 공식 로그인과 2차 인증을 먼저 완료해 주세요.") from error
                    result = cache.put(area, scope, fetch(client), source_url=url)
                stage.update(status="READY", cacheHit=result["cacheHit"], cachedAt=result["cachedAt"],
                             itemCount=len(result["items"]), partial=result.get("partial", False))
                progress(f"{title} 준비됐어요. ({stage['itemCount']}개{' · 저장된 내용' if result['cacheHit'] else ''})")
                return result
            except EverytimeError as error:
                stage.update(status="FAILED", message=str(error))
                report["partial"] = True
                progress(f"{title}: {error}")
                return None
            finally:
                stages.append(stage)
                cache.put("setup", {"view": "initial"}, report)

        lectures = step("classroom", {"view": "mine"}, "내 강의 목록", lambda c: c.my_lectures(), f"{PUBLIC_URL}/lecture")
        if lectures is not None:
            courses = lectures["items"]
            report["courseCount"] = len(courses)
            timetable = step("timetable", {"view": "current"}, "이번 학기 시간표",
                             lambda c: c.timetable(courses), f"{PUBLIC_URL}/timetable")
            if timetable is not None:
                report["scheduleItemCount"] = len(timetable["items"])
                report["meetingCount"] = sum(len(item["meetings"]) for item in timetable["items"])
            for index, course in enumerate(courses, 1):
                reviews = step("classroom", {"view": "reviews", "courseId": course["courseId"], "sort": "id", "limit": 20},
                               f"{course['courseName']} 강의평 ({index}/{len(courses)})", lambda c: c.reviews(course), course["url"])
                if reviews is not None:
                    report["reviewCount"] += len(reviews["items"])
                    for purpose in ("study", "enrollment"):
                        cache.put("classroom", {"view": "review-guide", "courseId": course["courseId"], "purpose": purpose},
                                  review_guide(reviews, purpose), source_url=course["url"])
        report["status"] = "PARTIAL" if report["partial"] else "READY"
        cache.put("setup", {"view": "initial"}, report)
    progress("기본 준비가 끝났어요. 이제 탭 없이 꺼내볼 수 있어요." if not report["partial"] else
             "일부 내용을 확인하지 못했어요. 저장된 내용은 유지하고, 다음 설정 때 빠진 부분을 다시 확인할게요.")
    return report


def sync_board(args: argparse.Namespace) -> dict[str, object]:
    if not 1 <= args.limit <= 100:
        raise EverytimeError("게시글 개수는 1~100 사이여야 합니다.")
    if args.max_age < -1:
        raise EverytimeError("캐시 유효 시간은 -1 또는 0 이상의 초여야 합니다.")
    board_id = args.board_url.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"\d+", board_id):
        raise EverytimeError("게시판 주소에서 숫자 게시판 ID를 확인하지 못했습니다.")
    scope = {"boardId": board_id, "keywords": args.keyword, "limit": args.limit}
    with LocalCache(args.db) as cache:
        cached = cache.get("board", scope, max_age=args.max_age)
        if cached is not None:
            return cached
        try:
            cookies = load()
        except Exception as error:
            raise EverytimeError("저장된 Everytime 세션이 없습니다. 공식 로그인과 2차 인증을 먼저 완료하세요.") from error
        result = Client(cookies).board(board_id, limit=args.limit, keywords=args.keyword)
        return cache.put("board", scope, result, source_url=f"{PUBLIC_URL}/{board_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="에브리타임 로컬 캐시 우선 조회")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--board-url")
    target.add_argument("--my-lectures", action="store_true", help="강의실의 내 강의 목록")
    target.add_argument("--setup", action="store_true", help="내 강의·시간표·과목별 최신 강의평을 준비")
    target.add_argument("--timetable", action="store_true", help="현재 학기의 대표 시간표와 일정")
    target.add_argument("--reviews", metavar="COURSE_ID", help="내 강의 과목의 최신 강의평 최대 20개")
    target.add_argument("--search-lectures", metavar="KEYWORD", help="현재 캠퍼스 강의평 과목 검색")
    target.add_argument("--search-subjects", metavar="KEYWORD", help="학기별 개설 과목 검색")
    parser.add_argument("--purpose", choices=("study", "enrollment"), help="강의평을 시험 준비/수강신청 근거로 정리")
    parser.add_argument("--field", choices=("name", "professor", "code", "place"), default="name")
    parser.add_argument("--year")
    parser.add_argument("--semester", choices=("1", "2", "여름", "겨울"))
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-age", type=int, default=900, help="캐시 유효 시간(초), -1이면 무제한")
    parser.add_argument("--db")
    args = parser.parse_args()
    args.db = Path(args.db) if args.db else None
    if args.max_age < -1:
        parser.error("--max-age는 -1 또는 0 이상의 초여야 합니다.")
    if args.purpose and not args.reviews:
        parser.error("--purpose는 --reviews와 함께 사용하세요.")
    try:
        if args.setup:
            result = initialize(db=args.db, max_age=args.max_age)
        elif args.search_lectures or args.search_subjects:
            if args.search_subjects and (not args.year or not args.semester):
                raise EverytimeError("개설 과목 검색에는 --year와 --semester를 지정해 주세요.")
            area = "classroom" if args.search_lectures else "timetable"
            scope = {"view": "search", "keyword": args.search_lectures or args.search_subjects,
                     "field": args.field, "year": args.year, "semester": args.semester, "limit": args.limit}
            with LocalCache(args.db) as cache:
                result = cache.get(area, scope, max_age=args.max_age)
                if result is None:
                    client = Client(load())
                    result = (client.search_lectures(args.search_lectures, field=args.field, limit=args.limit) if args.search_lectures else
                              client.search_subjects(args.search_subjects, year=args.year, semester=args.semester, field=args.field, limit=args.limit))
                    result = cache.put(area, scope, result, source_url=f"{PUBLIC_URL}/lecture/search" if args.search_lectures else f"{PUBLIC_URL}/timetable")
        elif args.my_lectures or args.timetable or args.reviews:
            if args.reviews and not args.reviews.isdigit():
                raise EverytimeError("과목 ID는 내 강의 목록에 있는 숫자여야 합니다.")
            area = "timetable" if args.timetable else "classroom"
            scope = ({"view": "current"} if args.timetable else
                     {"view": "reviews", "courseId": args.reviews, "sort": "id", "limit": 20} if args.reviews else {"view": "mine"})
            with LocalCache(args.db) as cache:
                result = cache.get(area, scope, max_age=args.max_age)
                if result is None or (args.purpose and "statistics" not in result):
                    client = Client(load())
                    courses = cache.get("classroom", {"view": "mine"}, max_age=args.max_age)
                    if courses is None:
                        courses = cache.put("classroom", {"view": "mine"}, client.my_lectures(), source_url=f"{PUBLIC_URL}/lecture")
                    if args.timetable:
                        result = client.timetable(courses["items"])
                        result = cache.put(area, scope, result, source_url=result["url"])
                    elif args.reviews:
                        course = next((item for item in courses["items"] if item["courseId"] == args.reviews), None)
                        if course is None:
                            course = client.course(args.reviews)
                        result = cache.put(area, scope, client.reviews(course), source_url=course["url"])
                    else:
                        result = courses
                if args.purpose:
                    guide = review_guide(result, args.purpose)
                    guide_scope = {"view": "review-guide", "courseId": args.reviews, "purpose": args.purpose}
                    cached = cache.get("classroom", guide_scope)
                    result = cached if cached and cached.get("sourceHash") == guide["sourceHash"] else cache.put(
                        "classroom", guide_scope, guide, source_url=result.get("sourceUrl"))
        else:
            result = sync_board(args)
    except (EverytimeError, OSError, RuntimeError) as error:
        message = str(error) if isinstance(error, EverytimeError) else "로컬 저장소를 열지 못했어요. 로그인 연결과 저장 위치를 확인해 주세요."
        print(json.dumps({"source": "everytime", "partial": True, "message": message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") == "PARTIAL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
