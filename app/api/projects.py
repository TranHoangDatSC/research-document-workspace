import logging
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query

from app.repositories import projects as repository
from app.schemas.projects import ProjectCreate, ProjectRead

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger("uvicorn.error")


def database_unavailable(exc):
    logger.warning("Project database error: %s", type(exc).__name__)
    return HTTPException(
        status_code=503,
        detail="Project storage is temporarily unavailable",
    )


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate):
    try:
        project = repository.create_project(payload)
    except psycopg.Error as exc:
        raise database_unavailable(exc) from None
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
):
    try:
        projects = repository.list_projects(limit, offset)
    except psycopg.Error as exc:
        raise database_unavailable(exc) from None
    return projects


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID):
    try:
        project = repository.get_project(project_id)
    except psycopg.Error as exc:
        raise database_unavailable(exc) from None
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
