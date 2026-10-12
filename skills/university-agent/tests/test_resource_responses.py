"""Response-driven downloads, with no real accounts, subjects or network calls."""
import io
import json
from email.message import Message
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import MoodleSession, LoginError, DownloadRestricted
from features.guidance import resource_unavailable_reason

ORIGIN = 'https://tls.kku.ac.kr'
ACTIVITY = ORIGIN + '/mod/ubfile/view.php?id=10'
CHILD = ORIGIN + '/mod/ubfile/viewer.php?id=10'
VIEWER = ORIGIN + '/local/ubdoc/?id=987&tp=m&pg=ubfile'

def headers(mime, name=None):
    result = Message()
    result['Content-Type'] = mime
    if name:
        result.add_header('Content-Disposition', 'attachment', filename=name)
    return result

class Session:
    base_url = ORIGIN
    def __init__(self):
        self.pages = {ACTIVITY: ('<a href="' + CHILD + '">문서 보기</a>', ACTIVITY, headers('text/html')),
                      CHILD: ('<script src="/local/ubdoc/_assets/js/main.js"></script>', VIEWER, headers('text/html'))}
        self.state = {'state_code': '100', 'file_download': '1'}
        self.pages_read, self.bytes_read, self.states_read = [], [], []
        self.content, self.response_headers = b'%PDF-fixture', headers('application/pdf', 'original.pdf')
    def get_page_info(self, path, **kwargs):
        self.pages_read.append(path)
        value = self.pages[path]
        if isinstance(value, Exception):
            raise value
        return value
    def document_state(self, viewer):
        self.states_read.append(viewer)
        return self.state
    def get_bytes(self, path, **kwargs):
        self.bytes_read.append(path)
        return self.content, SimpleNamespace(headers=self.response_headers, geturl=lambda: path)

def provider(session):
    result = MoodleTLSProvider(session)
    result._courses = [{'id': 'course-a', 'externalId': '1', 'name': '임의 과목'}]
    result._course_pages['1'] = '<li class="activity"><a href="' + ACTIVITY + '">단원. 자료</a></li>'
    return result

