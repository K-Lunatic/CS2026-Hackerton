"""Small, deterministic SQLite cache for Everytime read results."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_path() -> Path:
    configured = os.environ.get("EVERYTIME_DB")
    return Path(configured) if configured else Path.home() / ".everytime" / "cache.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scope_key(scope: dict[str, Any]) -> str:
    return json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class LocalCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                cache_key TEXT PRIMARY KEY,
                area TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                source_url TEXT,
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_area_fetched ON cache_entries(area, fetched_at DESC)"
        )
        self.connection.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LocalCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, area: str, scope: dict[str, Any], *, max_age: int | None = None) -> dict[str, Any] | None:
        key = self._key(area, scope)
        row = self.connection.execute(
            "SELECT payload_json, source_url, fetched_at FROM cache_entries WHERE cache_key=?",
            (key,),
        ).fetchone()
        if not row:
            return None
        if max_age is not None and max_age >= 0:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
            if age > max_age:
                return None
        payload = json.loads(row["payload_json"])
        payload["cacheHit"] = True
        payload["cachedAt"] = row["fetched_at"]
        payload["sourceUrl"] = row["source_url"]
        return payload

    def put(
        self,
        area: str,
        scope: dict[str, Any],
        payload: dict[str, Any],
        *,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        clean = dict(payload)
        clean.pop("cacheHit", None)
        clean.pop("cachedAt", None)
        body = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fetched_at = _now()
        self.connection.execute(
            """
            INSERT INTO cache_entries(cache_key, area, scope_json, payload_json, source_url, content_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json=excluded.payload_json,
                source_url=excluded.source_url,
                content_hash=excluded.content_hash,
                fetched_at=excluded.fetched_at
            """,
            (
                self._key(area, scope),
                area,
                scope_key(scope),
                body,
                source_url,
                hashlib.sha256(body.encode("utf-8")).hexdigest(),
                fetched_at,
            ),
        )
        self.connection.commit()
        result = dict(clean)
        result.update(cacheHit=False, cachedAt=fetched_at, sourceUrl=source_url)
        return result

    def _key(self, area: str, scope: dict[str, Any]) -> str:
        return hashlib.sha256(f"{area}\0{scope_key(scope)}".encode("utf-8")).hexdigest()
