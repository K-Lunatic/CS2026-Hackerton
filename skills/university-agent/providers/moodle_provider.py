"""Moodle HTML adapter for the KKU TLS pages observed by MoodleSession."""
from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import mimetypes
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from urllib.error import HTTPError, URLError
from concurrent.futures import ThreadPoolExecutor

from providers.moodle_session import MoodleSession, LoginError, DownloadRestricted, check_download_url, check_download_origin


class ResourceUnavailable(ValueError):
    """The current response cannot establish a usable original; not a prohibition."""


def validate_permission(permission, user_id, resource_id):
    if (not isinstance(user_id, str) or not user_id.strip() or
        not isinstance(resource_id, str) or not resource_id.strip() or
        not isinstance(permission, dict) or
        set(permission) != {'userId', 'resourceId', 'instructor', 'statement', 'userConfirmed'} or
        permission.get('userConfirmed') is not True or permission.get('userId') != user_id or
        permission.get('resourceId') != resource_id or
        not all(isinstance(permission.get(k), str) and 0 < len(permission[k].strip()) <= limit
                for k, limit in (('instructor', 200), ('statement', 2000)))):
        raise ValueError('이 자료에 대해 확인된 허락 내용이 없어 가져오지 않았어요.')


def permitted_download_url(viewer_url):
    """Only the observed KKU ubdoc viewer is eligible; never guess document IDs."""
    check_download_origin(viewer_url, 'tls.kku.ac.kr')
    parts = urlsplit(viewer_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if (parts.path != '/local/ubdoc/' or parts.fragment or
        set(query) != {'id', 'tp', 'pg'} or query['tp'] != ['m'] or query['pg'] != ['ubfile'] or
        len(query['id']) != 1 or not re.fullmatch(r'[1-9][0-9]*', query['id'][0])):
        raise ValueError('자료의 원래 주소를 확인하지 못해 가져오지 않았어요.')
    return 'https://tls.kku.ac.kr/local/ubdoc/download.php?' + urlencode({
        'id': query['id'][0], 'tp': 'm', 'pg': 'ubfile'})


def _file_extension(name):
    value = Path(name).suffix.lower().lstrip('.')
    return value if re.fullmatch(r'[a-z0-9]{1,16}', value) and value not in ('html', 'htm', 'php') else ''


def _header_filename(headers):
    name = headers.get_filename()
    # Some servers put raw UTF-8 into a legacy Latin-1 HTTP header.
    if name and 'filename*=' not in headers.get('Content-Disposition', '').lower():
        try:
            name = name.encode('latin1').decode('utf-8')
        except UnicodeError:
            pass
    return name


def _resource_http_error(error):
    code = error.code
    error.close()
    if code == 401:
        raise LoginError('학교 로그인이 만료되어 자료 확인을 중단했어요.')
    reason = {403: 'TLS 서버가 파일 다운로드를 거부했습니다. 파일 내용을 가져오지 않았습니다.',
              404: '학교에서 자료를 찾지 못했어요. 삭제되거나 주소가 바뀌었을 수 있어요.',
              410: '학교에서 더 이상 제공하지 않는 자료예요.',
              429: '학교에서 잠시 요청을 제한했어요. 잠시 후 다시 확인해 주세요.'}.get(code,
              '학교가 자료 요청을 처리하지 못했어요. 잠시 후 다시 확인해 주세요.')
    return {'downloadStatus': 'PROHIBITED' if code == 403 else 'NOT_DOWNLOADED', 'downloadReason': reason}


class _ViewerLinks(HTMLParser):
    """Visible links and embedded viewers, not scripts, hidden inputs or URL scans."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        hidden = (any(value for _, value in self.stack) or tag in ('script', 'style', 'noscript', 'template') or
                  'hidden' in values or values.get('aria-hidden') == 'true' or
                  any(c in (values.get('class') or '').split() for c in ('hidden', 'd-none', 'accesshide')) or
                  re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', values.get('style') or '', re.I))
        if not hidden:
            key = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(tag)
            if key and values.get(key):
                self.links.append(values[key])
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append((tag, bool(hidden)))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


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
    RESOURCE_RESPONSE_VERSION = '2'

    def __init__(self, session: MoodleSession, progress=None):
        self.session = session
        self.progress = progress
        self._courses: list[dict[str, Any]] | None = None
        self._course_pages: dict[str, str] = {}
        self._activity_links: dict[str, list[tuple[str, str]]] = {}

    def _parallel_courses(self, method, user, **kwargs):
        courses = self.get_courses(user)
        if len(courses) < 2 or not hasattr(self.session, 'fork'):
            return None
        # ponytail: at most four independent read sessions, not an unbounded request fan-out.
        def collect(course):
            provider = MoodleTLSProvider(self.session.fork(), self.progress)
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
            if self.progress:
                self.progress(f"{course['name']} 수업을 살펴보고 있어요…")
            self._course_pages[external_id] = self.session.get(f"/course/view.php?id={external_id}")
        return self._course_pages[external_id]

    def _course_activity_links(self, course: dict[str, Any]) -> list[tuple[str, str]]:
        """Reuse parsed links for the lifetime of the cached course page."""
        external_id = str(course["externalId"])
        if external_id not in self._activity_links:
            self._activity_links[external_id] = self._links(self._course_page(course), only_activities=True)
        return self._activity_links[external_id]

    def _resource_download_path(self, href, title, notices):
        """Inspect response/visible navigation, independent of course or filename."""
        if "/mod/ubfile/view.php" not in urlsplit(href).path:
            return href, None
        path, issue, _ = self._resolve_resource_page(href, title, notices)
        return path, issue

    def _resolve_resource_page(self, href, title, notices, *, permitted=False, viewer_url=None):
        origin = getattr(self.session, 'base_url', 'https://tls.kku.ac.kr').rstrip('/') + '/'
        current = urljoin(origin, href)
        activity_id = parse_qs(urlsplit(current).query).get('id')
        visited = set()
        for _ in range(4):
            check_download_origin(current, 'tls.kku.ac.kr')
            check_download_url(current)
            if current in visited:
                return None, '자료의 보기 화면이 반복되어 주소 확인을 멈췄어요.', None
            visited.add(current)
            headers = {}
            if hasattr(self.session, 'get_page_info'):
                html, page_url, headers = self.session.get_page_info(current, allowed_origin='tls.kku.ac.kr')
            elif hasattr(self.session, 'get_page'):
                html, page_url = self.session.get_page(current, allowed_origin='tls.kku.ac.kr')
            else:
                html, page_url = self.session.get(href), current
            check_download_origin(page_url, 'tls.kku.ac.kr')
            check_download_url(page_url)
            restriction = _download_restriction(_plain_text(html), title, notices)
            if restriction and not permitted:
                raise DownloadRestricted(restriction)
            mime = headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if mime in ('application/json', 'application/xml', 'text/xml') and 'attachment' not in headers.get('Content-Disposition', '').lower() and not urlsplit(page_url).path.startswith('/pluginfile.php/'):
                raise ResourceUnavailable('학교에서 원본 대신 문서 정보 응답을 보내 파일로 저장하지 않았어요.')
            direct_file = (mime and mime not in ('text/html', 'application/xhtml+xml')) or (
                not mime and not html and urlsplit(page_url).path.startswith('/pluginfile.php/'))
            if direct_file and not permitted:
                return page_url, None, None
            parser = _ViewerLinks()
            parser.feed(html)
            observed, children = set(), set()
            for link in (page_url, *parser.links):
                candidate = urljoin(page_url, link)
                try:
                    check_download_origin(candidate, 'tls.kku.ac.kr')
                except DownloadRestricted:
                    continue
                if urlsplit(candidate).path in ('/local/ubdoc/', '/mod/ubfile/viewer.php'):
                    check_download_url(candidate)
                try:
                    permitted_download_url(candidate)
                except ValueError:
                    parts = urlsplit(candidate)
                    query = parse_qs(parts.query, keep_blank_values=True)
                    if parts.path == '/mod/ubfile/viewer.php' and not parts.fragment and set(query) == {'id'} and query['id'] == activity_id:
                        children.add(candidate)
                else:
                    observed.add(candidate)
            if observed:
                chosen = viewer_url if viewer_url in observed else next(iter(observed)) if len(observed) == 1 and viewer_url is None else None
                if chosen is None:
                    return None, '원본 주소가 여러 개이거나 선택한 주소를 확인하지 못해 가져오지 않았어요.', None
                if hasattr(self.session, 'document_state'):
                    try:
                        state = self.session.document_state(chosen)
                    except ValueError:
                        raise ResourceUnavailable('학교의 문서 상태 응답을 확인하지 못했어요.') from None
                    if str(state.get('state_code')) != '100':
                        reason = '학교에서 문서를 찾지 못했어요.' if str(state.get('state_code')) == '201' else '학교의 문서 준비·변환이 끝나지 않았거나 지원하지 않는 형식이에요.'
                        raise ResourceUnavailable(reason)
                    allowed = state.get('file_download')
                    if allowed in (0, '0', False) and not permitted:
                        raise DownloadRestricted('학교 문서 보기에서 다운로드 금지가 확인되어 파일을 가져오지 않았습니다.')
                    if allowed not in (0, '0', False, 1, '1', True) and not permitted:
                        raise ResourceUnavailable('학교의 다운로드 허용 여부를 확인하지 못해 가져오지 않았어요.')
                elif not permitted:
                    raise ResourceUnavailable('학교의 다운로드 허용 여부를 확인하지 못해 가져오지 않았어요.')
                return permitted_download_url(chosen), None, chosen
            if len(children) != 1 or not (hasattr(self.session, 'get_page') or hasattr(self.session, 'get_page_info')):
                return None, 'TLS 자료 화면에서 원본 다운로드 주소를 확인하지 못했어요.', None
            current = next(iter(children))
        return None, '자료의 보기 화면이 너무 많아 주소 확인을 멈췄어요.', None

    @staticmethod
    def _activity_fingerprint(html: str) -> str:
        parser = _ActivityParser()
        parser.feed(html)
        activities = [{key: item.get(key, "") for key in ("href", "title", "text")}
                      for item in parser.items]
        payload = json.dumps(activities, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def preflight(self, user_id: str, course_ids: set[str] | None = None) -> dict[str, Any]:
        """Fetch only course activity lists to detect additions before a full sync."""
        courses = self.get_courses(user_id)
        selected = [course for course in courses if not course_ids or course["id"] in course_ids]
        if len(selected) < 2 or not hasattr(self.session, "fork"):
            fingerprints = {course["id"]: self._activity_fingerprint(self._course_page(course))
                            for course in selected}
        else:
            def collect(course):
                child = MoodleTLSProvider(self.session.fork(), self.progress)
                child._courses = [course]
                return course["id"], child._activity_fingerprint(child._course_page(course))

            with ThreadPoolExecutor(max_workers=min(4, len(selected))) as pool:
                fingerprints = dict(pool.map(collect, selected))
        return {"courseIds": [course["id"] for course in courses], "fingerprints": fingerprints}

    def activity_fingerprints(self, user_id: str) -> dict[str, str]:
        """Return fingerprints for course pages already fetched during a full sync."""
        courses = self.get_courses(user_id)
        return {course["id"]: self._activity_fingerprint(self._course_page(course)) for course in courses}

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
                if self.progress:
                    self.progress(f"{course['name']} · {title or '과제'}의 마감일을 확인하고 있어요…")
                detail = self.session.get(f"/mod/assign/view.php?id={match.group(1)}")
                text = _plain_text(detail)
                due = _find_datetime(text, ("종료 일시", "마감일", "Due date"))
                status = "SUBMITTED" if "제출 완료" in text else "NOT_SUBMITTED" if re.search(r"제출 (?:안 함|하지 않음|되지 않음)", text) else "UNKNOWN"
                result.append({"id": f"tls-assignment-{match.group(1)}", "externalId": match.group(1), "courseId": course["id"], "title": title or f"TLS assignment {match.group(1)}", "dueAt": due, "submissionStatus": status, "source": "tls"})
        return result

    def _academic_items(self, user_id):
        return ([('assignment', item) for item in self.get_assignments(user_id)] +
                [('lecture', item) for item in self.get_lectures(user_id)])

    def get_academic_snapshot(self, user_id):
        items = self._parallel_courses('_academic_items', user_id)
        if items is None:
            items = self._academic_items(user_id)
        return (self.get_courses(user_id),
                [item for kind, item in items if kind == 'assignment'],
                [item for kind, item in items if kind == 'lecture'])

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
                if self.progress:
                    self.progress(f"{course['name']} · {title or '강의 영상'}의 시청 상태를 확인하고 있어요…")
                viewer = self.session.get(f"/mod/vod/viewer.php?id={vod_id}")
                playtime = _find_playtime(viewer) or durations.get(vod_id, 0)
                progress = _find_number(viewer, "is_progress")
                complete = _find_number(viewer, "is_complete") == 1
                start, end = availability.get(vod_id, (None, None))
                result.append({"id": f"tls-lecture-{vod_id}", "externalId": vod_id, "courseId": course["id"], "title": title or f"TLS lecture {vod_id}", "durationSeconds": playtime, "watchedSeconds": round(playtime * progress / 100), "watchProgress": progress, "completed": complete, "availableFrom": start, "availableUntil": end, "source": "tls"})
        return result

    def get_lecture_media(self, user_id, lecture, notices=None, *, video=False):
        """Fresh completion/permission checks, without any player or attendance writes."""
        from providers.lecture_media import media_plan
        if lecture.get('source') != 'tls' or lecture.get('completed') is not True:
            raise ResourceUnavailable('끝까지 본 강의만 음성을 가져올 수 있어요. 시청 기록은 바꾸지 않아요.')
        identifier = str(lecture.get('externalId', ''))
        if not re.fullmatch(r'[1-9][0-9]*', identifier) or lecture.get('id') != 'tls-lecture-' + identifier:
            raise ResourceUnavailable('선택한 강의를 확인하지 못했어요.')
        course = next((item for item in self.get_courses(user_id) if item['id'] == lecture['courseId']), None)
        if course is None:
            raise ResourceUnavailable('현재 수강 중인 과목의 강의가 아니에요.')
        parser = _ActivityParser()
        parser.feed(self._course_page(course))
        title = next((title for href, title in self._course_activity_links(course)
                      if re.search(r'/mod/vod/view\.php\?id=' + identifier + r'(?:&|$)', href)), None)
        activity = next((item for item in parser.items
                         if re.search(r'/mod/vod/view\.php\?id=' + identifier + r'(?:&|$)', item['href'])), None)
        if not activity or title != lecture['title']:
            raise ResourceUnavailable('강의 정보가 바뀌었어요. 학교 목록을 새로 확인해 주세요.')
        if notices is None:
            reader = MoodleTLSProvider(self.session)
            reader._courses = [course]
            reader._course_pages = self._course_pages.copy()
            notices = reader.get_notices(user_id)
        issue = _download_restriction(activity['text'], lecture['title'], notices or [], videos=True)
        if issue:
            raise DownloadRestricted('이 강의는 다운로드가 제한되어 음성을 가져오지 않았어요.')
        viewer, final = self.session.get_page('/mod/vod/viewer.php?id=' + identifier,
                                             allowed_origin=urlsplit(self.session.base_url).hostname)
        if '/login' in urlsplit(final).path:
            raise LoginError('학교 연결이 만료됐어요. 다시 연결해 주세요.')
        if _find_number(viewer, 'is_complete') != 1:
            raise ResourceUnavailable('학교에서 아직 시청 완료로 확인되지 않았어요. 음성은 가져오지 않았어요.')
        if _download_restriction(_plain_text(viewer), lecture['title'], []):
            raise DownloadRestricted('이 강의는 다운로드가 제한되어 음성을 가져오지 않았어요.')
        if re.search(r'\b(?:allow_?download|file_download)\s*[:=]\s*[\"\']?(?:0|false)\b|\bdrm\s*:', viewer, re.I):
            raise DownloadRestricted('보호되거나 저장이 제한된 영상이라 음성을 가져오지 않았어요.')
        sources = re.findall(r'\bsources\s*:\s*\[\s*\{\s*file\s*:\s*([\"\'])(.*?)\1', viewer, re.S)
        if len(sources) != 1:
            raise ResourceUnavailable('이 강의의 실제 재생 주소를 확인하지 못했어요.')
        return media_plan(self.session, unescape(sources[0][1]).replace('\\/', '/'), video=video)

    def save_lecture_media(self, plan, target, progress=None):
        from providers.lecture_media import download_media
        return download_media(self.session, plan, target, progress)

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
                    if self.progress:
                        self.progress(f"{course['name']}의 공지를 살펴보고 있어요…")
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
                      save_file: Callable[[dict], dict] | None = None,
                      resource_ids: set[str] | None = None) -> list[dict[str, Any]]:
        jobs = []
        seen = set()
        notices_by_course = {}
        for notice in notices or []:
            notices_by_course.setdefault(notice.get('courseId', ''), []).append(notice)
        for course in self.get_courses(user_id):
            parser = _ActivityParser()
            parser.feed(self._course_page(course))
            for activity in parser.items:
                match = re.search(r"/mod/(?:resource|ubfile)/view\.php\?id=(\d+)", activity['href'])
                if match and match.group(1) not in seen:
                    seen.add(match.group(1))
                    jobs.append((course, activity, match.group(1)))

        def collect(job):
            course, activity, identifier = job
            provider = MoodleTLSProvider(self.session.fork()) if hasattr(self.session, 'fork') else self
            if progress:
                progress(f"{course['name']} · {activity['title'] or '자료'} 확인 중이에요…")
            return provider._get_resource(course, activity, identifier,
                notices_by_course.get(course['id'], []), existing_resources,
                download_new_files and (resource_ids is None or f'tls-resource-{identifier}' in resource_ids),
                save_file)

        # One flat pool also overlaps downloads within a single course.
        if len(jobs) > 1 and hasattr(self.session, 'fork'):
            with ThreadPoolExecutor(max_workers=4) as pool:
                return [item for item in pool.map(collect, jobs) if item is not None]
        return [item for job in jobs if (item := collect(job)) is not None]

    def _get_resource(self, course, activity, resource_id, notices, existing_resources, download_new_files, save_file):
        href, title = activity['href'], activity['title']
        record = {"id": f"tls-resource-{resource_id}", "externalId": resource_id,
                  "courseId": course["id"], "title": title or f"TLS resource {resource_id}",
                  "fileName": title or f"resource-{resource_id}", "extension": _file_extension(title) or "unknown",
                  "mimeType": None, "remotePath": href, "source": "tls", "downloadStatus": "NOT_DOWNLOADED"}
        restriction = _download_restriction(activity["text"], title, notices)
        if restriction:
            return {**record, 'downloadStatus': 'PROHIBITED', 'downloadReason': restriction}
        try:
            check_download_url(href)
            download_path, download_issue = self._resource_download_path(href, title, notices)
        except DownloadRestricted as error:
            return {**record, 'downloadStatus': 'PROHIBITED', 'downloadReason': str(error)}
        except HTTPError as error:
            return {**record, **_resource_http_error(error)}
        except ResourceUnavailable as error:
            return {**record, 'downloadReason': str(error)}
        except (URLError, TimeoutError):
            return {**record, 'downloadReason': '학교와 연결이 끊기거나 응답이 늦어 이 자료를 확인하지 못했어요.'}
        cached = (existing_resources or {}).get(resource_id)
        reusable = bool(cached and cached.get("localPath") and Path(cached["localPath"]).is_file()
            and cached.get("remotePath") == href and cached.get("mimeType") not in {"text/html", "application/xhtml+xml"})
        if reusable:
            reused = {**record, "title": title or cached.get("title", record['title']),
                      "fileName": cached.get("fileName") or record['fileName'],
                      "extension": cached.get("extension", "unknown"), "mimeType": cached.get("mimeType"),
                      "localPath": cached["localPath"], "downloadedAt": cached.get("downloadedAt"),
                      "downloadStatus": "DOWNLOADED", '_reused': True}
            if not download_new_files or not hasattr(self.session, 'revalidate'):
                return reused
        if not download_path:
            return {**record, 'mimeType': 'text/html',
                    'downloadReason': download_issue or 'TLS 자료의 원본 다운로드 주소를 확인하지 못했어요.'}
        if not download_new_files:
            return {**record, "downloadReason": "이번에는 목록만 확인했어요. 필요한 자료를 요청하면 해당 파일만 받을게요."}
        try:
            content, response = self.session.revalidate(download_path, cached) if reusable else self.session.get_bytes(download_path, allowed_origin='tls.kku.ac.kr')
        except DownloadRestricted as error:
            return {**record, 'downloadStatus': 'PROHIBITED', 'downloadReason': str(error)}
        except HTTPError as error:
            return {**record, **_resource_http_error(error)}
        except (URLError, TimeoutError):
            return {**record, 'downloadReason': '자료를 받는 중 연결이 끊기거나 응답이 늦었어요. 잠시 후 다시 확인해 주세요.'}
        if content is None and reusable:
            return reused
        if not content:
            return {**record, 'downloadReason': '학교에서 빈 파일 응답을 보내 원본을 저장하지 않았어요.'}
        mime_type = response.headers.get_content_type()
        if _looks_like_viewer_page(content, mime_type, response.geturl()) or mime_type in {"text/html", "application/xhtml+xml"}:
            return {**record, 'mimeType': mime_type,
                    'downloadReason': '학교에서 원본 대신 문서 보기 또는 안내 화면을 보내 파일로 저장하지 않았어요.'}
        final_path = unquote(urlsplit(response.geturl()).path)
        file_name = _header_filename(response.headers) or Path(final_path).name
        extension = _file_extension(file_name)
        if not extension:
            extension = (mimetypes.guess_extension(mime_type) or '').lstrip('.')
            extension = extension or _file_extension(title)
            if not extension and mime_type.startswith('text/'):
                extension = 'txt'
            if extension:
                file_name = (Path(title).name if _file_extension(title) == extension else f"resource-{resource_id}.{extension}")
        if not extension or extension in ('html', 'htm', 'php'):
            return {**record, 'mimeType': mime_type, 'downloadReason': '원본 파일의 종류를 확인하지 못해 저장하지 않았어요.'}
        signature = {'pdf': b'%PDF-', 'zip': b'PK',
            'ppt': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'doc': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',
            'hwp': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'pptx': b'PK\x03\x04',
            'docx': b'PK\x03\x04', 'hwpx': b'PK\x03\x04'}.get(extension)
        if signature and not content.startswith(signature):
            return {**record, 'mimeType': mime_type, 'downloadReason': '파일로 표시됐지만 원본 형식과 내용이 달라 저장하지 않았어요.'}
        item = {**record, "title": title or file_name, "fileName": file_name, "extension": extension,
                "mimeType": mime_type, "_content": content,
                '_validators': {'etag': response.headers.get('ETag'), 'lastModified': response.headers.get('Last-Modified')}}
        return save_file(item) if save_file else item


    def download_permitted_resource(self, user_id, resource, permission, viewer_url=None):
        validate_permission(permission, user_id, resource['id'])
        check_download_origin(self.session.base_url, 'tls.kku.ac.kr')
        if resource.get('source') != 'tls' or resource.get('downloadStatus') != 'PROHIBITED':
            raise ValueError('현재 확인된 제한 자료가 아니어서 별도로 가져오지 않았어요.')
        if str(resource.get('downloadReason', '')).startswith('TLS 서버가'):
            raise DownloadRestricted('학교에서 접근을 막아 둔 자료라 가져오지 않았어요.')
        course = next((c for c in self.get_courses(user_id) if c['id'] == resource['courseId']), None)
        if not course or (course.get('professor') and course['professor'].strip() != permission['instructor'].strip()):
            raise ValueError('수업과 담당 교수님을 확인하지 못해 가져오지 않았어요.')
        origin = 'https://tls.kku.ac.kr/'
        source_url = urljoin(origin, resource['remotePath'])
        check_download_origin(source_url, 'tls.kku.ac.kr')
        check_download_url(source_url)
        activities = _ActivityParser()
        activities.feed(self._course_page(course))
        activity = next((item for item in activities.items if urljoin(origin, item['href']) == source_url), None)
        if not activity or (activity['title'] and activity['title'] != resource['title']):
            raise ValueError('현재 수업에서 해당 자료를 찾지 못했어요.')
        download_path, issue, viewer_url = self._resolve_resource_page(
            source_url, resource['title'], [], permitted=True, viewer_url=viewer_url)
        if not download_path:
            raise ValueError(issue or '이 자료의 주소를 하나로 확인하지 못해 가져오지 않았어요.')
        content, response = self.session.get_bytes(download_path, allowed_origin='tls.kku.ac.kr')
        mime_type = response.headers.get_content_type()
        sample = content[:4096].lstrip().lower() if content else b''
        if not content or mime_type in ('text/html', 'application/xhtml+xml') or any(
                marker in sample for marker in (b'<html', b'<!doctype', b'<form', b'<script', b'<body')):
            raise DownloadRestricted('원본 자료를 받지 못했어요. 이번에는 저장하지 않을게요.')
        name = _header_filename(response.headers) or resource['fileName']
        extension = Path(name).suffix.lower().lstrip('.')
        if not extension:
            extension = (mimetypes.guess_extension(mime_type) or '').lstrip('.')
            extension = extension or (resource.get('extension') if resource.get('extension') != 'unknown' else '')
            name += '.' + extension if extension else ''
        if not extension or extension in ('html', 'htm', 'php'):
            raise DownloadRestricted('허락받은 자료의 원본을 확인하지 못했어요.')
        signature = {'pdf': b'%PDF-', 'ppt': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',
            'doc': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'hwp': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',
            'pptx': b'PK\x03\x04', 'docx': b'PK\x03\x04', 'hwpx': b'PK\x03\x04'}.get(extension)
        if signature and not content.startswith(signature):
            raise DownloadRestricted('원본 파일의 형식을 확인하지 못해 저장하지 않았어요.')
        return {**resource, 'fileName': name, 'extension': extension, 'mimeType': mime_type,
                '_content': content, '_viewerUrl': viewer_url}


def _download_restriction(activity_text: str, title: str, notices: list[dict[str, Any]], *, videos=False) -> str | None:
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
        kinds = '자료|파일|동영상|영상' if videos else '자료|파일'
        broad_rule = re.search(rf"(?:모든|전체|전부)\s*(?:강의|수업)?\s*(?:{kinds})|(?:강의|수업)(?:{kinds})\s*(?:모두|전체|전부)", notice_text)
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
