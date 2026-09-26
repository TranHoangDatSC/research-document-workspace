"""Application-data backup. Run on the host with Python 3.11+.

No secrets are read from .env or printed.
Binary dumps are written directly by subprocess, not through PowerShell.
Restore verification is a separate checkpoint.
"""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "http://127.0.0.1:8001"

CONFIG_SOURCE = """
import json, os
print(json.dumps({
    "postgres_database": os.environ["POSTGRES_DB"],
    "mongo_database": os.environ["MONGO_DB"],
    "minio_bucket": os.environ["MINIO_BUCKET"],
}))
"""

EXPORT_SOURCE = r'''
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile

from app.storage import postgres_connection, minio_client

# Web has stopped. Refuse to call an interrupted operation a clean snapshot.
with postgres_connection() as connection:
    unfinished = connection.execute(
        "SELECT count(*) FROM documents WHERE status IN ('pending', 'deleting')"
    ).fetchone()[0]
if unfinished:
    raise RuntimeError("Unfinished pending/deleting documents; reconcile first")

client = minio_client()
bucket = os.environ["MINIO_BUCKET"]

# This backup format covers an unversioned application's current objects.
versioning = client.get_bucket_versioning(bucket)
if versioning.status in ("Enabled", "Suspended"):
    raise RuntimeError("Versioned bucket requires a version-aware backup")

inventory = {"bucket": bucket, "objects": []}

with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
    for index, item in enumerate(client.list_objects(bucket, recursive=True)):
        key = item.object_name
        stat = client.stat_object(bucket, key)
        response = client.get_object(bucket, key)

        digest = hashlib.sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as content:
            try:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    content.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            finally:
                response.close()
                response.release_conn()

            if size != stat.size:
                raise RuntimeError("Object size changed during backup")

            # Archive paths are generated, never taken from user filenames.
            member_name = f"objects/{index:08d}.bin"
            member = tarfile.TarInfo(member_name)
            member.size = size
            content.seek(0)
            archive.addfile(member, content)

        inventory["objects"].append({
            "key": key,
            "member": member_name,
            "size": size,
            "sha256": digest.hexdigest(),
            "content_type": stat.content_type or "application/octet-stream",
            "metadata": {
                k: v for k, v in (stat.metadata or {}).items()
                if k.lower().startswith("x-amz-meta-")
            },
        })

    encoded = json.dumps(inventory, ensure_ascii=False).encode("utf-8")
    member = tarfile.TarInfo("inventory.json")
    member.size = len(encoded)
    archive.addfile(member, io.BytesIO(encoded))
'''


def command(args, *, data=None, output=None, timeout=180):
    """Keep stderr private: tool errors could contain connection details."""
    result = subprocess.run(
        args,
        cwd=ROOT,
        input=data,
        stdout=output if output is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}")
    return result.stdout


def compose(*args, **kwargs):
    return command(["docker", "compose", *args], **kwargs)


def wait_ready(seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                BASE_URL + "/health/ready", timeout=20
            ) as response:
                body = json.load(response)
                if response.status == 200 and body == {
                    "status": "ready",
                    "services": {
                        "postgresql": "up",
                        "mongodb": "up",
                        "minio": "up",
                    },
                }:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("Readiness did not recover within the allowed time")


def digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_archive(folder, filename, args, data=None):
    partial = folder / (filename + ".partial")
    with partial.open("xb") as stream:
        command(args, data=data, output=stream, timeout=1200)

    if partial.stat().st_size == 0:
        raise RuntimeError(f"Empty archive: {filename}")

    final = folder / filename
    partial.rename(final)
    return final


def verify_minio_archive(path, expected_bucket):
    with tarfile.open(path, "r:") as archive:
        stream = archive.extractfile("inventory.json")
        if stream is None:
            raise RuntimeError("Missing MinIO inventory")
        with stream:
            inventory = json.load(stream)

        if inventory["bucket"] != expected_bucket:
            raise RuntimeError("Unexpected MinIO bucket")

        for item in inventory["objects"]:
            member = archive.getmember(item["member"])
            if not member.isfile() or member.size != item["size"]:
                raise RuntimeError("Invalid object archive entry")

            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("Missing object content")
            with stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != item["sha256"]:
                raise RuntimeError("MinIO backup object hash mismatch")

    return len(inventory["objects"])


