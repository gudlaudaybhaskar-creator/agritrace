"""
AgriTrace Main Application Entrypoint
Graph-Native Agricultural Investigation & Persistent Farm-Memory Platform
"""

import os
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
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

    # Start Keep-Alive Background Robot if enabled
    keep_alive_task = None
    if settings.ENABLE_KEEP_ALIVE and "PYTEST_CURRENT_TEST" not in os.environ:
        from backend.keep_alive import keep_alive_worker
        keep_alive_task = asyncio.create_task(keep_alive_worker())

    yield

    # Shutdown
    if keep_alive_task:
        keep_alive_task.cancel()
        try:
            await keep_alive_task
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Agricultural Investigation & Persistent Farm-Memory Platform",
    lifespan=lifespan
)

# GZip compression middleware for responsive low-bandwidth performance
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Strict CORS Middleware configured to approved origins
if "*" in settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://.*",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
else:
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

# Google Search Console Verification & Crawling Endpoints
@app.get("/googlee3761f5885c801ac.html", include_in_schema=False)
def google_verification():
    vfile = FRONTEND_DIR / "googlee3761f5885c801ac.html"
    if vfile.exists():
        return FileResponse(str(vfile), media_type="text/html")
    return PlainTextResponse("google-site-verification: googlee3761f5885c801ac.html")

@app.get("/google{code}.html", include_in_schema=False)
def google_verification_dynamic(code: str):
    vfile = FRONTEND_DIR / f"google{code}.html"
    if vfile.exists():
        return FileResponse(str(vfile), media_type="text/html")
    return PlainTextResponse(f"google-site-verification: google{code}.html")

@app.get("/robots.txt", include_in_schema=False)
def serve_robots_txt(request: Request):
    rfile = FRONTEND_DIR / "robots.txt"
    if rfile.exists():
        return FileResponse(str(rfile), media_type="text/plain")
    base_url = str(request.base_url).rstrip("/")
    return PlainTextResponse(f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n")

@app.get("/sitemap.xml", include_in_schema=False)
def serve_sitemap_xml(request: Request):
    sfile = FRONTEND_DIR / "sitemap.xml"
    if sfile.exists():
        return FileResponse(str(sfile), media_type="application/xml")
    base_url = str(request.base_url).rstrip("/")
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base_url}/</loc>
    <lastmod>2026-11-21</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return Response(content=xml_content, media_type="application/xml")

# Diagnostics & Monitoring for Keep-Alive Robot
@app.get("/api/keep-alive/status", tags=["Diagnostics"])
def keep_alive_status():
    from backend.keep_alive import keep_alive_stats
    return keep_alive_stats

@app.post("/api/keep-alive/ping", tags=["Diagnostics"])
async def trigger_keep_alive_ping():
    from backend.keep_alive import ping_service, resolve_target_url
    target_url = resolve_target_url()
    success = await ping_service(target_url)
    return {"success": success, "target_url": target_url}


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