class ResourceResponseTests(unittest.TestCase):
    def test_legacy_utf8_header_names_are_recovered_without_breaking_latin1_or_rfc_names(self):
        from providers.moodle_provider import _header_filename
        message = headers('application/pdf')
        expected = '수업자료.pdf'
        message['Content-Disposition'] = 'attachment; filename="' + expected.encode('utf-8').decode('latin1') + '"'
        self.assertEqual(_header_filename(message), expected)
        message.replace_header('Content-Disposition', 'attachment; filename="café.pdf"')
        self.assertEqual(_header_filename(message), 'café.pdf')
        self.assertEqual(_header_filename(headers('application/pdf', expected)), expected)

    def test_actual_content_type_accepts_direct_pdf_and_zip(self):
        for mime, name, content in [('application/pdf', 'note.pdf', b'%PDF-fixture'),
                                     ('application/zip', 'sources.zip', b'PK\x03\x04-fixture')]:
            with self.subTest(mime=mime):
                session = Session()
                direct = ORIGIN + '/pluginfile.php/12/mod_ubfile/content/0/' + name
                session.pages[ACTIVITY] = ('', direct, headers(mime, name))
                session.content, session.response_headers = content, headers(mime, name)
                item = provider(session).get_resources('u')[0]
                self.assertEqual(item['fileName'], name)
                self.assertEqual(item['_content'], content)
                self.assertEqual(session.bytes_read, [direct])
                self.assertEqual(session.states_read, [])

    def test_nested_visible_viewer_uses_observed_document_id_and_server_permission(self):
        session = Session()
        item = provider(session).get_resources('u')[0]
        self.assertEqual(session.pages_read, [ACTIVITY, CHILD])
        self.assertEqual(session.states_read, [VIEWER])
        self.assertIn('id=987', session.bytes_read[0])
        self.assertEqual(item['extension'], 'pdf')
        session = Session()
        session.state['file_download'] = '0'
        item = provider(session).get_resources('u', download_new_files=False)[0]
        self.assertEqual(item['downloadStatus'], 'PROHIBITED')
        self.assertEqual(session.bytes_read, [])

    def test_unknown_permission_never_reuses_cached_original_or_calls_it_prohibited(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cached.pdf'
            path.write_bytes(b'%PDF-old')
            session = Session()
            session.state.pop('file_download')
            item = provider(session).get_resources('u', existing_resources={'10': {
                'localPath': str(path), 'remotePath': ACTIVITY, 'mimeType': 'application/pdf'}})[0]
            self.assertEqual(item['downloadStatus'], 'NOT_DOWNLOADED')
            self.assertIn('허용 여부', item['downloadReason'])
            self.assertEqual(session.bytes_read, [])
            self.assertEqual(path.read_bytes(), b'%PDF-old')
        session = Session()
        legacy_client = SimpleNamespace(base_url=ORIGIN, get_page_info=session.get_page_info, get_bytes=session.get_bytes)
        item = provider(legacy_client).get_resources('u')[0]
        self.assertEqual(item['downloadStatus'], 'NOT_DOWNLOADED')
        self.assertEqual(session.bytes_read, [])

    def test_not_ready_document_is_not_downloaded(self):
        for state in ('201', '202', '204', '300'):
            session = Session()
            session.state['state_code'] = state
            item = provider(session).get_resources('u')[0]
            self.assertEqual(item['downloadStatus'], 'NOT_DOWNLOADED')
            self.assertEqual(session.bytes_read, [])

    def test_http_failures_have_distinct_causes_without_raw_urls(self):
        for code in (403, 404, 410, 429, 503):
            session = Session()
            session.pages[ACTIVITY] = HTTPError(ACTIVITY + '&secret=DO_NOT_SHOW', code, 'error', Message(), io.BytesIO())
            item = provider(session).get_resources('u')[0]
            self.assertEqual(item['downloadStatus'], 'PROHIBITED' if code == 403 else 'NOT_DOWNLOADED')
            self.assertNotIn('DO_NOT_SHOW', item['downloadReason'])
            self.assertNotEqual(resource_unavailable_reason(item), '원본 파일은 아직 받지 못했어요.')
        session = Session()
        session.pages[ACTIVITY] = HTTPError(ACTIVITY, 401, 'expired', Message(), io.BytesIO())
        with self.assertRaises(LoginError):
            provider(session).get_resources('u')

    def test_unrelated_viewer_ids_hidden_links_and_cycles_are_not_followed(self):
        for html in ('<a href="' + CHILD.replace('id=10', 'id=11') + '">다른 자료</a>',
                     '<a href="' + CHILD + '" hidden>숨김</a>',
                     '<a href="https://other.invalid/mod/ubfile/viewer.php?id=10">다른 사이트</a>'):
            session = Session()
            session.pages[ACTIVITY] = (html, ACTIVITY, headers('text/html'))
            self.assertEqual(provider(session).get_resources('u')[0]['downloadStatus'], 'NOT_DOWNLOADED')
            self.assertEqual(session.pages_read, [ACTIVITY])
        session = Session()
        session.pages[CHILD] = session.pages[ACTIVITY]
        item = provider(session).get_resources('u')[0]
        self.assertIn('반복', item['downloadReason'])
        self.assertEqual(len(session.pages_read), 2)

    def test_permission_path_reuses_navigation_but_cannot_override_url_denial(self):
        session = Session()
        session.state['file_download'] = '0'
        adapter = provider(session)
        original = {'id': 'tls-resource-10', 'courseId': 'course-a', 'title': '단원. 자료',
            'fileName': 'note.pdf', 'extension': 'pdf', 'source': 'tls', 'remotePath': ACTIVITY,
            'downloadStatus': 'PROHIBITED', 'downloadReason': '다운로드 금지'}
        permission = {'userId': 'u', 'resourceId': original['id'], 'instructor': '테스트 교수',
            'statement': '모의 자료에 대한 파일별 허락 진술', 'userConfirmed': True}
        item = adapter.download_permitted_resource('u', original, permission)
        self.assertEqual(item['_viewerUrl'], VIEWER)
        self.assertEqual(len(session.bytes_read), 1)
        session.bytes_read.clear()
        session.pages[CHILD] = ('', VIEWER + '&nodownload=1', headers('text/html'))
        with self.assertRaises(DownloadRestricted):
            adapter.download_permitted_resource('u', original, permission)
        self.assertEqual(session.bytes_read, [])

    def test_html_masquerading_as_pdf_is_not_saved_and_chapter_title_is_not_extension(self):
        session = Session()
        session.content = b'<html>error</html>'
        item = provider(session).get_resources('u')[0]
        self.assertNotIn('_content', item)
        self.assertIn('형식과 내용', item['downloadReason'])
        self.assertNotIn('PROHIBITED', item['downloadStatus'])
        item = provider(Session()).get_resources('u', download_new_files=False)[0]
        self.assertEqual(item['extension'], 'unknown')

    def test_metadata_inspection_never_reads_file_body(self):
        session = MoodleSession()
        session.logged_in = True
        for mime in ('application/pdf', 'application/zip', 'text/plain'):
            response = Mock(headers=headers(mime), geturl=lambda: ORIGIN + '/file.bin')
            with patch.object(session.opener, 'open', return_value=response):
                self.assertEqual(session.get_page_info(ACTIVITY, allowed_origin='tls.kku.ac.kr')[0], '')
            response.read.assert_not_called()
            response.close.assert_called_once()

    def test_document_status_is_read_only_and_does_not_return_sensitive_fields(self):
        session = MoodleSession()
        session.logged_in = True
        response = Mock(headers=headers('application/json'), geturl=lambda: ORIGIN + '/local/ubdoc/worker.php')
        response.read.return_value = json.dumps({'state_code': '100', 'file_download': '0',
            'owner_id': 'DO_NOT_SHOW', 'file_id': 'DO_NOT_SHOW'}).encode()
        with patch.object(session.opener, 'open', return_value=response) as request:
            self.assertEqual(session.document_state(VIEWER), {'state_code': '100', 'file_download': '0'})
        fields = parse_qs(request.call_args.args[0].data.decode())
        self.assertEqual(fields['job'], ['checkState'])
        self.assertEqual(fields['id'], ['987'])
        with patch.object(session.opener, 'open') as request:
            with self.assertRaises(ValueError):
                session.document_state(VIEWER + '&apiRoute=other')
            request.assert_not_called()

if __name__ == '__main__':
    unittest.main()