def main():
    if not (ROOT / "docker-compose.yaml").is_file():
        raise RuntimeError("Run this script from the expected project")

    compose("config", "--quiet")
    wait_ready(seconds=30)

    config = json.loads(
        compose(
            "exec", "-T", "web", "python", "-",
            data=CONFIG_SOURCE.encode("utf-8"),
        )
    )

    web_id = compose("ps", "-q", "web").decode().strip()
    if not web_id or "\n" in web_id:
        raise RuntimeError("Expected exactly one running web container")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / "artifacts" / "backups" / (
        stamp + "-" + uuid4().hex[:8]
    )
    folder.mkdir(parents=True, exist_ok=False)

    helper_name = "rdw-backup-" + uuid4().hex
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": config,
        "status": "INCOMPLETE",
        "restore_verified": False,
        "files": {},
    }
    report_path = folder / "backup-report.json"
    stage = "stop-web"
    backup_error = None
    recovery_error = None

    print("Backup directory:", folder, flush=True)

    try:
        compose("stop", "-t", "60", "web")

        state = json.loads(command([
            "docker", "inspect", "--format", "{{json .State}}", web_id
        ]))
        if state["Running"] or state["ExitCode"] != 0:
            raise RuntimeError("Web did not stop cleanly; backup cancelled")

        stage = "minio-export"
        minio_path = write_archive(
            folder,
            "minio.tar",
            [
                "docker", "compose", "run",
                "--rm", "--no-deps", "-T",
                "--name", helper_name,
                "--entrypoint", "python", "web", "-",
            ],
            data=EXPORT_SOURCE.encode("utf-8"),
        )

        stage = "minio-verify"
        count = verify_minio_archive(
            minio_path, config["minio_bucket"]
        )
        report["minio_object_count"] = count
        print(f"MinIO archive hashes verified: {count} objects", flush=True)

        stage = "postgres-dump"
        pg_script = (
            'export PGPASSWORD="$POSTGRES_PASSWORD"; '
            'exec pg_dump --host=127.0.0.1 '
            '--username="$POSTGRES_USER" --dbname="$1" '
            '--format=custom --no-owner --no-acl'
        )
        write_archive(
            folder,
            "postgres.dump",
            [
                "docker", "compose", "exec", "-T", "postgres",
                "sh", "-c", pg_script, "backup",
                config["postgres_database"],
            ],
        )
        print("PostgreSQL dump completed", flush=True)

        stage = "mongo-dump"
        mongo_script = (
            'exec mongodump --host=127.0.0.1 --port=27017 '
            '--username="$MONGO_INITDB_ROOT_USERNAME" '
            '--password="$MONGO_INITDB_ROOT_PASSWORD" '
            '--authenticationDatabase=admin '
            '--db="$1" --archive --gzip'
        )
        write_archive(
            folder,
            "mongo.archive.gz",
            [
                "docker", "compose", "exec", "-T", "mongo",
                "sh", "-c", mongo_script, "backup",
                config["mongo_database"],
            ],
        )
        print("MongoDB dump completed", flush=True)

        stage = "archive-hashes"
        for name in ("postgres.dump", "mongo.archive.gz", "minio.tar"):
            path = folder / name
            report["files"][name] = {
                "bytes": path.stat().st_size,
                "sha256": digest_file(path),
            }

    except (Exception, KeyboardInterrupt) as exc:
        backup_error = f"{stage}: {type(exc).__name__}: {exc}"

    finally:
        # Only this run's temporary exporter may be removed.
        try:
            subprocess.run(
                ["docker", "rm", "-f", helper_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except Exception:
            pass

        try:
            compose("start", "web")
            wait_ready()
            report["web_recovered"] = True
            print("Web recovery: PASS", flush=True)
        except (Exception, KeyboardInterrupt) as exc:
            recovery_error = f"{type(exc).__name__}: {exc}"
            report["web_recovered"] = False

        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["backup_error"] = backup_error
        report["recovery_error"] = recovery_error
        if backup_error is None and recovery_error is None:
            report["status"] = "BACKUP_CREATED"

        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if backup_error or recovery_error:
        print("Backup checkpoint: FAIL")
        if backup_error:
            print(backup_error)
        if recovery_error:
            print("Recovery:", recovery_error)
        print("Report:", report_path)
        return 1

    print("Backup checkpoint: PASS")
    print("Restore verification: NOT RUN")
    print("Report:", report_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        sys.exit(1)