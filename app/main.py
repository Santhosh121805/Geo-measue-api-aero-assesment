"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import models
from app.config import settings
from app import database
from app.routes.files import router as files_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create runtime directories and database tables on startup."""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    database.Base.metadata.create_all(bind=database.engine)
    yield


app = FastAPI(
    title="Geo Measure API",
    description="Upload KML and zipped Shapefiles for per-feature measurements.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(files_router)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def homepage() -> FileResponse:
    """Serve the landing page with the upload box."""
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/app", include_in_schema=False)
def workspace() -> FileResponse:
    """Serve the map workspace (opens ?file=<id> directly)."""
    return FileResponse(STATIC_DIR / "app.html")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, error: RequestValidationError
) -> JSONResponse:
    """Keep request validation failures in the API's string-detail error format."""
    message = "; ".join(item["msg"] for item in error.errors())
    return JSONResponse(status_code=422, content={"detail": message})


@app.get("/health")
def health() -> dict[str, str]:
    """Return a simple service health response."""
    return {"status": "ok"}