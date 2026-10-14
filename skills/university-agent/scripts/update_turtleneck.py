#!/usr/bin/env python3
"""Check and safely update an installed Turtleneck package from the trusted repo."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError


REPO = "K-Lunatic/CS2026-Hackerton"
BRANCH = "dev"
REMOTE_PLUGIN = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/plugin.json"
REMOTE_ARCHIVE = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
CHECK_INTERVAL_SECONDS = 6 * 60 * 60


def version_tuple(value: str) -> tuple[int, ...]:
    parts = str(value).lstrip("v").split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"지원하지 않는 버전 형식: {value}")
    return tuple(int(part) for part in parts)


def is_newer(remote: str, local: str) -> bool:
    return version_tuple(remote) > version_tuple(local)


def find_package_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / "plugin.json").is_file():
            return candidate
    return None


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def package_root() -> Path | None:
    root = find_package_root(skill_root())
    if root is not None:
        return root
    # Older local installs contain only skills/university-agent, not plugin.json.
    return skill_root() if (skill_root() / "SKILL.md").is_file() else None


def development_checkout(root: Path | None) -> bool:
    return bool(root and (root / ".git").exists())


def _read_plugin(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "turtleneck-updater/0.4"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def _cache_path() -> Path:
    return Path(os.environ.get("UNIVERSITY_AGENT_UPDATE_CACHE", Path.home() / ".university-agent" / "update-check.json"))


def _extract_archive(bundle: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for member in bundle.infolist():
        destination = (root / member.filename).resolve()
        if destination != root and root not in destination.parents:
            raise ValueError("업데이트 압축 파일의 경로를 확인하지 못했어요.")
        bundle.extract(member, root)


def _recent_cache(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(value["checkedAt"])
        if (datetime.now(timezone.utc) - checked).total_seconds() < CHECK_INTERVAL_SECONDS:
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def check(*, force: bool = False) -> dict[str, str | bool]:
    root = package_root()
    if root is None:
        return {"status": "UNAVAILABLE", "message": "설치된 터틀넥 버전을 확인할 수 없어요."}
    local = _read_plugin(root / "plugin.json") if (root / "plugin.json").is_file() else {}
    local_version = str(local.get("version", "0.0.0"))
    cache = None if force else _recent_cache(_cache_path())
    if cache:
        remote_version = str(cache.get("remoteVersion", local_version))
    else:
        try:
            remote_version = str(json.loads(_fetch(REMOTE_PLUGIN))["version"])
        except (OSError, ValueError, KeyError, TypeError):
            return {"status": "UNAVAILABLE", "localVersion": local_version, "message": "새 버전을 확인하지 못했어요. 현재 버전으로 계속 사용할게요."}
        try:
            path = _cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"checkedAt": datetime.now(timezone.utc).isoformat(), "remoteVersion": remote_version}), encoding="utf-8")
        except OSError:
            pass
    try:
        newer = is_newer(remote_version, local_version)
    except ValueError:
        return {"status": "UNAVAILABLE", "localVersion": local_version, "message": "버전 정보를 읽지 못했어요."}
    return {"status": "UPDATE_AVAILABLE" if newer else "CURRENT", "localVersion": local_version, "remoteVersion": remote_version,
            "message": f"터틀넥 {remote_version} 버전이 있어요." if newer else f"터틀넥 {local_version} 버전이 최신이에요."}


def apply_update(*, force: bool = False) -> dict[str, str | bool]:
    installed_package = package_root()
    target = skill_root()
    if installed_package is None or development_checkout(installed_package):
        return {"status": "SKIPPED", "message": "개발 중인 Git 저장소는 자동으로 덮어쓰지 않아요. 최신 dev를 확인해 주세요."}
    state = check(force=force)
    if state.get("status") != "UPDATE_AVAILABLE":
        return state
    try:
        archive = _fetch(REMOTE_ARCHIVE)
        with tempfile.TemporaryDirectory() as folder:
            archive_path = Path(folder) / "turtleneck.zip"
            archive_path.write_bytes(archive)
            with zipfile.ZipFile(archive_path) as bundle:
                _extract_archive(bundle, Path(folder))
            extracted = next(Path(folder).glob(f"{REPO.rsplit('/', 1)[1]}-*/skills/university-agent"), None)
            if extracted is None:
                raise ValueError("업데이트 파일 구조를 확인하지 못했어요.")
            staging = Path(folder) / "staging"
            shutil.copytree(extracted, staging)
            shutil.copytree(staging, target, dirs_exist_ok=True)
            source_plugin = extracted.parents[1] / "plugin.json"
            if source_plugin.is_file():
                shutil.copy2(source_plugin, installed_package / "plugin.json")
            source_claude = extracted.parents[1] / '.claude-plugin/plugin.json'
            if source_claude.is_file() and (installed_package / '.claude-plugin/plugin.json').is_file():
                shutil.copy2(source_claude, installed_package / '.claude-plugin/plugin.json')
        return {"status": "UPDATED", "message": f"터틀넥 {state['remoteVersion']} 버전으로 업데이트했어요."}
    except (OSError, ValueError, zipfile.BadZipFile, URLError):
        return {"status": "FAILED", "message": "업데이트를 끝내지 못했어요. 설치본을 다시 확인해 주세요. 로그인·학습 데이터는 변경하지 않았어요."}


def main() -> int:
    parser = argparse.ArgumentParser(description="터틀넥 버전 확인 및 명시적 업데이트")
    parser.add_argument("--auto", action="store_true", help="새 버전 확인만 수행")
    parser.add_argument("--apply", action="store_true", help="확인된 새 버전을 명시적으로 적용")
    parser.add_argument("--force", action="store_true", help="캐시를 무시하고 버전 확인")
    args = parser.parse_args()
    if args.auto and args.apply:
        parser.error("--auto와 --apply는 함께 사용할 수 없습니다.")
    result = apply_update(force=args.force) if args.apply else check(force=args.force)
    print(result["message"])
    return 0 if result["status"] not in {"FAILED", "UNAVAILABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
