from fastapi import FastAPI

from app.api.projects import router as projects_router
from app.api.health import router as health_router
from app.api.documents import router as documents_router

app = FastAPI(title="Research Document Workspace")
app.include_router(projects_router)
app.include_router(health_router)
app.include_router(documents_router)
