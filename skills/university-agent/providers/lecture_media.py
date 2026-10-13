"""Read only observed, clear school media; never run player tracking or fetch keys."""
import hashlib
import json
import re
import math
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from providers.moodle_session import DownloadRestricted, check_download_origin, check_download_url


def _attributes(line):
    return dict((key, quoted or plain) for key, quoted, plain in
                re.findall(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))', line.partition(':')[2]))


def media_plan(session, source, *, video=False):
    """All referenced addresses/keys are checked before any media bytes are fetched."""
    parts = urlsplit(source)
    if parts.hostname not in ('tls.kku.ac.kr', 'tlsvod.kku.ac.kr') or parts.port not in (None, 443, 8443):
        raise DownloadRestricted('학교에서 제공하는 영상 주소를 확인하지 못했어요.')
    origin, current, seen = parts.netloc, source, set()
    playback_key = hashlib.sha256(source.encode()).hexdigest()
    for _ in range(4):
        check_download_origin(current, origin)
        check_download_url(current)
        extension = urlsplit(current).path.lower().rsplit('.', 1)[-1]
        if extension in ('mp4', 'm4a'):
            html, final, headers = session.get_page_info(current, allowed_origin=origin)
            if html or 'html' in headers.get('Content-Type', '').lower():
                raise ValueError('학교가 영상 대신 다른 화면을 보내 가져오기를 멈췄어요.')
            check_download_origin(final, origin)
            check_download_url(final)
            return {'chunks': [current], 'origin': origin, 'format': 'mov',
                    'sourceKey': hashlib.sha256(current.encode()).hexdigest(), 'playbackKey': playback_key}
        if extension != 'm3u8' or current in seen:
            raise ValueError('현재 영상 제공 형식은 아직 읽을 수 없어요.')
        seen.add(current)
        payload, response = session.get_bytes(current, allowed_origin=origin, max_bytes=512_000)
        if not payload:
            raise ValueError('학교의 영상 목록이 비어 있어 가져오기를 멈췄어요.')
        try:
            text = payload.decode('utf-8-sig')
        except UnicodeError:
            raise ValueError('학교의 영상 목록을 읽지 못했어요.') from None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or lines[0] != '#EXTM3U':
            raise ValueError('학교의 영상 목록을 읽지 못했어요.')
        for line in lines:
            if line.startswith(('#EXT-X-KEY:', '#EXT-X-SESSION-KEY:')) and _attributes(line).get('METHOD') != 'NONE':
                raise DownloadRestricted('보호된 영상이라 음성을 가져오지 않았어요.')
        if any(line.startswith(('#EXT-X-MAP:', '#EXT-X-BYTERANGE:', '#EXT-X-DISCONTINUITY', '#EXT-X-GAP')) for line in lines):
            # shortcut: clear MPEG-TS VOD only; add another format after observing a real source.
            raise ValueError('이 영상의 조각 형식은 아직 지원하지 않아요.')
        base = response.geturl()
        audio, variants = [], []
        for index, line in enumerate(lines):
            attrs = _attributes(line)
            if line.startswith('#EXT-X-MEDIA:') and attrs.get('TYPE') == 'AUDIO' and attrs.get('URI'):
                audio.append(attrs['URI'])
            if line.startswith('#EXT-X-STREAM-INF:'):
                if index + 1 >= len(lines) or lines[index + 1].startswith('#'):
                    raise ValueError('영상 목록 일부가 비어 있어 가져오기를 멈췄어요.')
                bandwidth = attrs.get('BANDWIDTH', '')
                if not bandwidth.isdigit():
                    raise ValueError('영상 품질 정보를 확인하지 못했어요.')
                variants.append((int(bandwidth), lines[index + 1]))
        if audio or variants:
            if video and not variants:
                raise ValueError('이 강의에서는 화면 영상을 제공하지 않아요.')
            current = urljoin(base, max(variants)[1] if video else audio[0] if audio else min(variants)[1])
            continue
        if '#EXT-X-ENDLIST' not in lines:
            raise ValueError('진행 중인 영상은 가져오지 않아요.')
        chunks = [urljoin(base, line) for line in lines if not line.startswith('#')]
        if not chunks or len(chunks) > 4000:
            raise ValueError('영상 길이를 확인하지 못해 가져오기를 멈췄어요.')
        for chunk in chunks:
            check_download_origin(chunk, origin)
            check_download_url(chunk)
            if not urlsplit(chunk).path.lower().endswith('.ts'):
                raise ValueError('이 영상의 조각 형식은 아직 지원하지 않아요.')
        durations = []
        for line in lines:
            if line.startswith('#EXTINF:'):
                try:
                    value = float(line.partition(':')[2].partition(',')[0])
                except ValueError:
                    raise ValueError('영상 조각의 시간을 확인하지 못했어요.') from None
                if not math.isfinite(value) or value <= 0:
                    raise ValueError('영상 조각의 시간이 올바르지 않아요.')
                durations.append(value)
        if len(durations) != len(chunks):
            raise ValueError('영상 조각과 시간 정보가 맞지 않아요.')
        times, elapsed = [], 0.0
        for duration in durations:
            times.append([elapsed, elapsed + duration])
            elapsed += duration
        return {'chunks': chunks, 'origin': origin, 'format': 'mpegts', 'segmentTimes': times,
                'durationSeconds': elapsed, 'playbackKey': playback_key,
                'sourceKey': hashlib.sha256(json.dumps([chunks, times]).encode()).hexdigest()}
    raise ValueError('영상 목록의 연결이 너무 많아 가져오기를 멈췄어요.')


