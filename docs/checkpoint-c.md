# Checkpoint C

Verified at: 2026-09-21T17:45:19.4586315+07:00

- Compose build and startup: PASS
- Liveness HTTP 200: PASS
- Readiness: PostgreSQL, MongoDB, MinIO up
- PostgreSQL and MongoDB host port bindings: empty
- Persistence after down/up: PASS for all three stores
- PostgreSQL stop: live 200, ready 503
- PostgreSQL recovery: ready 200

## Environment issues resolved
- Missing Dockerfile: created Dockerfile in build context.
- Container name filter mismatch: used actual container name.
- Port mapping text pasted as command: treated as output only.
- Standalone web occupied port 8000: stopped before Compose startup.

## Python in running web container
Python 3.13.15
