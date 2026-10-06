"""Moodle HTML adapter for the KKU TLS pages observed by MoodleSession."""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor

from providers.moodle_session import MoodleSession, DownloadRestricted, check_download_url


class _LinkParser(HTMLParser):
    def __init__(self, *, only_activities: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.only_activities = only_activities
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.text: list[str] = []
        self.hidden = 0
        self.activity_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "li" and "activity" in (values.get("class") or "").split():
            self.activity_depth += 1
        if tag == "a" and self.href is None:
            if self.only_activities and self.activity_depth == 0:
                return
            self.href = values.get("href")
            self.text = []
        if self.href is not None and "accesshide" in (values.get("class") or "").split():
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if self.href is not None and tag == "span" and self.hidden:
            self.hidden -= 1
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join("".join(self.text).split())))
            self.href = None
            self.text = []
        if tag == "li" and self.activity_depth:
            self.activity_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.href is not None and not self.hidden:
            self.text.append(data)


class _ActivityParser(HTMLParser):
    """Collect each resource activity's visible text before fetching its file."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.current: dict[str, Any] | None = None
        self.li_depth = 0
        self.anchor_text: list[str] = []
        self.in_anchor = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "li":
            classes = (values.get("class") or "").split()
            if self.current is None and "activity" in classes:
                self.current = {"href": "", "title": "", "text": []}
                self.li_depth = 1
            elif self.current is not None:
                self.li_depth += 1
        if self.current is not None and tag == "a" and not self.current["href"]:
            self.current["href"] = values.get("href") or ""
            self.anchor_text = []
            self.in_anchor = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None and self.in_anchor:
            self.current["title"] = " ".join("".join(self.anchor_text).split())
            self.anchor_text = []
            self.in_anchor = False
        if tag == "li" and self.current is not None:
            self.li_depth -= 1
            if self.li_depth == 0:
                self.current["text"] = " ".join("".join(self.current["text"]).split())
                self.items.append(self.current)
                self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["text"].append(data)
            if self.in_anchor:
                self.anchor_text.append(data)


class MoodleTLSProvider:
    def __init__(self, session: MoodleSession):
        self.session = session
        self._courses: list[dict[str, Any]] | None = None
        self._course_pages: dict[str, str] = {}
        self._activity_links: dict[str, list[tuple[str, str]]] = {}

    def _parallel_courses(self, method, user, **kwargs):
        courses = self.get_courses(user)
        if len(courses) < 2 or not hasattr(self.session, 'fork'):
            return None
        # ponytail: at most four independent read sessions, not an unbounded request fan-out.
        def collect(course):
            provider = MoodleTLSProvider(self.session.fork())
            provider._courses = [course]
            provider._course_pages = self._course_pages.copy()
            provider._activity_links = self._activity_links.copy()
            items = getattr(provider, method)(user, **kwargs)
            return items, provider._course_pages, provider._activity_links
        with ThreadPoolExecutor(max_workers=4) as pool:
            result = []
            for items, pages, links in pool.map(collect, courses):
                self._course_pages.update(pages)
                self._activity_links.update(links)
                result.extend(items)
            return result

    @staticmethod
    def _links(html: str, *, only_activities: bool = False) -> list[tuple[str, str]]:
        parser = _LinkParser(only_activities=only_activities)
        parser.feed(html)
        return parser.links

    def get_courses(self, _user_id: str) -> list[dict[str, Any]]:
        if self._courses is not None:
            return [dict(course) for course in self._courses]
        links = self._links(self.session.get("/local/ubion/user/"))
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for href, title in links:
            match = re.search(r"/course/view\.php\?id=(\d+)", href)
            if not match or match.group(1) in seen:
                continue
            seen.add(match.group(1))
            result.append({"id": f"tls-course-{match.group(1)}", "externalId": match.group(1), "name": title or f"TLS course {match.group(1)}", "source": "tls"})
        self._courses = result
        return [dict(course) for course in result]

    def _course_page(self, course: dict[str, Any]) -> str:
        external_id = str(course["externalId"])
        if external_id not in self._course_pages:
            self._course_pages[external_id] = self.session.get(f"/course/view.php?id={external_id}")
        return self._course_pages[external_id]

    def _course_activity_links(self, course: dict[str, Any]) -> list[tuple[str, str]]:
        """Reuse parsed links for the lifetime of the cached course page."""
        external_id = str(course["externalId"])
        if external_id not in self._activity_links:
            self._activity_links[external_id] = self._links(self._course_page(course), only_activities=True)
        return self._activity_links[external_id]

    def get_assignments(self, user_id: str) -> list[dict[str, Any]]:
        parallel = self._parallel_courses('get_assignments', user_id)
        if parallel is not None:
            return parallel
        courses = self.get_courses(user_id)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for course in courses:
            for href, title in self._course_activity_links(course):
                match = re.search(r"/mod/assign/view\.php\?id=(\d+)", href)
                if not match or match.group(1) in seen:
                    continue
                seen.add(match.group(1))
                detail = self.session.get(f"/mod/assign/view.php?id={match.group(1)}")
                text = _plain_text(detail)
                due = _find_datetime(text, ("종료 일시", "마감일", "Due date"))
                status = "SUBMITTED" if "제출 완료" in text else "NOT_SUBMITTED" if re.search(r"제출 (?:안 함|하지 않음|되지 않음)", text) else "UNKNOWN"
                result.append({"id": f"tls-assignment-{match.group(1)}", "externalId": match.group(1), "courseId": course["id"], "title": title or f"TLS assignment {match.group(1)}", "dueAt": due, "submissionStatus": status, "source": "tls"})
        return result

    def get_lectures(self, user_id: str) -> list[dict[str, Any]]:
        parallel = self._parallel_courses('get_lectures', user_id)
        if parallel is not None:
            return parallel
        courses = self.get_courses(user_id)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for course in courses:
            html = self._course_page(course)
            durations = _vod_durations(html)
            availability = _vod_availability(html)
            for href, title in self._course_activity_links(course):
                match = re.search(r"/mod/vod/view\.php\?id=(\d+)", href)
                if not match or match.group(1) in seen:
                    continue
                seen.add(match.group(1))
                vod_id = match.group(1)
                viewer = self.session.get(f"/mod/vod/viewer.php?id={vod_id}")
                playtime = _find_playtime(viewer) or durations.get(vod_id, 0)
                progress = _find_number(viewer, "is_progress")
                complete = _find_number(viewer, "is_complete") == 1
                start, end = availability.get(vod_id, (None, None))
                result.append({"id": f"tls-lecture-{vod_id}", "externalId": vod_id, "courseId": course["id"], "title": title or f"TLS lecture {vod_id}", "durationSeconds": playtime, "watchedSeconds": round(playtime * progress / 100), "watchProgress": progress, "completed": complete, "availableFrom": start, "availableUntil": end, "source": "tls"})
        return result

    def get_notices(self, user_id: str) -> list[dict[str, Any]]:
        parallel = self._parallel_courses('get_notices', user_id)
        if parallel is not None:
            return parallel
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for course in self.get_courses(user_id):
            for href, title in self._course_activity_links(course):
                match = re.search(r"/mod/ubboard/view\.php\?id=(\d+)", href)
                if not match or "공지" not in title or match.group(1) in seen:
                    continue
                seen.add(match.group(1))
                board_html = self.session.get(f"/mod/ubboard/view.php?id={match.group(1)}")
                for article_href, _ in self._links(board_html):
                    article = re.search(r"/mod/ubboard/article\.php\?id=(\d+)(?:&amp;|&)bwid=(\d+)", article_href)
                    if not article or article.group(2) in seen:
                        continue
                    seen.add(article.group(2))
                    detail = self.session.get(article_href)
                    article_title = _match_text(detail, r'<div[^>]+class=["\'][^"\']*subject[^"\']*["\'][^>]*>.*?<h3[^>]*>(.*?)</h3>')
                    content = _match_text(detail, r'<div[^>]+class=["\'][^"\']*text_to_html[^"\']*["\'][^>]*>(.*?)</div>')
                    text = _plain_text(detail)
                    result.append({"id": f"tls-notice-{article.group(2)}", "externalId": article.group(2), "courseId": course["id"], "title": article_title or f"TLS notice {article.group(2)}", "content": content, "publishedAt": _find_datetime(text, ("작성일", "게시일", "등록일")) or "1970-01-01T00:00:00+09:00", "source": "tls"})
        return result

    def get_resources(self, user_id: str, notices: list[dict[str, Any]] | None = None,
                      existing_resources: Mapping[str, dict[str, Any]] | None = None,
                      progress: Callable[[str], None] | None = None,
                      download_new_files: bool = True,
                      save_file: Callable[[dict], dict] | None = None) -> list[dict[str, Any]]:
        parallel = self._parallel_courses('get_resources', user_id, notices=notices,
            existing_resources=existing_resources, progress=progress, download_new_files=download_new_files, save_file=save_file)
        if parallel is not None:
            return parallel
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        notices_by_course: dict[str, list[dict[str, Any]]] = {}
        for notice in notices or []:
            notices_by_course.setdefault(notice.get("courseId", ""), []).append(notice)
        for course in self.get_courses(user_id):
            course_html = self._course_page(course)
            parser = _ActivityParser()
            parser.feed(course_html)
            for activity in parser.items:
                href, title = activity["href"], activity["title"]
                match = re.search(r"/mod/(?:resource|ubfile)/view\.php\?id=(\d+)", href)
                if not match or match.group(1) in seen:
                    continue
                seen.add(match.group(1))
                resource_id = match.group(1)
                if progress:
                    progress(f"{course['name']} · 자료 {len(result) + 1} 확인 중이에요…")
                restriction = _download_restriction(activity["text"], title, notices_by_course.get(course["id"], []))
                if not restriction:
                    try:
                        check_download_url(href)
                        if "/mod/ubfile/view.php" in urlsplit(href).path:
                            page_text = _plain_text(self.session.get(href))
                            restriction = _download_restriction(page_text, title, notices_by_course.get(course["id"], []))
                    except DownloadRestricted as error:
                        restriction = str(error)
                    except HTTPError as error:
                        if error.code != 403:
                            raise
                        error.close()
                        restriction = 'TLS 서버가 파일 다운로드를 거부했습니다. 파일 내용을 가져오지 않았습니다.'
                if restriction:
                    result.append({"id": f"tls-resource-{resource_id}", "externalId": resource_id,
                                   "courseId": course["id"], "title": title or f"TLS resource {resource_id}",
                                   "fileName": title or f"resource-{resource_id}", "extension": Path(title).suffix.lower().lstrip(".") or "unknown",
                                   "mimeType": None, "remotePath": href, "source": "tls",
                                   "downloadStatus": "PROHIBITED", "downloadReason": restriction})
                    continue
                cached = (existing_resources or {}).get(resource_id)
                reusable = bool(cached and cached.get("localPath") and Path(cached["localPath"]).is_file() and cached.get("remotePath") == href and cached.get("mimeType") not in {"text/html", "application/xhtml+xml"})
                if reusable:
                    reused = {"id": f"tls-resource-{resource_id}", "externalId": resource_id,
                                   "courseId": course["id"], "title": title or cached.get("title", f"TLS resource {resource_id}"),
                                   "fileName": cached.get("fileName") or title or f"resource-{resource_id}",
                                   "extension": cached.get("extension", "unknown"), "mimeType": cached.get("mimeType"),
                                   "remotePath": href, "localPath": cached["localPath"],
                                   "downloadedAt": cached.get("downloadedAt"), "source": "tls",
                                   "downloadStatus": "DOWNLOADED", '_reused': True}
                    if not download_new_files or not hasattr(self.session, 'revalidate'):
                        result.append(reused)
                        continue
                if not download_new_files:
                    result.append({"id": f"tls-resource-{resource_id}", "externalId": resource_id,
                                   "courseId": course["id"], "title": title or f"TLS resource {resource_id}",
                                   "fileName": title or f"resource-{resource_id}",
                                   "extension": Path(title).suffix.lower().lstrip(".") or "unknown",
                                   "mimeType": None, "remotePath": href, "source": "tls",
                                   "downloadStatus": "NOT_DOWNLOADED",
                                   "downloadReason": "이번에는 목록만 확인했어요. 파일이 필요하면 전체 새로고침을 요청해 주세요."})
                    continue
                try:
                    content, response = self.session.revalidate(href, cached) if reusable else self.session.get_bytes(href)
                except (HTTPError, DownloadRestricted) as error:
                    if isinstance(error, HTTPError):
                        if error.code != 403:
                            raise
                        error.close()
                    result.append({"id": f"tls-resource-{resource_id}", "externalId": resource_id,
                                   "courseId": course["id"], "title": title or f"TLS resource {resource_id}",
                                   "fileName": title or f"resource-{resource_id}", "extension": Path(title).suffix.lower().lstrip(".") or "unknown",
                                   "mimeType": None, "remotePath": href, "source": "tls",
                                   "downloadStatus": "PROHIBITED", "downloadReason": str(error) if isinstance(error, DownloadRestricted) else "TLS 서버가 파일 다운로드를 거부했습니다. 파일 내용을 가져오지 않았습니다."})
                    continue
                if content is None and reusable:
                    result.append(reused)
                    continue
                if _looks_like_viewer_page(content, response.headers.get("Content-Type", ""), response.geturl()):
                    result.append({"id": f"tls-resource-{resource_id}", "externalId": resource_id,
                                   "courseId": course["id"], "title": title or f"TLS resource {resource_id}",
                                   "fileName": title or f"resource-{resource_id}", "extension": "unknown", "mimeType": response.headers.get("Content-Type"),
                                   "remotePath": href, "source": "tls", "downloadStatus": "NOT_DOWNLOADED",
                                   "downloadReason": "TLS가 다운로드 금지가 아닌 문서 뷰어 페이지를 반환해 원본 파일을 저장하지 못했습니다."})
                    continue
                final_path = unquote(urlsplit(response.geturl()).path)
                mime_type = response.headers.get_content_type()
                if mime_type in {"text/html", "application/xhtml+xml"}:
                    result.append({"id": f"tls-resource-{resource_id}", "externalId": resource_id,
                                   "courseId": course["id"], "title": title or f"TLS resource {resource_id}",
                                   "fileName": title or f"resource-{resource_id}", "extension": "unknown",
                                   "mimeType": mime_type, "remotePath": href, "source": "tls",
                                   "downloadStatus": "NOT_DOWNLOADED", "downloadReason": "TLS가 원본 파일 대신 HTML 문서 페이지를 반환해 파일로 저장하지 않았습니다."})
                    continue
                file_name = response.headers.get_filename() or Path(final_path).name
                extension = Path(file_name).suffix.lower().lstrip(".")
                if extension in {"html", "htm", "php"}:
                    extension = ""
                    if Path(title).suffix:
                        file_name = Path(title).name
                extension = extension or Path(title).suffix.lower().lstrip(".")
                extension = extension or {
                    "application/pdf": "pdf",
                    "application/msword": "doc",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
                    "application/vnd.hancom.hwp": "hwp",
                    "application/haansofthwp": "hwp",
                    "application/vnd.ms-powerpoint": "ppt",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
                    "text/plain": "txt",
                }.get(mime_type, "")
                if not extension and mime_type.startswith("text/"):
                    extension = "txt"
                if not extension or extension in {"html", "htm", "php"}:
                    continue
                file_name = file_name or f"resource-{match.group(1)}.{extension}"
                if not Path(file_name).suffix:
                    file_name = f"{file_name}.{extension}"
                item = {"id": f"tls-resource-{resource_id}", "externalId": resource_id, "courseId": course["id"], "title": title or file_name, "fileName": file_name, "extension": extension, "mimeType": mime_type, "remotePath": href, "source": "tls", "downloadStatus": "NOT_DOWNLOADED", "_content": content,
                        '_validators': {'etag': response.headers.get('ETag'), 'lastModified': response.headers.get('Last-Modified')}}
                result.append(save_file(item) if save_file else item)
        return result


def _download_restriction(activity_text: str, title: str, notices: list[dict[str, Any]]) -> str | None:
    patterns = (
        r"(?:다운로드|다운받|내려받|저장)\s*(?:은|는|이|가|을|를)?\s*(?:금지|불가|제한|할\s*수\s*없|허용되지\s*않|하지\s*마|지\s*마|하면\s*안)",
        r"(?:금지|불가|제한)\s*(?:된|되어)?\s*(?:다운로드|저장)",
        r"(?:do\s+not\s+download|download(?:ing)?\s+(?:is\s+)?(?:prohibited|disabled|not\s+allowed|not\s+downloadable))",
    )
    def restricted(text: str) -> bool:
        clauses = re.split(r'[\n;,。.!?]+|\s+(?:하지만|그러나|다만)\s+', text)
        for clause in clauses:
            for pattern in patterns:
                for match in re.finditer(pattern, clause, re.I):
                    # Negation must describe this prohibition, not another allowed file.
                    if not re.match(r'\s*[（(]?\s*(?:가|는|을|를)?\s*(?:아니|아닙|아닌|아님|해제|하지\s*않)', clause[match.end():]):
                        return True
        return False

    if restricted(activity_text):
        return "강의실 자료 항목에 다운로드 제한이 표시되어 파일을 가져오지 않았습니다."

    normalized_title = re.sub(r"\s+", "", title).casefold()
    for notice in notices:
        notice_text = f"{notice.get('title', '')} {notice.get('content', '')}"
        if not restricted(notice_text):
            continue
        notice_compact = re.sub(r"\s+", "", notice_text).casefold()
        broad_rule = re.search(r"(?:모든|전체|전부)\s*(?:강의|수업)?\s*(?:자료|파일)|(?:강의|수업)(?:자료|파일)\s*(?:모두|전체|전부)", notice_text)
        if (normalized_title and normalized_title in notice_compact) or broad_rule:
            return "과목 공지에 해당 자료의 다운로드 제한이 있어 파일을 가져오지 않았습니다."
    return None


def _looks_like_viewer_page(content: bytes, mime_type: str, response_url: str) -> bool:
    """Reject HTML viewer responses masquerading as downloadable text files."""
    if "html" not in (mime_type or "").lower() and not urlsplit(response_url).path.lower().endswith(("/view.php", "/viewer.php")):
        return False
    sample = content[:4096].lstrip().lower()
    return b"<html" in sample or b"<!doctype" in sample or b"<a " in sample or b"<script" in sample or b"<body" in sample


def _plain_text(html: str) -> str:
    # Moodle pages may contain old conditional comments that Python's parser rejects.
    html = re.sub(r"<!\[[^>]*>", "", html, flags=re.S)

    class VisibleTextParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.suppressed = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            values = dict(attrs)
            classes = (values.get("class") or "").lower()
            style = (values.get("style") or "").lower().replace(" ", "")
            hidden = "hidden" in values or any(token in classes.split() for token in ("accesshide", "hidden", "sr-only", "d-none")) or re.search(r"(?:display:none|visibility:hidden)", style)
            if self.suppressed or tag.lower() in {"script", "style", "noscript", "template"} or hidden:
                if not self.suppressed:
                    self.parts.append(" ")
                self.suppressed += 1

        def handle_endtag(self, tag: str) -> None:
            if self.suppressed:
                self.suppressed -= 1
            if tag.lower() in {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
                self.parts.append(" ")

        def handle_data(self, data: str) -> None:
            if not self.suppressed:
                self.parts.append(data)

    parser = VisibleTextParser()
    parser.feed(html)
    return " ".join("".join(parser.parts).split())


def _match_text(html: str, pattern: str) -> str:
    match = re.search(pattern, html, re.I | re.S)
    return _plain_text(unescape(match.group(1))) if match else ""


def _find_datetime(text: str, labels: tuple[str, ...]) -> str | None:
    label = "|".join(re.escape(item) for item in labels)
    match = re.search(rf"(?:{label})\s*:?\s*(\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}\s+\d{{1,2}}:\d{{2}})", text, re.I)
    if not match:
        return None
    value = re.sub(r"[/.]", "-", match.group(1))
    return f"{value}:00+09:00"


def _find_number(html: str, variable: str) -> float:
    match = re.search(rf"var\s+{re.escape(variable)}\s*=\s*([0-9.]+)", html)
    return float(match.group(1)) if match else 0.0


def _find_playtime(html: str) -> int:
    match = re.search(r'class=["\']playtime["\'][^>]*>\s*(\d{1,3}):(\d{2})', html, re.I)
    return int(match.group(1)) * 60 + int(match.group(2)) if match else 0


def _vod_durations(html: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for match in re.finditer(r"/mod/vod/view\.php\?id=(\d+).*?text-info[^>]*>\s*,?\s*(\d{1,3}):(\d{2})", html, re.I | re.S):
        result[match.group(1)] = int(match.group(2)) * 60 + int(match.group(3))
    return result


def _vod_availability(html: str) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for activity in re.finditer(r'<li\b[^>]*class="[^"]*\bvod\b[^"]*"[^>]*id="module-(\d+)"[^>]*>.*?</li>', html, re.I | re.S):
        dates = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*~\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', activity.group())
        if dates:
            result[activity.group(1)] = tuple(value.replace(' ', 'T') + '+09:00' for value in dates.groups())
    return result
