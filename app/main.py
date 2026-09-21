from fastapi import FastAPI

app = FastAPI(title="Research Document Workspace", description="A research assistant for various tasks.", version="1.0.0")

@app.get("/health/live")
async def health_live():
    return {"status": "alive"}