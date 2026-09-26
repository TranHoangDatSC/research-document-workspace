"""Restore MongoDB only into the isolated Day 4 project."""

import hashlib
import json
import sys
from datetime import datetime, timezone

from day4_restore_postgres import ROOT, BACKUP, PROJECT, COMPOSE, run


DATABASE = "research_document_workspace"


def mongo_json(expression):
    # Credentials stay inside the MongoDB container.
    source = """
const connection = new Mongo("mongodb://127.0.0.1:27017");
connection.getDB("admin").auth(
    process.env.MONGO_INITDB_ROOT_USERNAME,
    process.env.MONGO_INITDB_ROOT_PASSWORD
);
const target = connection.getDB(DATABASE_NAME);
print(JSON.stringify(EXPRESSION));
"""
    source = source.replace("DATABASE_NAME", json.dumps(DATABASE))
    source = source.replace("EXPRESSION", expression)

    output = run(COMPOSE + [
        "exec", "-T", "mongo",
        "mongosh", "--quiet", "--nodb", "--eval", source,
    ])
    return json.loads(output)


def main():
    stage = "archive-check"
    evidence = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "project": PROJECT,
        "database": DATABASE,
        "status": "INCOMPLETE",
    }

    output = ROOT / "artifacts/day-04"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence_path = output / f"restore-mongo-{stamp}.json"

    try:
        report = json.loads(
            (BACKUP / "backup-report.json").read_text(encoding="utf-8")
        )
        if report["status"] != "BACKUP_CREATED":
            raise RuntimeError("Backup was not completed")
        if report["scope"]["mongo_database"] != DATABASE:
            raise RuntimeError("Unexpected source database")

        archive = BACKUP / "mongo.archive.gz"
        expected = report["files"]["mongo.archive.gz"]

        if archive.stat().st_size != expected["bytes"]:
            raise RuntimeError("MongoDB archive size mismatch")

        with archive.open("rb") as stream:
            actual_hash = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()

        if actual_hash != expected["sha256"]:
            raise RuntimeError("MongoDB archive hash mismatch")

        evidence["archive_sha256"] = actual_hash
        print("MongoDB archive: size + SHA256 PASS", flush=True)

        stage = "target-check"
        container_id = run(COMPOSE + ["ps", "-q", "mongo"])
        if not container_id or "\n" in container_id:
            raise RuntimeError("Expected one running restore MongoDB")

        actual_project = run([
            "docker", "inspect", "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            container_id,
        ])
        actual_volume = run([
            "docker", "inspect", "--format",
            '{{range .Mounts}}{{if eq .Destination "/data/db"}}'
            '{{.Name}}{{end}}{{end}}',
            container_id,
        ])

        if actual_project != PROJECT:
            raise RuntimeError("Wrong destination Compose project")
        if actual_volume != "rdw-restore-day4_mongo_data":
            raise RuntimeError("Wrong destination MongoDB volume")

        evidence["container_id"] = container_id
        evidence["volume"] = actual_volume
        print("Restore destination: PASS", flush=True)

        stage = "empty-database-check"
        collections = mongo_json("target.getCollectionNames()")
        if collections:
            raise RuntimeError(
                "Destination database has collections. "
                "Stop; do not erase or overwrite it."
            )
        print("Destination database has no collections: PASS", flush=True)

        stage = "mongo-restore"
        remote = (
            'exec mongorestore --host=127.0.0.1 --port=27017 '
            '--username="$MONGO_INITDB_ROOT_USERNAME" '
            '--password="$MONGO_INITDB_ROOT_PASSWORD" '
            '--authenticationDatabase=admin '
            '--archive --gzip --nsInclude="$1.*" --stopOnError'
        )

        with archive.open("rb") as stream:
            run(
                COMPOSE + [
                    "exec", "-T", "mongo",
                    "sh", "-c", remote, "restore", DATABASE,
                ],
                stdin=stream,
                timeout=600,
            )
        print("MongoDB restore command: PASS", flush=True)

        stage = "restored-data-check"
        collections = mongo_json("target.getCollectionNames()")
        if "document_details" not in collections:
            raise RuntimeError("Missing restored document_details collection")

        details = mongo_json("""({
            metadata_count: target.document_details.countDocuments({}),
            unique_document_id_index:
                target.document_details.getIndexes().some(index =>
                    index.unique === true &&
                    Object.keys(index.key).length === 1 &&
                    index.key.document_id === 1
                ),
            invalid_document_ids: target.document_details.countDocuments({
                $or: [
                    {document_id: {$not: {$type: "string"}}},
                    {document_id: ""}
                ]
            })
        })""")

        if not details["unique_document_id_index"]:
            raise RuntimeError("Missing unique document_id index")
        if details["invalid_document_ids"] != 0:
            raise RuntimeError("Invalid document_id values")

        evidence["collections"] = collections
        evidence["checks"] = details
        evidence["status"] = "MONGO_RESTORE_PASS"

        print(json.dumps(details, indent=2), flush=True)
        print("MongoDB restore checkpoint: PASS", flush=True)
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