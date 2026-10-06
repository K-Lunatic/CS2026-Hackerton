# Provider contract and team split

The runner reads the device-local database only; it does not invent sample TLS data. The TLS owner supplies data; feature owners only consume this contract. With no server or cross-device sync layer, `sync_tls.py` logs into KKU TLS and imports normalized data into the current device's local SQLite DB. The database shape is documented in [data-model.md](data-model.md) and implemented in `database/schema.sql`.

`providers/moodle_session.py` handles the Moodle login form, hidden fields such as `logintoken`, redirects, and the in-memory `MoodleSession` cookie. `providers/credentials.py` keeps the username locally and protects the password with macOS Keychain or Windows DPAPI. It is intentionally separate from record parsers. Run `scripts/sync_tls.py` for the first and later syncs.

For a faster status/list refresh, run `scripts/sync_tls.py --metadata-only`.
This still checks visible download prohibitions and retains valid cached files,
but leaves new files as `NOT_DOWNLOADED` with an explicit reason. It does not
prepare new file contents for study. Run normal sync when those files are needed.
Each stage reports elapsed time and resource checks report progress.
Independent per-course reads use up to four worker sessions, each with its own cookie jar/opener.
The local sync commits completed assignment/lecture/notice stages before fetching more data.
A later failure does not undo earlier stages; the run still reports failure, not full completion.
New originals are saved immediately through `save_file`, not retained as a whole batch of bytes.
They live under `files/<courseId>/<resourceId>/<content SHA-256>/<original filename>`;
different activities and changed versions never overwrite one another. Invalid filesystem characters
are sanitized only for the physical path; the source `fileName` remains unchanged.
Legacy shared paths are not trusted for reuse and are fetched again on a full refresh; old files are kept.
Full refresh revalidates cached originals with ETag/Last-Modified in an adjacent `.http.json` sidecar.
An HTTP 304 reuses bytes. Without validators, a full refresh fetches the file again to detect replacements.
Metadata-only refresh intentionally does not check file content freshness.
`Assignment.dueAt` may be null: missing dates do not remove assignments from collection.

```python
class TLSProvider(Protocol):
    def get_user(self, user_id: str) -> dict | None: ...
    def get_courses(self, user_id: str) -> list[dict]: ...
    def get_assignments(self, user_id: str) -> list[dict]: ...
    def get_lectures(self, user_id: str) -> list[dict]: ...
```

Minimum record fields:

- `Course`: `id`, `name`, optional `professor`, `semester`
- `Assignment`: `id`, `courseId`, `title`, `dueAt`, `submissionStatus`, `source`
- `Lecture`: `id`, `courseId`, `title`, `durationSeconds`, `watchedSeconds`, `watchProgress`, `completed`, optional `availableFrom`, `availableUntil`, `source`
- `Notice`: `id`, `courseId`, `title`, `content`, `publishedAt`, `source`
- `Resource`: `id`, `courseId`, `title`, `fileName`, `extension`, `mimeType`, `remotePath`, `localPath`, `downloadedAt`, `downloadStatus`, `downloadReason`, `source`. `PROHIBITED` means an explicit TLS activity/notice rule matched or the TLS server returned HTTP 403; sync must not fetch file bytes, and study-material features must report the restriction rather than read a local copy.

Use ISO-8601 timestamps with timezone offsets and the statuses `NOT_SUBMITTED`, `SUBMITTED`, `LATE`, or `UNKNOWN`.

`LATE` means submitted late, not an overdue unsubmitted assignment. `UNKNOWN` requires checking and must not be counted as confirmed unsubmitted. `watchProgress` uses percentage points (0–100), so 1.0 is 1%; `completed` is independent.
The local user record also returns `lastSyncedAt`, sourced from that user's snapshot import timestamp, not a shared course timestamp.

AI integrations should call the Skill commands or consume their JSON; they should not move TLS logic into a vendor-specific prompt or SDK. Add authentication and secret handling only when a real TLS API is available.

## Parallel work

The TLS owner changes `providers/tls_provider.py`, `providers/moodle_provider.py`, and this contract only when the upstream TLS response requires it. Feature owners work independently in `features/assignments.py`, `features/lectures.py`, `features/context.py`, `features/bookmarks.py`, or `features/study.py`. Tests use explicit fixtures without seeding runtime data.

## ChatGPT Actions gateway

`server/app.py` composes the same `MoodleSession`, `MoodleTLSProvider` and `LocalDatabase` contracts. Each authenticated TLS account gets a separate hashed-path SQLite file on the server; HTTP callers cannot select a user ID or DB path. TLS passwords arrive only at the browser OAuth login endpoint and are not persisted. Background sync publishes assignments before fetching lectures and notices, so later failures do not discard available assignments. Only successfully synced sections may be read. The gateway does not import the CLI runner's global account state or call feature-specific TLS endpoints.
