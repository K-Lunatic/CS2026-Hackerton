#!/usr/bin/env python3
"""Explicit completed-lecture audio request, with stored login and no player window."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.lecture_audio import extract_audio, select_lectures
from providers.moodle_provider import MoodleTLSProvider
from providers.moodle_session import DownloadRestricted, LoginError
from scripts.sync_tls import _login_with_retries
from storage.local_db import LocalDatabase


def main():
    parser = argparse.ArgumentParser(description='끝까지 본 강의의 음성만 로컬에 보관')
    parser.add_argument('--course', default='')
    parser.add_argument('--lecture', default='')
    parser.add_argument('--lecture-id', action='append', default=[])
    parser.add_argument('--all-completed', action='store_true', help='명시적으로 요청한 전체 시청 완료 강의')
    parser.add_argument('--check', action='store_true', help='완료·제한·제공 형식만 확인하고 음성은 받지 않음')
    args = parser.parse_args()
    path = Path(os.environ.get('UNIVERSITY_AGENT_DB', Path.home() / '.university-agent/university.db')).expanduser()
    if not path.is_file():
        raise SystemExit('먼저 학교 계정을 연결하고 강의 목록을 가져와 주세요.')
    try:
        username, session = _login_with_retries(os.environ.get('TLS_BASE_URL', 'https://tls.kku.ac.kr'),
                                               os.environ.get('TLS_USERNAME'), stored_only=True)
        user = os.environ.get('UNIVERSITY_AGENT_USER_ID', username)
        with closing(LocalDatabase(path, read_only=True)) as database:
            courses, lectures = database.get_courses(user), database.get_lectures(user)
            cached = {item['lectureId']: item for item in database.get_lecture_audio(user)}
        selected = select_lectures(courses, lectures, course_query=args.course, lecture_query=args.lecture,
                                   lecture_ids=args.lecture_id, all_completed=args.all_completed)
        names = {course['id']: course['name'] for course in courses}
        provider = MoodleTLSProvider(session)
        results, notices = [], {}
        for index, lecture in enumerate(selected, 1):
            item = {'courseName': names[lecture['courseId']], 'title': lecture['title']}
            print(f"강의 {index}/{len(selected)} · {item['courseName']} · {item['title']}", flush=True)
            if lecture.get('completed') is not True:
                results.append({**item, 'status': 'SKIPPED', 'reason': '시청 완료로 기록되지 않은 강의라 제외했어요. 최신 상태가 다르면 학교 목록을 새로 확인해 주세요.'})
                continue
            try:
                # Check each selected course's current notices once, never use stale permissions.
                if lecture['courseId'] not in notices:
                    reader = MoodleTLSProvider(session)
                    reader._courses = [course for course in provider.get_courses(user) if course['id'] == lecture['courseId']]
                    if not reader._courses:
                        raise ValueError('현재 수강 중인 과목이 아니에요.')
                    notices[lecture['courseId']] = reader.get_notices(user)
                    provider._course_pages.update(reader._course_pages)
                plan = provider.get_lecture_media(user, lecture, notices[lecture['courseId']])
                if args.check:
                    results.append({**item, 'status': 'AVAILABLE', 'reason': '현재 시청 완료와 제공 형식을 확인했어요. 음성은 아직 받지 않았어요.'})
                    continue
                saved = extract_audio(provider, user, lecture, plan, path.parent / 'audio',
                                      cached.get(lecture['id']), lambda message: print(message, flush=True))
                with closing(LocalDatabase(path)) as database:
                    database.save_lecture_audio(user, saved)
                results.append({**item, 'status': 'SAVED', 'localPath': saved['localPath'],
                                'savedAt': saved['savedAt'], 'reused': saved['reused']})
                print('이미 보관한 음성이 있어 바로 가져왔어요.' if saved['reused'] else '음성을 보관했어요. 임시 영상은 정리했어요.', flush=True)
            except HTTPError as error:
                error.close()
                if error.code == 401:
                    raise LoginError('학교 연결이 만료됐어요. 다시 연결해 주세요.') from None
                reason = {403: '학교에서 영상 받기를 거부해 제외했어요.', 404: '학교에서 영상을 찾지 못했어요.',
                          429: '학교에서 잠시 요청을 제한했어요. 여기서 멈추고 나중에 이어갈게요.'}.get(error.code, '학교가 영상 요청을 처리하지 못했어요.')
                results.append({**item, 'status': 'FAILED', 'reason': reason})
                if error.code == 429:
                    break
            except (DownloadRestricted, ValueError, RuntimeError) as error:
                results.append({**item, 'status': 'SKIPPED', 'reason': str(error)})
            except (URLError, TimeoutError, OSError):
                results.append({**item, 'status': 'FAILED', 'reason': '연결 또는 저장을 마무리하지 못했어요. 기존 보관 파일은 그대로 뒀어요.'})
        print(json.dumps({'data': results, 'transcribed': False, 'answer': '강의 음성 확인을 마쳤어요. 전사나 시험 내용 검수는 아직 하지 않았어요.'}, ensure_ascii=False))
    except (LoginError, URLError, RuntimeError):
        raise SystemExit('학교 연결을 확인하지 못했어요. 비밀번호는 대화가 아니라 학교 연결창에서 입력해 주세요.') from None
    except (ValueError, sqlite3.Error, OSError) as error:
        raise SystemExit(str(error) if isinstance(error, ValueError) else '학교 기록이나 음성 저장소를 읽지 못했어요. 폴더 접근 권한을 확인해 주세요.') from None


if __name__ == '__main__':
    main()
