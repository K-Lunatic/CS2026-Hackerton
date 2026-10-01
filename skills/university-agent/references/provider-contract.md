# Provider contract and team split

The default runner is Mock TLS so the Skill works immediately after installation. The TLS owner supplies data; feature owners only consume this contract.

```python
class LMSProvider(Protocol):
    def get_courses(self, user_id: str) -> list[dict]: ...
    def get_assignments(self, user_id: str) -> list[dict]: ...
    def get_lectures(self, user_id: str) -> list[dict]: ...
```

Minimum record fields:

- `Course`: `id`, `name`, optional `professor`, `semester`
- `Assignment`: `id`, `courseId`, `title`, `dueAt`, `submissionStatus`, `source`
- `Lecture`: `id`, `courseId`, `title`, `durationSeconds`, `watchedSeconds`, `watchProgress`, `completed`, `source`

Use ISO-8601 timestamps with timezone offsets and the statuses `NOT_SUBMITTED`, `SUBMITTED`, `LATE`, or `UNKNOWN`.

AI integrations should call the Skill commands or consume their JSON; they should not move TLS logic into a vendor-specific prompt or SDK. Add authentication and secret handling only when a real TLS API is available.

## Parallel work

The TLS owner changes `providers/tls_provider.py` and this contract only when the upstream TLS response requires it. Feature owners work independently in `features/assignments.py`, `features/lectures.py`, `features/context.py`, `features/bookmarks.py`, or `features/handover.py`. Keep `MockTLSProvider` compatible until the real adapter is ready.
