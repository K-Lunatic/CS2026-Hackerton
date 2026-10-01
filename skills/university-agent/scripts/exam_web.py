#!/usr/bin/env python3
"""Serve a saved exam locally, or wait for its answers for the current host AI."""
import argparse
import json
import time
import threading
import sys
import webbrowser
from run_agent import DB_PATH, USER_ID, database
from features.study import StudySession
from features.exam_web import create_exam_server


def main():
    parser = argparse.ArgumentParser(description='터틀넥 로컬 시험지')
    parser.add_argument('--conversation', required=True)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--wait', type=int, metavar='SECONDS', help='제출 대기 후 AI 채점 데이터를 반환 (1~60초)')
    args = parser.parse_args()
    try:
        session = StudySession(DB_PATH.parent / 'study-sessions.db', USER_ID, args.conversation, database(), DB_PATH.parent / 'files')
        if args.wait is not None:
            if not 1 <= args.wait <= 60:
                raise ValueError('대기 시간은 1~60초입니다.')
            end = time.monotonic() + args.wait
            while True:
                result = session.call({'action': 'status'})
                if result['status'] in ('grading', 'finished') or time.monotonic() >= end:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    return
                time.sleep(1)
        server, url = create_exam_server(session, args.port)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()  # Serve assets before invoking a potentially blocking browser launcher.
        try:
            print(json.dumps({'url': url, 'answer': '터틀넥 시험지가 준비됐어요. 링크에서 답안을 제출해 주세요.'}, ensure_ascii=False), flush=True)
            if not args.no_open:
                try:
                    if not webbrowser.open(url):
                        print('브라우저를 자동으로 열지 못했어요. 위 시험 링크를 열어 주세요.', file=sys.stderr, flush=True)
                except (webbrowser.Error, OSError):
                    print('브라우저를 자동으로 열지 못했어요. 위 시험 링크를 열어 주세요.', file=sys.stderr, flush=True)
            worker.join()
        finally:
            server.shutdown()
            worker.join()
            server.server_close()
    except (ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
