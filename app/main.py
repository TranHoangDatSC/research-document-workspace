from fastapi import FastAPI

app = FastAPI(title="Research Document Workspace")


@app.get("/health/live")
async def health_live():
    return {"status": "alive"}
