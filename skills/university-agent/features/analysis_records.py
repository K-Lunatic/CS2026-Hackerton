"""Append-only, per-file analysis history, independent of disposable caches."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
from features.material_cache import key


def save(root, user, sources, settings, kind, data, *, operation_id=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'analysis-records.db'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    first = sources[0]
    identifier = key([user, kind, first['resourceId'], operation_id])[:32] if operation_id else secrets.token_hex(16)
    # Store source text once. Unit ranges retain exact chunk boundaries for later audits.
    compact = dict(data)
    position = 0
    if 'units' in data:
        compact['units'] = []
        for unit in data['units']:
            count = len(unit.get('sources', []))
            compact['units'].append({**{k: v for k, v in unit.items() if k != 'sources'},
                                     'sourceRange': [position, position + count]})
            position += count
    record = dict(schemaVersion=2, recordId=identifier, resourceId=first['resourceId'],
        courseId=first['courseId'], title=first['name'], kind=kind,
        analyzedAt=datetime.now(timezone.utc).isoformat(), sourceHash=key(sources),
        sources=sources, settings=settings, data=compact, status='complete',
        validation={'evidence': 'structurally_validated', 'semanticAccuracy': 'not_verified'})
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS analyses (user TEXT, id TEXT, resource TEXT, created TEXT, metadata TEXT, value TEXT, PRIMARY KEY(user,id))')
        db.execute('CREATE INDEX IF NOT EXISTS analyses_history ON analyses(user,created DESC,id DESC)')
        db.execute('CREATE INDEX IF NOT EXISTS analyses_resource_history ON analyses(user,resource,created DESC,id DESC)')
        metadata = {k: v for k, v in record.items() if k not in ('sources', 'data')}
        db.execute('INSERT OR IGNORE INTO analyses VALUES (?,?,?,?,?,?)',
            (user, identifier, first['resourceId'], record['analyzedAt'],
             json.dumps(metadata, ensure_ascii=False), json.dumps(record, ensure_ascii=False)))
        saved = json.loads(db.execute('SELECT value FROM analyses WHERE user=? AND id=?', (user, identifier)).fetchone()[0])
        if any(saved[k] != record[k] for k in ('sources', 'settings', 'kind', 'data')):
            raise ValueError('같은 분석 요청의 결과가 달라졌어요. 저장했던 결과로 재시도하거나 새 분석을 시작해주세요.')
    return identifier


def read(root, user, resources, identifier='', resource_id='', offset=0, limit=20):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('분석 목록은 offset >= 0, limit 1~20으로 조회하세요.')
    allowed = {r['id'] for r in resources if r.get('downloadStatus') != 'PROHIBITED'}
    path = (Path(root) / 'analysis-records.db').resolve()
    if not path.exists():
        if identifier:
            raise ValueError('조회 가능한 분석 기록이 없습니다.')
        return {'records': [], 'nextOffset': None}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        # Attachments belong to the recording account; school sources need current access.
        params = [user, *sorted(allowed)]
        query = 'FROM analyses WHERE user=? AND (resource IN (' + ','.join('?' for _ in allowed) + ") OR resource LIKE 'attachment-%')"
        if identifier:
            row = db.execute('SELECT value ' + query + ' AND id=?', [*params, identifier]).fetchone()
            if not row:
                raise ValueError('조회 가능한 분석 기록이 없습니다. 자료 접근 권한과 다운로드 제한을 확인하세요.')
            record = json.loads(row[0])
            # Candidate answers remain private until exam grading.
            record['data'].pop('candidates', None)
            return record
        if resource_id:
            query += ' AND resource=?'
            params.append(resource_id)
        rows = db.execute('SELECT metadata ' + query + ' ORDER BY created DESC, id DESC LIMIT ? OFFSET ?',
            [*params, limit + 1, offset]).fetchall()
    return {'records': [json.loads(r[0]) for r in rows[:limit]],
            'nextOffset': offset + limit if len(rows) > limit else None}
