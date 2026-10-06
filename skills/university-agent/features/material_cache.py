"""Private, versioned local extraction/analysis cache; no model or network calls."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def fingerprint(root, path):
    """Hash only changed files; never substitute this for source-access checks."""
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    signature = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    identifier = key(str(path))
    cached = get(root, 'fingerprint', '', identifier)
    if cached and cached['signature'] == signature:
        return cached['digest']
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(65536), b''):
            digest.update(block)
    after = path.stat()
    if signature != [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns]:
        raise ValueError('파일이 읽는 도중 변경됐어요. 다시 시도해주세요.')
    # ponytail: normal filesystem change detection, not tamper-proof against privileged metadata manipulation.
    put(root, 'fingerprint', '', identifier, {'signature': signature, 'digest': digest.hexdigest()})
    return digest.hexdigest()


def get(root, kind, user, identifier):
    path = (Path(root) / 'materials.db').resolve()
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT value FROM material_cache WHERE kind=? AND user=? AND key=?',
                         (kind, user, identifier)).fetchone()
    return json.loads(row[0]) if row else None


def put(root, kind, user, identifier, value):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'materials.db'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS material_cache (kind TEXT, user TEXT, key TEXT, value TEXT, PRIMARY KEY(kind,user,key))')
        db.execute('INSERT OR REPLACE INTO material_cache VALUES (?,?,?,?)',
                   (kind, user, identifier, json.dumps(value, ensure_ascii=False, separators=(',', ':'))))
