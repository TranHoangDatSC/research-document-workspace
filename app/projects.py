import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.storage import postgres_connection

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger("uvicorn.error")

ProjectName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ProjectName
    description: str | None = Field(default=None, max_length=5000)


class ProjectRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime


def database_unavailable(exc):
    logger.warning("Project database error: %s", type(exc).__name__)
    return HTTPException(
        status_code=503,
        detail="Project storage is temporarily unavailable",
    )


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate):
    try:
        with postgres_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    INSERT INTO projects (id, name, description)
                    VALUES (%s, %s, %s)
                    RETURNING id, name, description, created_at
                    """,
                    (uuid4(), payload.name, payload.description),
                )
                project = cursor.fetchone()
    except psycopg.Error as exc:
        raise database_unavailable(exc) from None

    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        with postgres_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT id, name, description, created_at
                    FROM projects
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                projects = cursor.fetchall()
    except psycopg.Error as exc:
        raise database_unavailable(exc) from None

    return projects


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID):
    try:
        with postgres_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT id, name, description, created_at
                    FROM projects
                    WHERE id = %s
                    """,
                    (project_id,),
                )
                project = cursor.fetchone()
    except psycopg.Error as exc:
        raise database_unavailable(exc) from None

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return project
