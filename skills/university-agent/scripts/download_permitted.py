#!/usr/bin/env python3
"""Explicit, single-file instructor permission; not part of ordinary sync."""
import argparse
from contextlib import closing
import json
import os
import subprocess
import sqlite3
from pathlib import Path
import sys
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from providers.credentials import CONFIG_PATH, load
from providers.moodle_provider import MoodleTLSProvider, validate_permission
from providers.moodle_session import DownloadRestricted, LoginError, MoodleSession
from storage.local_db import LocalDatabase
from sync_tls import store_resource


def main():
    parser = argparse.ArgumentParser(description='Process one explicitly permitted local resource')
    parser.add_argument('--resource-id', required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--permission-file', type=Path)
    action.add_argument('--revoke', action='store_true')
    parser.add_argument('--viewer-url')
    args = parser.parse_args()
    path = Path(os.environ.get('UNIVERSITY_AGENT_DB', Path.home() / '.university-agent/university.db')).expanduser()
    try:
        # Load and validate the attestation before credentials or network access.
        if args.permission_file and args.permission_file.stat().st_size > 16000:
            raise ValueError('허락 내용이 너무 길어 자료를 가져오지 않았어요.')
        permission = json.loads(args.permission_file.read_text(encoding='utf-8')) if args.permission_file else None
        if permission is not None:
            validate_permission(permission, permission.get('userId') if isinstance(permission, dict) else None, args.resource_id)
        elif not args.revoke:
            raise ValueError('이 자료에 대해 확인된 허락 내용이 없어 가져오지 않았어요.')
        try:
            if args.revoke:
                username = os.environ.get('UNIVERSITY_AGENT_USER_ID') or json.loads(CONFIG_PATH.read_text(encoding='utf-8'))['username']
            else:
                username, password = load()
        except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
            raise SystemExit('학교 연결이 먼저 필요해요. 비밀번호는 대화에 보내지 말고 연결창에서 입력해 주세요.') from error
        user = os.environ.get('UNIVERSITY_AGENT_USER_ID', username)
        if permission is not None:
            validate_permission(permission, user, args.resource_id)
        if not path.is_file():
            raise ValueError('먼저 학교 자료 목록을 가져와 주세요.')
        with closing(LocalDatabase(path)) as database:
            original = next((r for r in database.get_resources(user, include_permissions=False)
                             if r['id'] == args.resource_id), None)
            if not original:
                raise ValueError('선택한 수업 자료를 찾지 못했어요.')
            if args.revoke:
                database.revoke_resource_permission(user, args.resource_id)
                print('이 자료에 대한 허락 기록을 해제했어요. 저장했던 원본은 지우지 않았어요.', flush=True)
                return
            print(f"{original['title']}의 수업 자료 주소를 확인하고 있어요…", flush=True)
            session = MoodleSession()
            session.login(username, password)
            provider = MoodleTLSProvider(session, lambda message: print(message, flush=True))
            item = provider.download_permitted_resource(user, original, permission, args.viewer_url)
            print('자료를 받았어요. 원래 파일 이름으로 보관할게요…', flush=True)
            saved = store_resource(path.parent / 'files', item)
            database.save_permitted_resource(user, original, saved, permission)
            print('자료를 보관했어요. 이제 이 파일을 사용할 수 있어요.', flush=True)
    except json.JSONDecodeError as error:
        raise SystemExit('허락 내용을 읽지 못했어요. 이번에는 자료를 가져오지 않았어요.') from error
    except (LoginError, URLError) as error:
        raise SystemExit('학교에서 자료를 받지 못했어요. 이번에는 보관하지 않았어요.') from error
    except (DownloadRestricted, ValueError) as error:
        raise SystemExit(str(error)) from error
    except sqlite3.Error as error:
        raise SystemExit('자료 보관을 마무리하지 못했어요. 저장돼 있던 파일은 그대로 남겨 뒀어요.') from error
    except OSError as error:
        raise SystemExit('자료를 보관할 폴더에 접근하지 못했어요. 기존 파일은 그대로 남겨 뒀어요.') from error


if __name__ == '__main__':
    main()
