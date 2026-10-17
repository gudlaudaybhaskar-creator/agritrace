"""
AgriTrace Main Application Entrypoint
Graph-Native Agricultural Investigation & Persistent Farm-Memory Platform
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError

from backend.config import settings
from backend.database.db import init_db
from backend.datasets.dataset_manager import dataset_manager
from backend.routers import (
    auth,
    farms,
    observations,
    investigations,
    graph_routes,
    datasets_routes,
    sync_routes,
    diagnostics,
    meta_routes
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize persistent database & datasets
    init_db()
    try:
        dataset_manager.initialize_and_profile()
    except Exception as e:
        print(f"Dataset profiling warning: {e}")
    yield
    # Shutdown

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Agricultural Investigation & Persistent Farm-Memory Platform",
    lifespan=lifespan
)

# GZip compression middleware for responsive low-bandwidth performance
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Strict CORS Middleware configured to approved origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Human-readable 422 validation error handler (prevents [object Object] toast popups)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err.get("loc", []) if l != "body")
        msg = err.get("msg", "Invalid value")
        errors.append(f"{loc}: {msg}" if loc else msg)
    clean_message = " | ".join(errors) if errors else "Validation failed"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": clean_message}
    )

# Include API Routers under /api
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(farms.router, prefix=settings.API_V1_STR)
app.include_router(observations.router, prefix=settings.API_V1_STR)
app.include_router(investigations.router, prefix=settings.API_V1_STR)
app.include_router(graph_routes.router, prefix=settings.API_V1_STR)
app.include_router(datasets_routes.router, prefix=settings.API_V1_STR)
app.include_router(sync_routes.router, prefix=settings.API_V1_STR)
app.include_router(diagnostics.router, prefix=settings.API_V1_STR)
app.include_router(meta_routes.router, prefix=settings.API_V1_STR)

@app.get("/api/health")
def api_health():
    return {"status": "ok", "app": settings.PROJECT_NAME, "version": settings.VERSION}


# Serve Frontend Static Assets
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if (FRONTEND_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
if (FRONTEND_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")
if (FRONTEND_DIR / "locales").exists():
    app.mount("/locales", StaticFiles(directory=str(FRONTEND_DIR / "locales")), name="locales")

@app.get("/")
def serve_index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "AgriTrace Backend Running. API docs at /docs"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
