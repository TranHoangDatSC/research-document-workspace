import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from app.api.projects import router as projects_router
from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.ui.routes import router as ui_router, error_page

@asynccontextmanager
async def lifespan(app):
    logging.getLogger("uvicorn.error").info("application_started")
    yield

app = FastAPI(title="Research Document Workspace", lifespan=lifespan)
app.include_router(projects_router)
app.include_router(health_router)
app.include_router(documents_router)
app.include_router(ui_router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

def is_ui(request):
    return request.url.path == "/" or request.url.path.startswith("/ui/")

@app.exception_handler(HTTPException)
async def html_http_error(request: Request, exc):
    if is_ui(request):
        return error_page(request, exc.status_code, str(exc.detail))
    return await http_exception_handler(request, exc)

@app.exception_handler(RequestValidationError)
async def html_validation_error(request: Request, exc):
    if is_ui(request):
        return error_page(request, 422, "Dữ liệu không hợp lệ. Kiểm tra ID, các trường và file đã chọn.")
    return await request_validation_exception_handler(request, exc)

@app.middleware("http")
async def same_origin_forms(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith("/ui/"):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            return error_page(request, 403, "Biểu mẫu phải được gửi từ trang ứng dụng này.")
    return await call_next(request)
