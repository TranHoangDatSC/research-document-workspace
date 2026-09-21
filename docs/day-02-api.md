# Day 2 — Document storage APIs

## Scope

One FastAPI application. Existing project APIs and both health endpoints are retained.
PostgreSQL stores projects and fixed document fields, MongoDB stores flexible metadata,
and MinIO stores original bytes. No frontend service, authentication, LLM, or deployment.

The existing environment is authoritative. In this project:

- PostgreSQL database: `research_document_workspace`.
- MinIO bucket: `research-documents-workspace` (read from `MINIO_BUCKET`).
- `checkpoint-c` is the existing Day 1 test bucket; do not delete it for this exercise.

## Endpoints

| Method | Path | Result |
| --- | --- | --- |
| POST | `/projects/{project_id}/documents` | Multipart upload; returns 201 and aggregated metadata |
| GET | `/projects/{project_id}/documents?limit=20&offset=0` | Fixed metadata list, including pending/failed status |
| GET | `/documents/{document_id}` | PostgreSQL and MongoDB metadata for a ready document |
| GET | `/documents/{document_id}/download` | Original bytes as an attachment |

Multipart fields: `file` (required), `tags` and `authors` (comma-separated),
`custom_metadata` (JSON object string, default `{}`).

## Validation

- At most 10 MiB of file content; empty files rejected.
- `.txt`, `.pdf`, `.docx` extensions only; extension matching is case-insensitive.
- This is an extension filter, NOT file-content verification or malware scanning.
- The file limit is enforced in the endpoint after multipart parsing. It is not
  a global incoming HTTP request-size limit; this package targets local coursework.
- Project/document IDs are UUIDs. Missing valid IDs return 404; malformed input 422.
- Unsupported extension 415, oversized file 413, unavailable storage 503.
- Pending/failed documents cannot download or report ready detail (409).
- Files with the same original name have distinct UUID object keys.
- SHA-256 is stored in MongoDB and checked by integration tests after download.

## Write sequence and partial failure

1. Validate input and existing project.
2. Commit a PostgreSQL row with `pending` status.
3. Put original bytes in MinIO.
4. Insert MongoDB metadata.
5. Commit PostgreSQL status `ready`; return 201.

Before finalization, failures trigger best-effort cleanup in MongoDB and MinIO,
and mark the PostgreSQL record `failed`. A failed SQL row is intentionally retained
as an audit record. If cleanup fails, the document ID and stage are logged.

These are NOT distributed atomic transactions. Process death can leave pending
records. If the final SQL commit acknowledgement is lost, assets are retained
because the database may already have committed `ready`. Inspect the logged
document ID and reconcile manually; automatic recovery is outside Day 2 scope.
Do not delete assets merely because the client received a timeout or 503.

## Test commands (run at project root)

```powershell
python .\scripts\day2_test.py before
# Save state before recreating containers. Do NOT rerun before to prove persistence.
docker compose down
docker compose up -d --wait --wait-timeout 180
python .\scripts\day2_test.py after
```

`down` here has NO `-v`: named volumes remain. `down -v` would delete data.

Controlled outage:

```powershell
docker compose stop mongo
try {
    python .\scripts\day2_test.py failure
    if ($LASTEXITCODE -ne 0) { throw 'Failure test failed.' }
}
finally {
    docker compose start mongo
    docker compose up -d --wait --wait-timeout 180
}
python .\scripts\day2_test.py recovery
```

The host test script uses Python standard library only and talks to localhost:8000.
Read-only storage evidence is collected through `docker compose exec -T web` using
the existing environment; passwords are never printed or copied into reports.

Each phase writes actual results to `docs/day-02-<phase>-result.txt`. Failures produce
nonzero exit codes. State in `docs/day-02-state.json` records project/document IDs
and SHA-256 hashes, not secrets. `after` does not create or upload new data.
The sample files and test project are deliberately retained as demo evidence.

## Validation performed on this package before delivery

Six local FastAPI TestClient tests passed with mocked storage: upload/detail/download
and duplicate names; invalid input and oversize; missing entities; partial-failure
cleanup; uncertain commit retention; Unicode download filenames and size mismatch.
Python files compiled successfully. This does not replace real Compose integration
tests on the student's Windows machine. PowerShell installer has not been executed
on Windows by the assistant.

## Known operational issue resolved before Day 2B

Web initially requested `research_workspace`, but the existing PostgreSQL volume
contained `research_document_workspace`. The environment name was corrected and
containers recreated; data was retained. Bootstrap subsequently passed twice.

## References

- https://fastapi.tiangolo.com/tutorial/request-files/
- https://fastapi.tiangolo.com/advanced/custom-response/
- https://www.psycopg.org/psycopg3/docs/basic/usage.html
- https://github.com/minio/minio-py/blob/master/examples/get_object.py
