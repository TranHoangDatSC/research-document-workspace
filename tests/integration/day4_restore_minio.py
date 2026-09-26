"""Restore application objects into isolated MinIO, then verify downloads."""

import json
import sys
from datetime import datetime, timezone

from day4_restore_postgres import ROOT, BACKUP, PROJECT, COMPOSE, run


REMOTE = r'''
import hashlib
import json
from pathlib import Path
import tarfile

from app.storage import minio_client

stage = "archive-check"

def main():
    global stage

    folder = Path("/backups") / BACKUP_DIRECTORY
    report = json.loads(
        (folder / "backup-report.json").read_text(encoding="utf-8")
    )
    if report["status"] != "BACKUP_CREATED":
        raise RuntimeError("Incomplete backup")

    bucket = report["scope"]["minio_bucket"]
    if bucket != "research-documents-workspace":
        raise RuntimeError("Unexpected bucket")

    path = folder / "minio.tar"
    expected = report["files"]["minio.tar"]
    if path.stat().st_size != expected["bytes"]:
        raise RuntimeError("Archive size mismatch")

    with path.open("rb") as stream:
        archive_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if archive_hash != expected["sha256"]:
        raise RuntimeError("Archive hash mismatch")

    with tarfile.open(path, "r:") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate archive members")

        info = archive.getmember("inventory.json")
        if not info.isfile():
            raise RuntimeError("Invalid inventory entry")

        with archive.extractfile(info) as stream:
            inventory = json.load(stream)

        if inventory["bucket"] != bucket:
            raise RuntimeError("Inventory bucket mismatch")

        objects = inventory["objects"]
        if len(objects) != report["minio_object_count"]:
            raise RuntimeError("Inventory count mismatch")

        keys = [item["key"] for item in objects]
        paths = [item["member"] for item in objects]
        if len(keys) != len(set(keys)) or len(paths) != len(set(paths)):
            raise RuntimeError("Duplicate object keys or content references")
        if set(names) != {"inventory.json", *paths}:
            raise RuntimeError("Unexpected archive entries")

        # Validate every object before creating a destination bucket.
        stage = "object-archive-check"
        for item in objects:
            member = archive.getmember(item["member"])
            if not member.isfile() or member.size != item["size"]:
                raise RuntimeError("Invalid object archive entry")
            with archive.extractfile(member) as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != item["sha256"]:
                raise RuntimeError("Object archive hash mismatch")

        stage = "empty-destination-check"
        client = minio_client()
        if client.bucket_exists(bucket):
            raise RuntimeError(
                "Destination bucket already exists; no overwrite allowed"
            )

        stage = "create-bucket"
        client.make_bucket(bucket)

        stage = "upload-objects"
        for item in objects:
            with archive.extractfile(item["member"]) as stream:
                client.put_object(
                    bucket,
                    item["key"],
                    stream,
                    length=item["size"],
                    content_type=item["content_type"],
                    metadata=item.get("metadata", {}),
                )

        stage = "verify-downloads"
        actual_keys = {
            item.object_name
            for item in client.list_objects(bucket, recursive=True)
        }
        if actual_keys != set(keys):
            raise RuntimeError("Restored object list mismatch")

        checked = []
        for item in objects:
            stat = client.stat_object(bucket, item["key"])
            if stat.size != item["size"]:
                raise RuntimeError("Restored object size mismatch")
            if stat.content_type != item["content_type"]:
                raise RuntimeError("Restored content type mismatch")

            response = client.get_object(bucket, item["key"])
            digest = hashlib.sha256()
            size = 0
            try:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
            finally:
                response.close()
                response.release_conn()

            if size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise RuntimeError("Restored download hash mismatch")

            checked.append({
                "key": item["key"],
                "bytes": size,
                "sha256": digest.hexdigest(),
            })

    return {
        "ok": True,
        "bucket": bucket,
        "archive_sha256": archive_hash,
        "restored_objects": len(checked),
        "verified_downloads": len(checked),
        "objects": checked,
    }

try:
    print(json.dumps(main()))
except Exception as exc:
    # Return only the stage and exception type; do not expose credentials.
    print(json.dumps({
        "ok": False,
        "stage": stage,
        "error_type": type(exc).__name__,
    }))
'''


def main():
    stage = "target-check"
    evidence = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "project": PROJECT,
        "status": "INCOMPLETE",
    }

    output = ROOT / "artifacts/day-04"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence_path = output / f"restore-minio-{stamp}.json"

    try:
        container_id = run(COMPOSE + ["ps", "-q", "minio"])
        if not container_id or "\n" in container_id:
            raise RuntimeError("Expected one running restore MinIO")

        actual_project = run([
            "docker", "inspect", "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            container_id,
        ])
        actual_volume = run([
            "docker", "inspect", "--format",
            '{{range .Mounts}}{{if eq .Destination "/data"}}'
            '{{.Name}}{{end}}{{end}}',
            container_id,
        ])
        networks = json.loads(run([
            "docker", "inspect", "--format",
            "{{json .NetworkSettings.Networks}}",
            container_id,
        ]))

        if actual_project != PROJECT:
            raise RuntimeError("Wrong destination Compose project")
        if actual_volume != "rdw-restore-day4_minio_data":
            raise RuntimeError("Wrong destination MinIO volume")
        if set(networks) != {"rdw-restore-day4_default"}:
            raise RuntimeError("Unexpected destination network")

        evidence["container_id"] = container_id
        evidence["volume"] = actual_volume
        print("Restore destination: PASS", flush=True)

        stage = "minio-restore-and-verify"
        source = REMOTE.replace(
            "BACKUP_DIRECTORY", repr(BACKUP.name)
        )
        result = json.loads(run(
            COMPOSE + [
                "run", "--rm", "--no-deps", "-T",
                "restore_tools", "-c", source,
            ],
            timeout=600,
        ))

        evidence["result"] = result
        if not result.get("ok"):
            raise RuntimeError(
                f"Remote stage={result.get('stage')} "
                f"error={result.get('error_type')}"
            )

        evidence["status"] = "MINIO_RESTORE_PASS"
        print("Bucket:", result["bucket"])
        print("Restored objects:", result["restored_objects"])
        print("Verified download SHA256:", result["verified_downloads"])
        print("MinIO restore checkpoint: PASS")
        return 0

    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["failed_stage"] = stage
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        print(f"FAIL at {stage}: {exc}", flush=True)
        return 1

    finally:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        evidence_path.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print("Evidence:", evidence_path, flush=True)


if __name__ == "__main__":
    sys.exit(main())