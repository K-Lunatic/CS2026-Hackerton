"""Local TLS credential storage: username in config, password in macOS Keychain."""
from __future__ import annotations

import json
import os
import subprocess
from getpass import getpass
from pathlib import Path

SERVICE = "university-agent/tls"
CONFIG_PATH = Path.home() / ".university-agent" / "tls-account.json"


def save(username: str, password: str) -> None:
    if not username or not password:
        raise ValueError("TLS username and password are required")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"username": username}, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)
    try:
        subprocess.run(("/usr/bin/security", "add-generic-password", "-U", "-s", SERVICE, "-a", username, "-w", password), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        CONFIG_PATH.unlink(missing_ok=True)
        raise RuntimeError("macOS Keychain is unavailable; password was not saved") from error


def load() -> tuple[str, str]:
    account = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    username = account["username"]
    result = subprocess.run(("/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", username, "-w"), check=True, capture_output=True, text=True)
    return username, result.stdout.rstrip("\n")


def resolve(username: str | None = None, password: str | None = None) -> tuple[str, str]:
    if username and password:
        return username, password
    try:
        stored_username, stored_password = load()
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError):
        stored_username, stored_password = "", ""
    return username or stored_username or input("TLS username: "), password or stored_password or getpass("TLS password: ")