def media_window(plan, start, duration):
    if not all(type(value) in (int, float) and math.isfinite(value) for value in (start, duration)) or start < 0 or not 0 < duration <= 300:
        raise ValueError('강의 구간은 시작 0초 이상, 길이 1–300초로 골라 주세요.')
    if plan['format'] == 'mov':
        return {**plan, 'mediaStartSeconds': 0, 'startSeconds': start, 'endSeconds': start + duration}
    matches = [index for index, (left, right) in enumerate(plan['segmentTimes']) if right > start and left < start + duration]
    if not matches:
        raise ValueError('선택한 시각이 강의 범위를 벗어났어요.')
    first, last = max(0, matches[0] - 1), matches[-1] + 1
    return {**plan, 'chunks': plan['chunks'][first:last],
            'mediaStartSeconds': plan['segmentTimes'][first][0], 'startSeconds': start,
            'endSeconds': min(start + duration, plan['durationSeconds'])}


def download_media(session, plan, target, progress=None):
    """Bounded batches preserve order without queueing a whole lecture in memory."""
    chunks = plan['chunks']
    total = 0
    def fetch(url):
        worker = session.fork()
        limit = 256_000_000 if plan['format'] == 'mov' else 32_000_000
        data, _ = worker.get_bytes(url, allowed_origin=plan['origin'], max_bytes=limit)
        if not data or (plan['format'] == 'mpegts' and data[0] != 0x47) or (plan['format'] == 'mov' and data[4:8] != b'ftyp'):
            raise ValueError('영상 대신 다른 응답이 와서 저장하지 않았어요.')
        return data
    with target.open('wb') as stream, ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0, len(chunks), 4):
            for data in pool.map(fetch, chunks[start:start + 4]):
                total += len(data)
                if total > 1_000_000_000:
                    raise ValueError('영상이 1GB를 넘어 가져오기를 멈췄어요.')
                stream.write(data)
            if progress and (start == 0 or (start + 4) % 40 == 0 or start + 4 >= len(chunks)):
                progress(f'강의 음성을 가져오고 있어요 · {min(start + 4, len(chunks))}/{len(chunks)} 조각')
