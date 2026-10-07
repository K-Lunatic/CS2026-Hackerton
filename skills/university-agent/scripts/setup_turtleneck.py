#!/usr/bin/env python3
"""One installation, one private onboarding flow; keep each service's data separate."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'integrations/everytime/scripts'))

from providers.credentials import CONFIG_PATH
from scripts.sync_tls import _saved_sync_state, open_connection_terminal
from scripts.academic_watch import install_schedule, uninstall_schedule
from everytime_cache import default_path, scope_key


def status() -> dict:
    """Only inspect account metadata and existing DBs, never passwords or cookies."""
    tls = {'status': 'NEEDS_LOGIN'}
    try:
        account = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        user = os.environ.get('UNIVERSITY_AGENT_USER_ID') or account['username']
        username = os.environ.get('TLS_USERNAME')
        if username and username != account['username']:
            raise ValueError('A different account was requested')
        db = Path(os.environ.get('UNIVERSITY_AGENT_DB', Path.home() / '.university-agent/university.db'))
        saved = _saved_sync_state(db, user)
        tls['status'] = 'READY' if {'course_ids', 'course_fingerprints'} <= saved.keys() else 'NEEDS_SETUP'
    except (PermissionError, sqlite3.Error):
        tls = {'status': 'UNAVAILABLE', 'message': '학교 저장소를 읽지 못했어요. 접근 권한과 저장 상태를 먼저 확인해 주세요.'}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    eta = {'status': 'NEEDS_SETUP'}
    db = default_path()
    if db.is_file():
        try:
            with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                row = connection.execute('SELECT payload_json, fetched_at FROM cache_entries WHERE area=? AND scope_json=?',
                                         ('setup', scope_key({'view': 'initial'}))).fetchone()
            if row:
                report = json.loads(row[0])
                eta = {'status': report['status'], 'cachedAt': row[1],
                       **{key: report.get(key, 0) for key in ('courseCount', 'scheduleItemCount', 'reviewCount')}}
                # An interrupted setup must not leave a permanent "please wait" state.
                if eta['status'] == 'RUNNING' and (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).total_seconds() > 600:
                    eta['status'] = 'PARTIAL'
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            eta = {'status': 'UNAVAILABLE', 'message': '에타 저장소를 읽지 못했어요. 접근 권한과 저장 상태를 먼저 확인해 주세요.'}
    return {'tls': tls, 'everytime': eta}


def prepare(only: str | None = None) -> int:
    # This function runs only in the user's private console, not a captured host PTY.
    current = status()
    failed = False
    for service, label in (('tls', '학교 자료'), ('everytime', '에타 시간표와 강의평')):
        if only and only != service:
            continue
        if current[service]['status'] == 'READY':
            print(f'{label}는 이미 준비됐어요. 다시 로그인하지 않을게요.', flush=True)
            continue
        print(f'{label}를 준비할게요.', flush=True)
        try:
            if service == 'tls':
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/sync_tls.py'), '--ensure'])
                ready = result.returncode == 0 and status()['tls']['status'] == 'READY'
            else:
                from session_store import load
                from everytime import Client, initialize
                from connect import connect
                try:
                    cookies = load()
                    print('저장된 에타 연결을 먼저 사용할게요.', flush=True)
                except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                    print('열리는 공식 화면에서 로그인과 2차 인증을 직접 마쳐주세요.', flush=True)
                    cookies = connect(300)
                # Reuse completed steps even when retrying a partly completed first setup.
                ready = initialize(client=Client(cookies), max_age=-1)['status'] == 'READY'
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            ready = False
        if not ready:
            failed = True
            print(f'{label}는 아직 준비되지 않았어요. 연결과 저장 권한을 확인해 주세요. 다른 기능은 계속 준비할게요.', flush=True)
        elif service == 'tls' and current[service]['status'] != 'READY':
            try:
                print(install_schedule(), flush=True)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print(f'학교 자료는 준비됐지만 자동 확인은 아직 켜지 못했어요. {error}', flush=True)
    print('준비를 마쳤어요. 대화로 돌아와 주세요.' if not failed else
          '준비된 기능부터 쓸 수 있어요. 대화에서 아직 연결되지 않은 항목을 확인해 주세요.', flush=True)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description='터틀넥 학교·에타 첫 연결')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--status', action='store_true', help='비밀값 없이 준비 상태만 확인')
    mode.add_argument('--run', action='store_true', help='사용자의 별도 터미널에서 직접 설정')
    mode.add_argument('--watch-install', action='store_true', help='6시간마다 학교 자료를 확인하도록 등록')
    mode.add_argument('--watch-uninstall', action='store_true', help='학교 자료 자동 확인 해제')
    parser.add_argument('--only', choices=('tls', 'everytime'), help='빠진 연결 하나만 준비')
    args = parser.parse_args()
    if args.watch_install:
        try:
            print(install_schedule())
            return 0
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f'자동 확인을 등록하지 못했어요. {error}')
            return 1
    if args.watch_uninstall:
        print(uninstall_schedule())
        return 0
    if args.run:
        result = prepare(args.only)
        return result
    current = status()
    print(json.dumps(current, ensure_ascii=False))
    if args.status or all(item['status'] == 'READY' for key, item in current.items() if not args.only or key == args.only):
        return 0
    if any(item['status'] in ('RUNNING', 'UNAVAILABLE') for key, item in current.items() if not args.only or key == args.only):
        print('이미 진행 중이거나 저장소 확인이 필요해요. 새 연결창을 열지 않을게요.')
        return 1
    command = [sys.executable, str(Path(__file__).resolve()), '--run']
    if args.only:
        command += ['--only', args.only]
    try:
        open_connection_terminal(command)
    except (OSError, subprocess.SubprocessError, SystemExit):
        print('연결창을 열지 못했어요. 별도 터미널에서 실행해 주세요: ' + subprocess.list2cmdline(command))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
