import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.bootstrap import check_postgres, check_mongodb, check_minio

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.get("/health/live")
async def health_live():
    return {"status": "alive"}


@router.get("/health/ready")
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
            logger.warning("%s readiness failed: %s", name, type(exc).__name__)
            services[name] = "down"

    ready = all(value == "up" for value in services.values())

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "services": services,
        },
    )
