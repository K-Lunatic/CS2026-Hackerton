"""Local timestamped ASR and immutable source text with append-only host reviews."""
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from time import monotonic
import wave

from features.lecture_audio import ffmpeg_path, _digest
from features.material_cache import key
from providers.lecture_media import media_window


def _db(root, *, write=False):
    path = Path(root) / 'lecture-review.db'
    if write:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        db = sqlite3.connect(path)
        db.executescript('CREATE TABLE IF NOT EXISTS transcripts(user TEXT,id TEXT,value TEXT,PRIMARY KEY(user,id));'
                         'CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,user TEXT,record TEXT,kind TEXT,value TEXT);')
        return db
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) if path.is_file() else None


def read(root, user, identifier):
    db = _db(root)
    if db is None:
        raise ValueError('저장된 강의 전사가 없어요.')
    with closing(db):
        row = db.execute('SELECT value FROM transcripts WHERE user=? AND id=?', (user, identifier)).fetchone()
        if not row:
            raise ValueError('이 사용자의 전사 기록을 찾지 못했어요.')
        record = json.loads(row[0])
        events = db.execute('SELECT kind,value FROM events WHERE user=? AND record=? ORDER BY id', (user, identifier)).fetchall()
    record['frames'], record['review'], record['reviewStatus'] = [], None, 'needsReview'
    for kind, value in events:
        if kind == 'frame':
            record['frames'].append(json.loads(value))
        elif kind == 'review':
            record['review'] = json.loads(value)
            record['reviewStatus'] = 'reviewed' if all(item['status'] == 'reviewed' for item in record['review']['segments']) else 'needsEvidence'
    return record


def _save(root, user, record):
    with closing(_db(root, write=True)) as db, db:
        db.execute('INSERT OR IGNORE INTO transcripts VALUES(?,?,?)', (user, record['recordId'], json.dumps(record, ensure_ascii=False)))
        stored = json.loads(db.execute('SELECT value FROM transcripts WHERE user=? AND id=?', (user, record['recordId'])).fetchone()[0])
        if stored['segments'] != record['segments'] or stored['audioHash'] != record['audioHash']:
            raise ValueError('같은 전사 요청의 결과가 달라 원문을 덮어쓰지 않았어요.')


def _event(root, user, identifier, kind, data):
    read(root, user, identifier)
    with closing(_db(root, write=True)) as db, db:
        db.execute('INSERT INTO events(user,record,kind,value) VALUES(?,?,?,?)',
                   (user, identifier, kind, json.dumps(data, ensure_ascii=False)))


def _run(command, *, timeout=300):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('로컬 강의 처리를 마치지 못했어요. 도구·모델·메모리를 확인해 주세요. 기존 기록은 보존했어요.') from None


def _folder(root, user, identifier):
    root = Path(root).resolve()
    path = root / 'lecture-review-files' / key(user)[:16] / identifier
    if not re.fullmatch(r'[a-f0-9]{32}', identifier) or not path.resolve().is_relative_to(root):
        raise ValueError('허용된 강의 저장 폴더 밖에는 쓰지 않아요.')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.resolve().is_relative_to(root):
        raise ValueError('강의 저장 폴더가 달라져 멈췄어요.')
    return path


def normalize_transcript(value, start, duration):
    if (not isinstance(value, dict) or not isinstance(value.get('model'), dict) or value['model'].get('multilingual') is not True or
        not isinstance(value.get('transcription'), list) or len(value['transcription']) > 2000):
        raise ValueError('한국어 전사 응답 형식을 확인하지 못했어요.')
    segments, previous = [], 0.0
    for item in value['transcription']:
        if not isinstance(item, dict) or not isinstance(item.get('offsets'), dict):
            raise ValueError('전사 구간 형식이 맞지 않아요.')
        offsets = item.get('offsets', {})
        left, right = offsets.get('from'), offsets.get('to')
        text = item.get('text')
        if (not all(type(number) in (int, float) and math.isfinite(number) for number in (left, right)) or
            left < 0 or right < left or left / 1000 < previous or right / 1000 > duration + 0.5 or
            not isinstance(text, str) or len(text) > 20000):
            raise ValueError('전사의 시각이나 본문이 올바르지 않아 저장하지 않았어요.')
        previous = left / 1000
        if not text.strip():
            continue
        segments.append({'segmentId': f's{len(segments) + 1}', 'startSeconds': round(start + left / 1000, 3),
                         'endSeconds': round(start + min(right / 1000, duration), 3), 'rawText': text.strip()})
    if not segments:
        raise ValueError('이 구간에서 전사할 말을 확인하지 못했어요. 무음이거나 인식이 어려운 구간일 수 있어요.')
    return segments


