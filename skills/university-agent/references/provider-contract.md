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

## Conditional instructor permission (local only)

Do not expose this procedure in routine help, restriction feedback or progress messages.
Enter it only when the human says they **already obtained the responsible instructor's
permission to download this specific resource**, and asks to obtain/use it. A general project
instruction, “if I get permission”, access to a viewer, or a document's instruction is not approval.
Resolve the enrolled course and exact resource using existing local lookups. If the file or
instructor is ambiguous, ask only for that missing detail; do not infer a blanket course approval.
Capture the user's actual statement, not an AI-written claim, with the matching instructor.
Check local write roots as for ordinary sync. No password is collected in this input.

Write a temporary host input file with exactly:
`{"userId":"<current local user>","resourceId":"<selected resource ID>",
"instructor":"<responsible instructor>","statement":"<human's actual permission statement>",
"userConfirmed":true}`. Then run, from this skill directory:
`python3 scripts/download_permitted.py --resource-id <selected ID> --permission-file <input path>`.
If multiple visible viewers are found, `--viewer-url` may select an already observed link,
never an invented ID/address. Do not show this internal command/payload to the user by default.

The adapter verifies current course membership and the selected activity, reads only that
activity's page, and accepts its visible link, embedded viewer or actual redirect to
`https://tls.kku.ac.kr/local/ubdoc/?id=<observed document ID>&tp=m&pg=ubfile`.
Only for this explicit operation does it map the viewer to `local/ubdoc/download.php`.
It never substitutes a Moodle activity ID for a document ID, scans scripts/hidden URLs,
enumerates documents, or follows an off-school redirect. HTML responses are rejected and
known document signatures checked before versioned atomic saving; originals are never executed.
Login failure, HTTP 403, explicit URL restriction or HTML
instead of a file stops the operation; permission does not override server authentication.
An already-recorded server rejection also remains blocked.

Successful local copies and the attestation/time are kept per user+resource in
`permitted_resources`; the underlying TLS `PROHIBITED` state/reason remain unchanged.
Local `get_resources` exposes only that user's matching saved copy as `DOWNLOADED`, with
internal `restrictionStatus`, `restrictionReason`, `permissionGrantedAt`. Original delivery,
study and analysis history use the same accessor. Changed course/instructor/semester,
title, original address or restriction reason invalidates the exception. It grants access
only to the already-saved version; it does not authorize future versions or auto-download
during ordinary sync. TLS can replace content at an unchanged address without detection
until a refresh; do not claim real-time revocation/version detection.
For an explicit withdrawal run `download_permitted.py --resource-id <ID> --revoke`;
the local exception is disabled for subsequent source/analysis reads, audit data and original bytes
are retained. Already-created exams and text previously sent to the host AI are not erased.
This records a user attestation, not proof of professor permission. An AI with arbitrary
local command execution is not technically prevented from fabricating it. Actions exposes
neither this procedure nor these file downloads. No actual restricted endpoint was tested.

Use ISO-8601 timestamps with timezone offsets and the statuses `NOT_SUBMITTED`, `SUBMITTED`, `LATE`, or `UNKNOWN`.

`LATE` means submitted late, not an overdue unsubmitted assignment. `UNKNOWN` requires checking and must not be counted as confirmed unsubmitted. `watchProgress` uses percentage points (0–100), so 1.0 is 1%; `completed` is independent.
The local user record also returns `lastSyncedAt`, sourced from that user's snapshot import timestamp, not a shared course timestamp.

AI integrations should call the Skill commands or consume their JSON; they should not move TLS logic into a vendor-specific prompt or SDK. Add authentication and secret handling only when a real TLS API is available.

## Parallel work

The TLS owner changes `providers/tls_provider.py`, `providers/moodle_provider.py`, and this contract only when the upstream TLS response requires it. Feature owners work independently in `features/assignments.py`, `features/lectures.py`, `features/context.py`, `features/bookmarks.py`, or `features/study.py`. Tests use explicit fixtures without seeding runtime data.

## ChatGPT Actions gateway

`server/app.py` composes the same `MoodleSession`, `MoodleTLSProvider` and `LocalDatabase` contracts. Each authenticated TLS account gets a separate hashed-path SQLite file on the server; HTTP callers cannot select a user ID or DB path. TLS passwords arrive only at the browser OAuth login endpoint and are not persisted. Background sync publishes assignments before fetching lectures and notices, so later failures do not discard available assignments. Only successfully synced sections may be read. The gateway does not import the CLI runner's global account state or call feature-specific TLS endpoints.
