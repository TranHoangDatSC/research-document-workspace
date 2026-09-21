import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.storage import postgres_connection, mongo_client, minio_client

app = FastAPI(title="Research Document Workspace")
logger = logging.getLogger("uvicorn.error")


@app.get("/health/live")
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    services = {}

    try:
        with postgres_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        services["postgresql"] = "up"
    except Exception as exc:
        logger.warning("PostgreSQL readiness failed: %s", type(exc).__name__)
        services["postgresql"] = "down"

    try:
        with mongo_client() as client:
            client.admin.command("ping")
        services["mongodb"] = "up"
    except Exception as exc:
        logger.warning("MongoDB readiness failed: %s", type(exc).__name__)
        services["mongodb"] = "down"

    try:
        minio_client().list_buckets()
        services["minio"] = "up"
    except Exception as exc:
        logger.warning("MinIO readiness failed: %s", type(exc).__name__)
        services["minio"] = "down"

    ready = all(value == "up" for value in services.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "services": services,
        },
    )

@app.get("/health")
def health_check():
    return {"status": "ok"}
