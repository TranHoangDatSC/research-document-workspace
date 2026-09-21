"""Day 2 document APIs. All routes belong to the existing FastAPI monolith."""
import hashlib
import io
import json
import logging
import os
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from psycopg.rows import dict_row

from app.bootstrap import bucket_name
from app.storage import postgres_connection, mongo_client, minio_client

router = APIRouter(tags=["documents"])
log = logging.getLogger("uvicorn.error")
MAX_BYTES = 10 * 1024 * 1024
MIME_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
FIELDS = "id, project_id, original_name, object_name, content_type, size_bytes, status, created_at"


def storage_error(stage, exc, document_id=None):
    log.warning("document stage=%s id=%s error=%s", stage, document_id, type(exc).__name__)
    return HTTPException(503, "Document storage unavailable; check server logs")


def sql_one(statement, params):
    with postgres_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(statement, params)
            return cursor.fetchone()


def require_project(project_id):
    try:
        row = sql_one("SELECT id FROM projects WHERE id = %s", (project_id,))
    except psycopg.Error as exc:
        raise storage_error("read-project", exc) from None
    if row is None:
        raise HTTPException(404, "Project not found")


def document_row(document_id):
    try:
        row = sql_one(f"SELECT {FIELDS} FROM documents WHERE id = %s", (document_id,))
    except psycopg.Error as exc:
        raise storage_error("read-document", exc, document_id) from None
    if row is None:
        raise HTTPException(404, "Document not found")
    return row


def require_ready(row):
    if row["status"] != "ready":
        raise HTTPException(409, "Document is not ready")


def split_values(value):
    result = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if len(result) > 50 or any(len(part) > 200 for part in result):
        raise HTTPException(422, "At most 50 items, each at most 200 characters")
    return result


def compensate(document_id, object_name, object_attempted, mongo_attempted):
    # Only used before the final SQL commit is attempted. Do not delete a possibly
    # committed ready document if the result of that commit is unknown.
    if mongo_attempted:
        try:
            with mongo_client() as client:
                client[os.environ["MONGO_DB"]]["document_details"].delete_one(
                    {"document_id": str(document_id)}
                )
        except Exception as exc:
            log.error("cleanup MongoDB incomplete id=%s error=%s", document_id, type(exc).__name__)
    if object_attempted:
        try:
            minio_client().remove_object(bucket_name(), object_name)
        except Exception as exc:
            log.error("cleanup MinIO incomplete id=%s error=%s", document_id, type(exc).__name__)
    try:
        with postgres_connection() as connection:
            connection.execute(
                "UPDATE documents SET status = 'failed' WHERE id = %s AND status = 'pending'",
                (document_id,),
            )
    except Exception as exc:
        log.error("cleanup PostgreSQL incomplete id=%s error=%s", document_id, type(exc).__name__)


@router.post("/projects/{project_id}/documents", status_code=201)
def upload_document(
    project_id: UUID,
    file: Annotated[UploadFile, File()],
    tags: Annotated[str, Form(max_length=5000)] = "",
    authors: Annotated[str, Form(max_length=5000)] = "",
    custom_metadata: Annotated[str, Form(max_length=16000)] = "{}",
):
    require_project(project_id)
    filename = PurePosixPath((file.filename or "").replace("\\", "/")).name
    suffix = PurePosixPath(filename).suffix.lower()
    if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, "Invalid filename")
    if suffix not in MIME_TYPES:
        raise HTTPException(415, "Allowed extensions: .txt, .pdf, .docx")
    try:
        metadata = json.loads(custom_metadata)
    except ValueError:
        raise HTTPException(422, "custom_metadata must be a JSON object") from None
    if not isinstance(metadata, dict):
        raise HTTPException(422, "custom_metadata must be a JSON object")
    tag_list, author_list = split_values(tags), split_values(authors)
    payload = file.file.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise HTTPException(413, "File exceeds 10 MiB")
    if not payload:
        raise HTTPException(422, "Empty file")

    document_id = uuid4()
    object_name = f"documents/{document_id}/original{suffix}"
    details = {
        "document_id": str(document_id),
        "tags": tag_list,
        "authors": author_list,
        "source": {"type": "upload", "url": None},
        "custom_metadata": metadata,
        "extracted_text": None,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    object_attempted = mongo_attempted = finalizing = False
    try:
        # Durable pending record makes interrupted uploads discoverable.
        sql_one(
            "INSERT INTO documents "
            "(id, project_id, original_name, object_name, content_type, size_bytes, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING id",
            (document_id, project_id, filename, object_name, MIME_TYPES[suffix], len(payload)),
        )
        object_attempted = True
        minio_client().put_object(
            bucket_name(), object_name, io.BytesIO(payload), len(payload),
            content_type=MIME_TYPES[suffix],
        )
        mongo_attempted = True
        with mongo_client() as client:
            client[os.environ["MONGO_DB"]]["document_details"].insert_one(details.copy())
        finalizing = True
        row = sql_one(
            f"UPDATE documents SET status = 'ready' WHERE id = %s RETURNING {FIELDS}",
            (document_id,),
        )
    except Exception as exc:
        if finalizing:
            # SQL COMMIT could have succeeded even if its acknowledgement was lost.
            # Retain assets; operator can reconcile using the durable document ID.
            log.error("finalization uncertain; retain assets id=%s", document_id)
        else:
            compensate(document_id, object_name, object_attempted, mongo_attempted)
        raise storage_error("upload", exc, document_id) from None
    return {**row, **details}


@router.get("/projects/{project_id}/documents")
def list_documents(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require_project(project_id)
    try:
        with postgres_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    f"SELECT {FIELDS} FROM documents WHERE project_id = %s "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    (project_id, limit, offset),
                )
                return cursor.fetchall()
    except psycopg.Error as exc:
        raise storage_error("list-documents", exc) from None


@router.get("/documents/{document_id}")
def get_document(document_id: UUID):
    row = document_row(document_id)
    require_ready(row)
    try:
        with mongo_client() as client:
            details = client[os.environ["MONGO_DB"]]["document_details"].find_one(
                {"document_id": str(document_id)}, {"_id": 0}
            )
        if details is None:
            raise RuntimeError("Missing document metadata")
    except Exception as exc:
        raise storage_error("read-metadata", exc, document_id) from None
    return {**row, **details}


@router.get("/documents/{document_id}/download")
def download_document(document_id: UUID):
    row = document_row(document_id)
    require_ready(row)
    try:
        response = minio_client().get_object(bucket_name(), row["object_name"])
        try:
            # Bounded buffering avoids returning HTTP 200 before a failed read.
            payload = response.read(MAX_BYTES + 1)
        finally:
            response.close()
            response.release_conn()
        if len(payload) > MAX_BYTES or len(payload) != row["size_bytes"]:
            raise RuntimeError("Stored file size mismatch")
    except Exception as exc:
        raise storage_error("download", exc, document_id) from None
    return Response(
        content=payload,
        media_type=row["content_type"],
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["original_name"], safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )
