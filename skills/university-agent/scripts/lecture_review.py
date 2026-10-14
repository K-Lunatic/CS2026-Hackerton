#!/usr/bin/env python3
"""Completed-lecture local transcription, current-host review and targeted evidence."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from features import lecture_review
from features.secret_boundary import redact_output
from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import DownloadRestricted, LoginError
from scripts.sync_tls import _login_with_retries
from storage.local_db import LocalDatabase


def main():
    parser = argparse.ArgumentParser(description='이미 본 강의의 시간표시 전사와 검수')
    sub = parser.add_subparsers(dest='action', required=True)
    speech = sub.add_parser('transcribe')
    speech.add_argument('--lecture-id', required=True)
    speech.add_argument('--start', type=float, default=0)
    speech.add_argument('--duration', type=float, default=120)
    speech.add_argument('--model', type=Path)
    speech.add_argument('--vad-model', type=Path)
    speech.add_argument('--whisper-cli')
    for action in ('show', 'review', 'frame'):
        command = sub.add_parser(action)
        command.add_argument('--record-id', required=True)
        if action == 'review': command.add_argument('--input-file', type=Path, required=True)
        if action == 'frame': command.add_argument('--seconds', type=float, action='append', required=True)
    args = parser.parse_args()
    path = Path(os.environ.get('UNIVERSITY_AGENT_DB', Path.home() / '.university-agent/university.db')).expanduser()
    if not path.is_file():
        raise SystemExit('먼저 학교 연결과 강의 목록을 준비해 주세요.')
    try:
        username, session = _login_with_retries(os.environ.get('TLS_BASE_URL', 'https://tls.kku.ac.kr'),
                                               os.environ.get('TLS_USERNAME'), stored_only=True)
        user, root = os.environ.get('UNIVERSITY_AGENT_USER_ID', username), path.parent
        record = lecture_review.read(root, user, args.record_id) if args.action != 'transcribe' else None
        selected = args.lecture_id if args.action == 'transcribe' else record['lectureId']
        with closing(LocalDatabase(path, read_only=True)) as db:
            lecture = next((item for item in db.get_lectures(user) if item['id'] == selected), None)
            names = {course['id']: course['name'] for course in db.get_courses(user)}
        if not lecture:
            raise ValueError('현재 사용자의 강의를 찾지 못했어요.')
        provider = MoodleTLSProvider(session)
        print('이미 본 강의인지와 현재 제공 상태를 다시 확인하고 있어요…', flush=True)
        plan = provider.get_lecture_media(user, lecture, video=args.action == 'frame')
        if record and (record['playbackKey'] != plan['playbackKey'] or record['title'] != lecture['title'] or
                       record['courseId'] != lecture['courseId']):
            raise ValueError('저장한 전사와 현재 강의가 달라 사용하지 않았어요.')
        if args.action == 'transcribe':
            executable = args.whisper_cli or shutil.which('whisper-cli')
            if not executable:
                raise ValueError('로컬 전사 엔진이 아직 없어요. Whisper.cpp의 whisper-cli를 먼저 준비해 주세요.')
            record = lecture_review.transcribe(provider, user, lecture, plan, root,
                args.model or root / 'models/ggml-small-q5_1.bin', args.vad_model or root / 'models/ggml-silero-v6.2.0.bin',
                executable, start=args.start, duration=args.duration, progress=lambda message: print(message, flush=True))
        elif args.action == 'review':
            if args.input_file.stat().st_size > 200_000:
                raise ValueError('검수 입력이 너무 길어요. 강의 구간을 나눠 주세요.')
            record = lecture_review.save_review(root, user, record['recordId'], json.loads(args.input_file.read_text(encoding='utf-8')))
        elif args.action == 'frame':
            if len(args.seconds) > 3:
                raise ValueError('한 번에 필요한 화면 1–3장만 확인해요.')
            for seconds in args.seconds:
                record = lecture_review.extract_frame(provider, user, lecture, plan, root, record['recordId'], seconds)
        print(json.dumps(redact_output({'courseName': names[lecture['courseId']], 'data': record,
                          'answer': '전사와 검수 기록을 준비했어요. 화면이 필요한 부분은 해당 시각만 확인해요.'}), ensure_ascii=False))
    except (DownloadRestricted, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    except (LoginError, URLError):
        raise SystemExit('학교 연결을 확인하지 못했어요. 저장된 기록으로 현재 상태를 추정하지 않아요.') from None
    except (OSError, sqlite3.Error):
        raise SystemExit('로컬 강의 처리에 필요한 파일·저장 권한을 확인해 주세요. 기존 기록은 보존했어요.') from None


if __name__ == '__main__':
    main()
