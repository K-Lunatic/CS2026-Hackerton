"""Completed-only media, local conversion and private storage; no real school account."""
from contextlib import closing
from contextlib import redirect_stdout
from email.message import Message
from pathlib import Path
import sys
import io
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from features import lecture_audio
from providers.lecture_media import media_plan, download_media
from providers.moodle_provider import MoodleTLSProvider, _download_restriction
from providers.moodle_session import MoodleSession, DownloadRestricted, _DownloadRedirectHandler, check_download_origin
from storage.local_db import LocalDatabase
from scripts import lecture_audio as audio_script

SOURCE = 'https://tlsvod.kku.ac.kr:8443/course/playlist.m3u8'
PLAYLIST = '#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\none.ts\n#EXTINF:10,\ntwo.ts\n#EXT-X-ENDLIST\n'
COURSE = {'id': 'tls-course-1', 'externalId': '1', 'name': '테스트 과목', 'source': 'tls'}
LECTURE = {'id': 'tls-lecture-3', 'externalId': '3', 'courseId': COURSE['id'], 'title': '강의 1',
           'source': 'tls', 'completed': True, 'durationSeconds': 20, 'watchedSeconds': 20, 'watchProgress': 100}


class Session:
    base_url = 'https://tls.kku.ac.kr'
    def __init__(self):
        self.calls = []
        self.viewer = "<script>var is_complete = 1; sources: [{file: '" + SOURCE + "'}]</script>"
        self.course = '<li class="activity"><a href="/mod/vod/view.php?id=3">강의 1</a></li>'
        self.files = {SOURCE: PLAYLIST.encode(), SOURCE.rsplit('/', 1)[0] + '/one.ts': b'Gfirst',
                      SOURCE.rsplit('/', 1)[0] + '/two.ts': b'Gsecond'}
    def fork(self): return self
    def get(self, path):
        self.calls.append(path)
        if path == '/local/ubion/user/':
            return '<a href="/course/view.php?id=1">테스트 과목</a>'
        return self.course
    def get_page(self, path, **kwargs):
        self.calls.append(path)
        return self.viewer, self.base_url + path
    def get_bytes(self, url, **kwargs):
        self.calls.append(url)
        check_download_origin(url, kwargs.get('allowed_origin'))
        return self.files[url], SimpleNamespace(geturl=lambda: url)
    def get_page_info(self, url, **kwargs):
        self.calls.append(url)
        headers = Message()
        headers['Content-Type'] = 'video/mp4'
        return '', url, headers


