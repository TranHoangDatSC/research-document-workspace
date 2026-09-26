"""Read-only cross-store verification of the isolated restored workspace."""

import json
import sys
from datetime import datetime, timezone

from day4_restore_postgres import ROOT, PROJECT, COMPOSE, run


REMOTE = r'''
import hashlib
import json
import os

from psycopg.rows import dict_row
from app.storage import postgres_connection, mongo_client, minio_client

stage = "configuration-check"

def require(condition, message):
    if not condition:
        raise ValueError(message)

def main():
    global stage

    database = "research_document_workspace"
    bucket = "research-documents-workspace"

    require(os.environ["POSTGRES_DB"] == database, "Unexpected PostgreSQL DB")
    require(os.environ["MONGO_DB"] == database, "Unexpected MongoDB DB")
    require(os.environ["MINIO_BUCKET"] == bucket, "Unexpected MinIO bucket")

    stage = "postgres-read"
    with postgres_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT id FROM projects")
            project_ids = {str(row["id"]) for row in cursor.fetchall()}

            cursor.execute("""
                SELECT id, project_id, object_name, size_bytes, status
                FROM documents
            """)
            documents = cursor.fetchall()

    require(
        all(str(row["project_id"]) in project_ids for row in documents),
        "Document references a missing project",
    )
    require(
        not any(row["status"] in ("pending", "deleting") for row in documents),
        "Unfinished document operations exist",
    )

    ready = [row for row in documents if row["status"] == "ready"]
    ready_ids = {str(row["id"]) for row in ready}
    require(bool(ready), "No ready documents to verify")

    stage = "mongo-read"
    with mongo_client() as client:
        details = list(
            client[database]["document_details"].find({}, {"_id": 0})
        )

    metadata = {item["document_id"]: item for item in details}
    require(len(metadata) == len(details), "Duplicate metadata document IDs")
    require(
        set(metadata) == ready_ids,
        "MongoDB metadata IDs do not match PostgreSQL ready IDs",
    )

    stage = "minio-object-list"
    client = minio_client()
    keys = {
        item.object_name
        for item in client.list_objects(bucket, recursive=True)
    }
    expected_keys = {row["object_name"] for row in ready}
    require(len(expected_keys) == len(ready), "Duplicate PostgreSQL object keys")
    require(keys == expected_keys, "MinIO objects do not match ready documents")

    stage = "linked-download-hashes"
    for row in ready:
        info = metadata[str(row["id"])]
        response = client.get_object(bucket, row["object_name"])
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

        require(size == row["size_bytes"], "File size differs from PostgreSQL")
        require(
            digest.hexdigest() == info.get("sha256"),
            "File SHA256 differs from MongoDB metadata",
        )

    return {
        "ok": True,
        "projects": len(project_ids),
        "documents": len(documents),
        "ready_documents": len(ready),
        "other_documents": len(documents) - len(ready),
        "mongodb_metadata": len(metadata),
        "minio_objects": len(keys),
        "verified_linked_downloads": len(ready),
    }

try:
    print(json.dumps(main()))
except Exception as exc:
    print(json.dumps({
        "ok": False,
        "stage": stage,
        "error_type": type(exc).__name__,
        "detail": str(exc) if isinstance(exc, ValueError) else "Storage check failed",
    }))
'''


def main():
    evidence = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "project": PROJECT,
        "status": "INCOMPLETE",
    }
    output = ROOT / "artifacts/day-04"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output / f"verify-restore-{stamp}.json"

    try:
        # Check the three destination containers before reading their data.
        for service in ("postgres", "mongo", "minio"):
            container_id = run(COMPOSE + ["ps", "-q", service])
            if not container_id or "\n" in container_id:
                raise RuntimeError(f"Expected one running {service} container")

            actual_project = run([
                "docker", "inspect", "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}',
                container_id,
            ])
            networks = json.loads(run([
                "docker", "inspect", "--format",
                "{{json .NetworkSettings.Networks}}",
                container_id,
            ]))

            if actual_project != PROJECT:
                raise RuntimeError("Wrong destination project")
            if set(networks) != {"rdw-restore-day4_default"}:
                raise RuntimeError("Unexpected destination network")

        result = json.loads(run(
            COMPOSE + [
                "run", "--rm", "--no-deps", "-T",
                "restore_tools", "-c", REMOTE,
            ],
            timeout=300,
        ))
        evidence["checks"] = result
        print(json.dumps(result, indent=2), flush=True)

        if not result.get("ok"):
            raise RuntimeError("Cross-store verification failed")

        evidence["status"] = "RESTORE_CROSS_STORE_PASS"
        print("Restore cross-store verification: PASS")
        return 0

    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        print(f"FAIL: {exc}")
        return 1

    finally:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print("Evidence:", path)


if __name__ == "__main__":
    sys.exit(main())