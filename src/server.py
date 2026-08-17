"""FastAPI application assembly."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, __version__, config
from .pdfcompat import pymupdf
from .routers import (
    batch_routes,
    designer_routes,
    documents,
    edit,
    extract_routes,
    ocr_routes,
    stamp,
)

log = logging.getLogger("pdf_workbench")

app = FastAPI(title=APP_NAME, version=__version__)

app.include_router(documents.router)
app.include_router(edit.router)
app.include_router(ocr_routes.router)
app.include_router(extract_routes.router)
app.include_router(stamp.router)
app.include_router(designer_routes.router)
app.include_router(batch_routes.router)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    """Domain errors are the user's problem to fix, not a server fault."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": __version__,
        "pymupdf": getattr(pymupdf, "__doc__", "").split(":")[0].strip(),
        "workspace": str(config.WORKSPACE),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> Any:
    index_path = config.STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse(
        f"<h1>{APP_NAME}</h1><p>The static front end is missing from "
        f"{config.STATIC_DIR}.</p>",
        status_code=500,
    )


if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
