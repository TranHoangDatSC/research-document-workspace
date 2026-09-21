import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.bootstrap import check_postgres, check_mongodb, check_minio
from app.projects import router as projects_router

app = FastAPI(title="Research Document Workspace")
app.include_router(projects_router)

logger = logging.getLogger("uvicorn.error")


@app.get("/health/live")
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    services = {}

    checks = (
        ("postgresql", check_postgres),
        ("mongodb", check_mongodb),
        ("minio", check_minio),
    )

    for name, check in checks:
        try:
            check()
            services[name] = "up"
        except Exception as exc:
            logger.warning(
                "%s readiness failed: %s", name, type(exc).__name__
            )
            services[name] = "down"

    ready = all(value == "up" for value in services.values())

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "services": services,
        },
    )

from app.documents import router as documents_router

app.include_router(documents_router)
