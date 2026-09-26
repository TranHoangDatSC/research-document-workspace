"""Restore PostgreSQL only into the isolated Day 4 Compose project."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "artifacts/backups/20260926T125605Z-a1bf6680"
PROJECT = "rdw-restore-day4"
DATABASE = "research_document_workspace"

COMPOSE = [
    "docker", "compose",
    "-p", PROJECT,
    "-f", str(ROOT / "docker-compose.restore.yaml"),
]


def run(args, *, stdin=None, timeout=120):
    result = subprocess.run(
        args,
        cwd=ROOT,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        # Do not print tool stderr containing possible connection information.
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}"
        )
    return result.stdout.decode("utf-8").strip()


def sql(statement):
    remote = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        'exec psql --host=127.0.0.1 --username="$POSTGRES_USER" '
        '--dbname="$1" -X -A -t -v ON_ERROR_STOP=1 -c "$2"'
    )
    return run(COMPOSE + [
        "exec", "-T", "postgres",
        "sh", "-c", remote, "restore-check",
        DATABASE, statement,
    ])


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
    evidence_path = output / f"restore-postgres-{stamp}.json"

    try:
        report = json.loads(
            (BACKUP / "backup-report.json").read_text(encoding="utf-8")
        )
        if report["status"] != "BACKUP_CREATED":
            raise RuntimeError("Backup was not completed")
        if report["scope"]["postgres_database"] != DATABASE:
            raise RuntimeError("Unexpected source database")

        verified = {}
        for name in ("postgres.dump", "mongo.archive.gz", "minio.tar"):
            path = BACKUP / name
            expected = report["files"][name]

            if path.stat().st_size != expected["bytes"]:
                raise RuntimeError(f"Archive size mismatch: {name}")

            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()

            if actual != expected["sha256"]:
                raise RuntimeError(f"Archive hash mismatch: {name}")

            verified[name] = actual
            print(f"{name}: size + SHA256 PASS", flush=True)

        evidence["archive_sha256"] = verified

        stage = "target-check"
        container_id = run(COMPOSE + ["ps", "-q", "postgres"])
        if not container_id or "\n" in container_id:
            raise RuntimeError("Expected one running restore PostgreSQL")

        actual_project = run([
            "docker", "inspect", "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            container_id,
        ])
        actual_volume = run([
            "docker", "inspect", "--format",
            '{{range .Mounts}}'
            '{{if eq .Destination "/var/lib/postgresql/data"}}'
            '{{.Name}}{{end}}{{end}}',
            container_id,
        ])

        if actual_project != PROJECT:
            raise RuntimeError("Wrong destination Compose project")
        if actual_volume != "rdw-restore-day4_postgres_data":
            raise RuntimeError("Wrong destination PostgreSQL volume")
        if sql("SELECT current_database()") != DATABASE:
            raise RuntimeError("Wrong destination database")

        evidence["container_id"] = container_id
        evidence["volume"] = actual_volume
        print("Restore destination: PASS", flush=True)

        stage = "empty-database-check"
        count = int(sql("""
            SELECT count(*)
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname <> 'information_schema'
              AND n.nspname !~ '^pg_'
              AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
        """))
        if count != 0:
            raise RuntimeError(
                "Destination is not empty. Stop; do not erase or overwrite it."
            )
        print("Destination has no user tables/views/sequences: PASS", flush=True)

        stage = "postgres-restore"
        remote = (
            'export PGPASSWORD="$POSTGRES_PASSWORD"; '
            'exec pg_restore --host=127.0.0.1 '
            '--username="$POSTGRES_USER" --dbname="$1" '
            '--no-owner --no-acl --exit-on-error --single-transaction'
        )

        # Stream the binary archive directly to Docker, not via PowerShell.
        with (BACKUP / "postgres.dump").open("rb") as stream:
            run(
                COMPOSE + [
                    "exec", "-T", "postgres",
                    "sh", "-c", remote, "restore", DATABASE,
                ],
                stdin=stream,
                timeout=600,
            )
        print("PostgreSQL restore command: PASS", flush=True)

        stage = "restored-data-check"
        counts = json.loads(sql("""
            SELECT json_build_object(
                'projects', (SELECT count(*) FROM projects),
                'documents', (SELECT count(*) FROM documents),
                'ready_documents', (
                    SELECT count(*) FROM documents WHERE status = 'ready'
                ),
                'documents_without_project', (
                    SELECT count(*)
                    FROM documents AS d
                    LEFT JOIN projects AS p ON p.id = d.project_id
                    WHERE p.id IS NULL
                )
            )
        """))

        if counts["documents_without_project"] != 0:
            raise RuntimeError("Restored documents have missing projects")

        evidence["counts"] = counts
        evidence["status"] = "POSTGRES_RESTORE_PASS"
        print(json.dumps(counts, indent=2), flush=True)
        print("PostgreSQL restore checkpoint: PASS", flush=True)
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