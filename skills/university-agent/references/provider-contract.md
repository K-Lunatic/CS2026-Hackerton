# Provider contract and team split

The runner reads the device-local database only; it does not invent sample TLS data. The TLS owner supplies data; feature owners only consume this contract. With no server or cross-device sync layer, `sync_tls.py` logs into KKU TLS and imports normalized data into the current device's local SQLite DB. The database shape is documented in [data-model.md](data-model.md) and implemented in `database/schema.sql`.

`providers/moodle_session.py` handles the Moodle login form, hidden fields such as `logintoken`, redirects, and the in-memory `MoodleSession` cookie. `providers/credentials.py` keeps the username in local config and the password in macOS Keychain. It is intentionally separate from record parsers. Run `scripts/sync_tls.py` for the first and later syncs.

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
- `Resource`: `id`, `courseId`, `title`, `fileName`, `extension`, `mimeType`, `remotePath`, `localPath`, `downloadedAt`, `source`

Use ISO-8601 timestamps with timezone offsets and the statuses `NOT_SUBMITTED`, `SUBMITTED`, `LATE`, or `UNKNOWN`.

AI integrations should call the Skill commands or consume their JSON; they should not move TLS logic into a vendor-specific prompt or SDK. Add authentication and secret handling only when a real TLS API is available.

## Parallel work

The TLS owner changes `providers/tls_provider.py`, `providers/moodle_provider.py`, and this contract only when the upstream TLS response requires it. Feature owners work independently in `features/assignments.py`, `features/lectures.py`, `features/context.py`, `features/bookmarks.py`, or `features/handover.py`. Tests use explicit fixtures without seeding runtime data.