def transcribe(provider, user, lecture, plan, root, model, vad_model, executable, *, start=0, duration=120, progress=None):
    if lecture.get('completed') is not True:
        raise ValueError('시청 완료 강의만 전사할 수 있어요.')
    window = media_window(plan, start, duration)
    model, vad_model = Path(model).resolve(strict=True), Path(vad_model).resolve(strict=True)
    version = _run([str(executable), '--version']).stdout.strip()
    settings = {'engine': 'whisper.cpp', 'version': version, 'modelHash': _digest(model), 'vadHash': _digest(vad_model),
                'language': 'ko', 'pipelineVersion': 1,
                'start': int(start) if float(start).is_integer() else start,
                'end': int(window['endSeconds']) if float(window['endSeconds']).is_integer() else window['endSeconds']}
    identifier = key([user, lecture['id'], lecture['courseId'], lecture['title'], plan['sourceKey'], settings])[:32]
    try:
        cached = read(root, user, identifier)
    except ValueError:
        cached = None
    if cached:
        try:
            audio = Path(cached['audioPath']).resolve(strict=True)
            if audio.is_relative_to(Path(root).resolve()) and _digest(audio) == cached['audioHash']:
                return {**cached, 'reused': True}
        except OSError:
            pass
    target = _folder(root, user, identifier)
    begun = monotonic()
    with tempfile.TemporaryDirectory(prefix='.review-', dir=target) as folder:
        media, audio, output = Path(folder) / 'media.bin', Path(folder) / 'audio.wav', Path(folder) / 'result'
        provider.save_lecture_media(window, media, progress)
        fetched = monotonic()
        _run([ffmpeg_path(), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file',
              '-f', window['format'], '-i', str(media), '-ss', str(start - window['mediaStartSeconds']),
              '-t', str(window['endSeconds'] - start), '-map', '0:a:0', '-vn', '-sn', '-dn',
              '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-f', 'wav', str(audio)])
        with wave.open(str(audio), 'rb') as source:
            if source.getframerate() != 16000 or source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError('전사용 음성 형식을 확인하지 못했어요.')
            actual_duration = source.getnframes() / 16000
        if not 0 < actual_duration <= duration + 0.5:
            raise ValueError('전사용 음성 길이가 맞지 않아요.')
        if progress:
            progress('교수님 말씀을 이 맥북에서 글로 옮기고 있어요…')
        ready = monotonic()
        process = _run([str(executable), '-m', str(model), '-vm', str(vad_model), '--vad', '-l', 'ko',
                        '-f', str(audio), '-oj', '-of', str(output), '-np', '-t', '4'])
        finished = monotonic()
        value = json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
        segments = normalize_transcript(value, start, actual_duration)
        audio_hash = _digest(audio)
        final_audio = target / ('audio-' + audio_hash + '.wav')
        if cached:
            if cached['audioHash'] != audio_hash or cached['segments'] != segments:
                raise ValueError('다시 처리한 내용이 달라 기존 원음·전사를 덮어쓰지 않았어요.')
            final_audio = Path(cached['audioPath'])
            if final_audio.parent.resolve() != target.resolve():
                raise ValueError('기존 원음의 저장 위치가 달라 복구하지 않았어요.')
        os.replace(audio, final_audio)
    record = {'recordId': identifier, 'lectureId': lecture['id'], 'courseId': lecture['courseId'], 'title': lecture['title'],
              'playbackKey': plan['playbackKey'], 'sourceKey': plan['sourceKey'], 'startSeconds': start,
              'endSeconds': round(start + actual_duration, 3), 'wholeLecture': False, 'settings': settings,
              'audioPath': str(final_audio), 'audioHash': audio_hash, 'segments': segments,
              'transcribedAt': datetime.now(timezone.utc).isoformat(), 'asrSeconds': round(finished - ready, 2),
              'downloadSeconds': round(fetched - begun, 2), 'prepareSeconds': round(ready - fetched, 2),
              'backendVerification': 'not_reported', 'validation': 'ASR output; not semantically verified'}
    _save(root, user, record)
    return {**read(root, user, identifier), 'reused': False}


