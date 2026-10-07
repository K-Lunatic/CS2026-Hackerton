#!/usr/bin/env python3
"""Keep local TLS data fresh and surface new academic work on the user's device."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.academic_alerts import collect_alerts, fingerprint, format_alerts, notice_ids
from providers.credentials import CONFIG_PATH
from storage.local_db import LocalDatabase


INTERVAL_SECONDS = 6 * 60 * 60
TASK_NAME = "Turtleneck Academic Watch"
PLIST_NAME = "com.k-lunatic.turtleneck-watch.plist"


def paths() -> tuple[Path, str]:
    db_path = Path(os.environ.get("UNIVERSITY_AGENT_DB", Path.home() / ".university-agent" / "university.db"))
    user_id = os.environ.get("UNIVERSITY_AGENT_USER_ID")
    if not user_id:
        try:
            user_id = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["username"]
        except (OSError, ValueError, KeyError, TypeError):
            user_id = ""
    return db_path, user_id


def notify(message: str) -> None:
    """Best-effort native notification; the DB remains the source of truth."""
    title = "터틀넥"
    body = message.replace("\n", " ")[:240]
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'display notification {json.dumps(body, ensure_ascii=False)} with title {json.dumps(title)}'],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        elif os.name == "nt":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$n=New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon=[System.Drawing.SystemIcons]::Information; $n.Visible=$true; "
                f"$n.ShowBalloonTip(10000,{json.dumps(title)},{json.dumps(body)},[System.Windows.Forms.ToolTipIcon]::Info); "
                "Start-Sleep -Seconds 10; $n.Dispose()"
            )
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


def run_sync() -> tuple[bool, str]:
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("sync_tls.py")), "--ensure", "--metadata-only"],
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        return False, "학교 자료를 새로 확인하지 못했어요. 저장된 내용은 그대로 남아 있어요."
    return True, ""


def run_once(*, sync: bool = True, send_notification: bool = True, force_report: bool = False) -> int:
    db_path, user_id = paths()
    if not user_id or not db_path.exists():
        print("터틀넥 자동 확인을 시작하려면 학교 연결을 먼저 마쳐 주세요.")
        return 1
    if sync:
        ok, message = run_sync()
        if not ok:
            print(message)
            return 1
    try:
        with closing(LocalDatabase(db_path, read_only=True)) as database:
            data = collect_alerts(database, user_id)
            state = database.get_sync_state(user_id)
        previous_fingerprint = state.get("watch_alert_fingerprint")
        previous_notice_ids = set(json.loads(state.get("watch_notice_ids", "[]")))
        current_notice_ids = set(notice_ids(data))
        data["newNotices"] = [item for item in data["notices"] if item["id"] in current_notice_ids - previous_notice_ids] if previous_notice_ids else []
        changed = force_report or previous_fingerprint is None or previous_fingerprint != fingerprint(data)
        report = format_alerts(data, changed=changed)
        with closing(LocalDatabase(db_path)) as database:
            database.merge_sync_state(user_id, {
                "watch_alert_fingerprint": fingerprint(data),
                "watch_notice_ids": json.dumps(sorted(current_notice_ids), ensure_ascii=False),
                "watch_checked_at": datetime.now(timezone.utc).isoformat(),
            })
        if report:
            print(report)
            if send_notification:
                notify(report)
        else:
            print("새로 확인할 학사 변경이 없어요.")
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print("저장된 학교 정보를 읽지 못했어요. 연결 상태와 저장 권한을 확인해 주세요.")
        return 1


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / PLIST_NAME


def install_schedule() -> str:
    script = Path(__file__).resolve()
    if sys.platform == "darwin":
        path = _plist_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "Label": "com.k-lunatic.turtleneck-watch",
            "ProgramArguments": [sys.executable, str(script), "--once"],
            "RunAtLoad": True,
            "StartInterval": INTERVAL_SECONDS,
            "StandardOutPath": str(path.parent / "turtleneck-watch.log"),
            "StandardErrorPath": str(path.parent / "turtleneck-watch.error.log"),
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as stream:
            plistlib.dump(payload, stream, fmt=plistlib.FMT_XML)
        os.replace(temporary, path)
        subprocess.run(["launchctl", "unload", str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "load", str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return f"6시간마다 자동 확인을 켰어요. ({path})"
    if os.name == "nt":
        command = subprocess.list2cmdline([sys.executable, str(script), "--once"])
        subprocess.run(["schtasks", "/Create", "/SC", "HOURLY", "/MO", "6", "/TN", TASK_NAME, "/TR", command, "/F"],
                       check=True, capture_output=True, text=True)
        return "6시간마다 자동 확인을 켰어요."
    raise RuntimeError("이 운영체제의 예약 작업은 아직 지원하지 않아요.")


def uninstall_schedule() -> str:
    if sys.platform == "darwin":
        path = _plist_path()
        subprocess.run(["launchctl", "unload", str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        path.unlink(missing_ok=True)
        return "자동 확인을 껐어요."
    if os.name == "nt":
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "자동 확인을 껐어요."
    return "이 운영체제에는 등록된 예약 작업이 없어요."


def main() -> int:
    parser = argparse.ArgumentParser(description="터틀넥 로컬 학사 자동 확인")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="빠른 동기화 후 학사 변경 확인")
    mode.add_argument("--report", action="store_true", help="저장된 학사 상태만 표시")
    mode.add_argument("--install", action="store_true", help="운영체제 예약 작업 등록")
    mode.add_argument("--uninstall", action="store_true", help="운영체제 예약 작업 해제")
    parser.add_argument("--show-current", action="store_true", help="변경이 없어도 현재 할 일을 표시")
    args = parser.parse_args()
    if args.install:
        try:
            print(install_schedule())
            return 0
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"자동 확인을 등록하지 못했어요. {error}")
            return 1
    if args.uninstall:
        print(uninstall_schedule())
        return 0
    return run_once(sync=not args.report, send_notification=not args.report,
                    force_report=args.report or args.show_current)


if __name__ == "__main__":
    raise SystemExit(main())