class LectureAudioTests(unittest.TestCase):
    def test_completed_flag_not_percentage_authorizes_source_and_never_calls_tracking(self):
        session = Session()
        provider = MoodleTLSProvider(session)
        with self.assertRaises(ValueError):
            provider.get_lecture_media('u', {**LECTURE, 'completed': False}, [])
        self.assertEqual(session.calls, [])
        session.viewer = session.viewer.replace('is_complete = 1', 'is_complete = 0')
        with self.assertRaises(ValueError):
            provider.get_lecture_media('u', LECTURE, [])
        self.assertNotIn(SOURCE, session.calls)
        session.viewer = session.viewer.replace('is_complete = 0', 'is_complete = 1')
        plan = provider.get_lecture_media('u', LECTURE, [])
        self.assertEqual(plan['format'], 'mpegts')
        self.assertFalse(any('progress' in path or 'complete' in path or '.ts' in path for path in session.calls))

    def test_missing_or_moved_enrolled_activity_never_fetches_media(self):
        for changed in ({'id': 'tls-lecture-999'}, {'courseId': 'other'}, {'title': '다른 제목'}, {'source': 'manual'}):
            session = Session()
            with self.assertRaises(ValueError):
                MoodleTLSProvider(session).get_lecture_media('u', {**LECTURE, **changed}, [])
            self.assertNotIn(SOURCE, session.calls)

    def test_activity_accessibility_suffix_does_not_change_lecture_identity(self):
        session = Session()
        session.course = session.course.replace('강의 1</a>', '강의 1<span class="accesshide"> 동영상</span></a>')
        self.assertEqual(MoodleTLSProvider(session).get_lecture_media('u', LECTURE, [])['format'], 'mpegts')

    def test_visible_notice_or_player_prohibition_blocks_before_original(self):
        self.assertIsNone(_download_restriction('', 'slides.pdf', [{'title': '전체 영상 다운로드 금지', 'content': ''}]))
        for place in ('activity', 'notice', 'viewer', 'flag', 'drm'):
            session = Session()
            notices = []
            if place == 'activity': session.course = session.course.replace('</li>', ' 다운로드 금지</li>')
            if place == 'notice': notices = [{'title': '전체 영상 다운로드 금지', 'content': ''}]
            if place == 'viewer': session.viewer += '<p>다운로드 금지</p>'
            if place == 'flag': session.viewer += '<script>var allow_download = 0;</script>'
            if place == 'drm': session.viewer += '<script>drm: {}</script>'
            with self.subTest(place=place), self.assertRaises(DownloadRestricted):
                MoodleTLSProvider(session).get_lecture_media('u', LECTURE, notices)
            self.assertNotIn(SOURCE, session.calls)

    def test_direct_media_status_is_rechecked_before_allowing_a_cached_copy(self):
        session = Session()
        source = SOURCE.replace('playlist.m3u8', 'video.mp4')
        plan = media_plan(session, source)
        self.assertEqual(plan['format'], 'mov')
        self.assertEqual(session.calls, [source])
        with patch.object(session, 'get_page_info', side_effect=HTTPError(source, 403, 'denied', None, None)):
            with self.assertRaises(HTTPError): media_plan(session, source)
        with patch.object(session, 'get_page_info', return_value=('<html>login</html>', source, Message())):
            with self.assertRaises(ValueError): media_plan(session, source)

    def test_hls_prefers_explicit_audio_and_then_lowest_bandwidth(self):
        for audio in (False, True):
            session = Session()
            master = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900\nhigh.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n'
            if audio: master += '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",URI="audio.m3u8"\n'
            selected = SOURCE.rsplit('/', 1)[0] + ('/audio.m3u8' if audio else '/low.m3u8')
            session.files[SOURCE] = master.encode()
            session.files[selected] = PLAYLIST.encode()
            plan = media_plan(session, SOURCE)
            self.assertEqual(session.calls, [SOURCE, selected])
            self.assertEqual(len(plan['chunks']), 2)

    def test_encryption_keys_unknown_formats_live_and_foreign_segments_fail_before_bytes(self):
        cases = [PLAYLIST.replace('#EXTINF:10,', '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:10,', 1),
                 PLAYLIST.replace('#EXTINF:10,', '#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="key.bin"\n#EXTINF:10,', 1),
                 PLAYLIST.replace('#EXT-X-ENDLIST', ''), PLAYLIST.replace('one.ts', 'https://other.invalid/one.ts'),
                 PLAYLIST.replace('one.ts', 'https://tlsvod.kku.ac.kr:9443/one.ts'),
                 PLAYLIST.replace('one.ts', 'one.ts?nodownload=1'), PLAYLIST.replace('one.ts', 'one.m4s'),
                 PLAYLIST + '#EXT-X-BYTERANGE:100@0', PLAYLIST + '#EXT-X-DISCONTINUITY']
        for text in cases:
            session = Session()
            session.files[SOURCE] = text.encode()
            with self.subTest(text=text), self.assertRaises((ValueError, DownloadRestricted)):
                media_plan(session, SOURCE)
            self.assertEqual(session.calls, [SOURCE])
        session = Session()
        session.files[SOURCE] = ('#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n' + PLAYLIST.split('\n', 1)[1]).encode()
        self.assertEqual(len(media_plan(session, SOURCE)['chunks']), 2)

    def test_exact_media_port_is_scoped_and_redirects_cannot_leave_it(self):
        check_download_origin(SOURCE, 'tlsvod.kku.ac.kr:8443')
        for url in (SOURCE.replace('8443', '9443'), SOURCE.replace('https:', 'http:'), SOURCE.replace('tlsvod', 'other'),
                    SOURCE.replace('https://', 'https://name:secret@')):
            with self.assertRaises(DownloadRestricted): check_download_origin(url, 'tlsvod.kku.ac.kr:8443')
        with self.assertRaises(DownloadRestricted): check_download_origin(SOURCE, 'tlsvod.kku.ac.kr')
        request = Request(SOURCE)
        request.allowed_origin = 'tlsvod.kku.ac.kr:8443'
        stream = Mock()
        with self.assertRaises(DownloadRestricted):
            _DownloadRedirectHandler().redirect_request(request, stream, 302, 'Found', {}, SOURCE.replace('8443', '9443'))
        stream.close.assert_called_once()

    def test_bounded_binary_read_and_bad_server_response_close_connections(self):
        session = MoodleSession()
        session.logged_in = True
        headers = Message()
        response = Mock(headers=headers, geturl=lambda: SOURCE)
        response.read.return_value = b'a' * 11
        with patch.object(session.opener, 'open', return_value=response), self.assertRaises(ValueError):
            session.get_bytes(SOURCE, allowed_origin='tlsvod.kku.ac.kr:8443', max_bytes=10)
        response.read.assert_called_once_with(11)
        response.close.assert_called_once()

    def test_download_order_is_preserved_and_html_or_http_denial_never_succeeds(self):
        session = Session()
        plan = media_plan(session, SOURCE)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'media.bin'
            download_media(session, plan, path)
            self.assertEqual(path.read_bytes(), b'GfirstGsecond')
            session.files[plan['chunks'][0]] = b'<html>sign in</html>'
            with self.assertRaises(ValueError): download_media(session, plan, path)
            with patch.object(session, 'get_bytes', side_effect=HTTPError(SOURCE, 403, 'denied', None, None)), self.assertRaises(HTTPError):
                download_media(session, plan, path)

    def test_atomic_audio_reuse_and_failure_cleanup_do_not_change_saved_copy(self):
        provider = Mock()
        provider.save_lecture_media.side_effect = lambda plan, target, progress: target.write_bytes(b'Gsample')
        def convert(command, **kwargs):
            self.assertEqual(command[command.index('-protocol_whitelist') + 1], 'file')
            self.assertNotIn(SOURCE, command)
            Path(command[-1]).write_bytes(b'\x00\x00\x00\x20ftypM4A ' + b'a' * 200)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'audio'
            plan = {'format': 'mpegts', 'sourceKey': 'source'}
            with patch.object(lecture_audio, 'ffmpeg_path', return_value='ffmpeg'), patch.object(lecture_audio.subprocess, 'run', side_effect=convert):
                saved = lecture_audio.extract_audio(provider, 'u', LECTURE, plan, root)
                self.assertFalse(saved['reused'])
                hit = lecture_audio.extract_audio(provider, 'u', LECTURE, plan, root, saved)
                self.assertTrue(hit['reused'])
                self.assertEqual(provider.save_lecture_media.call_count, 1)
                with patch.object(lecture_audio.subprocess, 'run', side_effect=lecture_audio.subprocess.CalledProcessError(1, 'ffmpeg')):
                    with self.assertRaises(RuntimeError):
                        lecture_audio.extract_audio(provider, 'u', LECTURE, {**plan, 'sourceKey': 'replacement'}, root, saved)
            self.assertTrue(Path(saved['localPath']).is_file())
            self.assertFalse(list(root.glob('.audio-*')))
            Path(saved['localPath']).write_bytes(b'corrupt')
            self.assertIsNone(lecture_audio.cached_audio(saved, root, saved['sourceKey']))
            with self.assertRaises(ValueError), patch.object(lecture_audio, 'ffmpeg_path', return_value='ffmpeg'):
                lecture_audio.extract_audio(provider, 'u', {**LECTURE, 'courseId': '../escape'}, plan, root)

    def test_local_selection_requires_course_but_all_completed_request_is_explicit(self):
        other = {**COURSE, 'id': 'tls-course-2', 'name': '다른 과목'}
        with self.assertRaises(ValueError): lecture_audio.select_lectures([COURSE, other], [LECTURE])
        self.assertEqual(lecture_audio.select_lectures([COURSE, other], [LECTURE], course_query='테스트'), [LECTURE])
        self.assertEqual(lecture_audio.select_lectures([COURSE, other], [LECTURE], all_completed=True), [LECTURE])
        self.assertEqual(lecture_audio.select_lectures([COURSE, other], [LECTURE, {**LECTURE, 'completed': False}], all_completed=True), [LECTURE])
        with self.assertRaises(ValueError):
            lecture_audio.select_lectures([COURSE, other], [LECTURE], lecture_ids=['nonexistent'])

    def test_audio_schema_migration_user_scope_completion_and_cli_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'school.db'
            with closing(LocalDatabase(path)) as db:
                db.upsert_tls_snapshot('u', [COURSE], [], [LECTURE], 'u', None)
                db.connection.execute('DROP TABLE lecture_audio')
                db.connection.commit()
            with closing(LocalDatabase(path, read_only=True)) as db:
                self.assertEqual(db.get_lecture_audio('u'), [])

    def test_check_command_has_no_form_media_conversion_or_db_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'school.db'
            with closing(LocalDatabase(path)) as db:
                db.upsert_tls_snapshot('u', [COURSE], [], [LECTURE, {**LECTURE, 'id': 'tls-lecture-4',
                    'externalId': '4', 'title': '미시청', 'completed': False}], 'u', None)
            before = path.read_bytes()
            session, output = Session(), io.StringIO()
            with patch.object(sys, 'argv', ['lecture_audio.py', '--course', '테스트', '--check']), \
                 patch.dict(os.environ, {'UNIVERSITY_AGENT_DB': str(path), 'UNIVERSITY_AGENT_USER_ID': 'u'}), \
                 patch.object(audio_script, '_login_with_retries', return_value=('u', session)) as login, \
                 patch.object(audio_script, 'extract_audio', side_effect=AssertionError('metadata only')), redirect_stdout(output):
                audio_script.main()
            login.assert_called_once()
            self.assertTrue(login.call_args.kwargs['stored_only'])
            result = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual([item['status'] for item in result['data']], ['AVAILABLE', 'SKIPPED'])
            self.assertNotIn(SOURCE, output.getvalue())
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((path.parent / 'audio').exists())
            with closing(LocalDatabase(path)) as db:
                saved = {'lectureId': LECTURE['id'], 'sourceKey': 's', 'localPath': str(Path(folder) / 'a.m4a'),
                         'contentHash': 'h', 'savedAt': '2026-10-12T00:00:00+00:00'}
                db.save_lecture_audio('u', saved)
                self.assertEqual(db.get_lecture_audio('u'), [saved])
                self.assertEqual(db.get_lecture_audio('other'), [])
                with self.assertRaises(ValueError): db.save_lecture_audio('other', saved)
                db.upsert_tls_snapshot('u', [COURSE], [], [{**LECTURE, 'completed': False}], 'u', None)
                self.assertEqual(db.get_lecture_audio('u'), [])


if __name__ == '__main__':
    unittest.main()