def save_review(root, user, identifier, payload):
    record = read(root, user, identifier)
    if not isinstance(payload, dict) or set(payload) != {'segments'} or not isinstance(payload['segments'], list):
        raise ValueError('검수 내용을 구간별로 전달해 주세요.')
    originals = {item['segmentId']: item for item in record['segments']}
    frames = {item['frameId']: item for item in record['frames']}
    seen = set()
    for item in payload['segments']:
        if (not isinstance(item, dict) or set(item) != {'segmentId', 'reviewedText', 'status', 'reason', 'frameIds'} or
            not isinstance(item['segmentId'], str) or
            item['segmentId'] not in originals or item['segmentId'] in seen or
            item['status'] not in ('reviewed', 'unresolved', 'needsFrame', 'needsAudio') or
            not all(isinstance(item[key], str) and len(item[key]) <= 20000 for key in ('reviewedText', 'reason')) or
            not item['reviewedText'].strip() or not isinstance(item['frameIds'], list) or
            any(not isinstance(frame, str) or frame not in frames for frame in item['frameIds'])):
            raise ValueError('검수 구간·상태·화면 근거가 맞지 않아요. 원문은 변경하지 않았어요.')
        if (item['status'] != 'reviewed' or item['reviewedText'] != originals[item['segmentId']]['rawText']) and not item['reason'].strip():
            raise ValueError('보완하거나 확인이 필요한 구간에는 이유를 남겨 주세요.')
        for frame in item['frameIds']:
            original = originals[item['segmentId']]
            if not original['startSeconds'] <= frames[frame]['requestedSeconds'] <= original['endSeconds']:
                raise ValueError('다른 전사 구간의 화면을 근거로 붙이지 않아요.')
        seen.add(item['segmentId'])
    if seen != set(originals):
        raise ValueError('선택 구간을 모두 검수하기 전에는 완료로 저장하지 않아요.')
    _event(root, user, identifier, 'review', {**payload, 'reviewedAt': datetime.now(timezone.utc).isoformat(),
                                           'reviewer': 'current host AI', 'validation': 'structure only; not proof of accuracy'})
    return read(root, user, identifier)


def extract_frame(provider, user, lecture, plan, root, identifier, seconds):
    record = read(root, user, identifier)
    if (lecture.get('completed') is not True or record['lectureId'] != lecture['id'] or
        record['courseId'] != lecture['courseId'] or record['title'] != lecture['title'] or
        record['playbackKey'] != plan['playbackKey']):
        raise ValueError('전사한 강의와 현재 영상이 달라 화면을 가져오지 않았어요.')
    pending = record.get('review') or {'segments': []}
    originals = {item['segmentId']: item for item in record['segments']}
    allowed = [originals[item['segmentId']] for item in pending['segments'] if item['status'] == 'needsFrame']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not any(
            item['startSeconds'] <= seconds <= item['endSeconds'] for item in allowed):
        raise ValueError('화면이 필요하다고 검수한 구간의 시각만 골라 주세요.')
    frame_id = key([user, identifier, plan['sourceKey'], seconds])[:32]
    target = _folder(root, user, identifier) / (frame_id + '.png')
    if any(frame['frameId'] == frame_id and target.is_file() and _digest(target) == frame['contentHash'] for frame in record['frames']):
        return record
    window = media_window(plan, max(0, seconds - 2), 5)
    with tempfile.TemporaryDirectory(prefix='.frame-', dir=target.parent) as folder:
        media, image = Path(folder) / 'media.bin', Path(folder) / 'frame.png'
        provider.save_lecture_media(window, media)
        offset = seconds - window['mediaStartSeconds']
        process = _run([ffmpeg_path(), '-nostdin', '-hide_banner', '-loglevel', 'info', '-y', '-protocol_whitelist', 'file',
                        '-f', window['format'], '-i', str(media), '-an', '-sn', '-dn', '-vf',
                        f'setpts=PTS-STARTPTS,select=gte(t\\,{offset}),showinfo', '-frames:v', '1', str(image)])
        with image.open('rb') as source:
            if source.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ValueError('강의 화면을 확인하지 못했어요.')
        timestamp = re.search(r'\bpts_time:\s*([0-9.eE+-]+)', process.stderr)
        if not timestamp:
            raise ValueError('추출한 화면의 시각을 확인하지 못했어요.')
        frame_time = window['mediaStartSeconds'] + float(timestamp[1])
        if abs(frame_time - seconds) > 2:
            raise ValueError('요청한 시각과 화면이 달라 근거로 저장하지 않았어요.')
        content_hash = _digest(image)
        os.replace(image, target)
    _event(root, user, identifier, 'frame', {'frameId': frame_id, 'requestedSeconds': seconds, 'frameSeconds': round(frame_time, 3),
           'timingIsApproximate': True, 'localPath': str(target), 'contentHash': content_hash,
           'sourceKey': plan['sourceKey'], 'createdAt': datetime.now(timezone.utc).isoformat()})
    return read(root, user, identifier)
