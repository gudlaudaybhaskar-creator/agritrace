"""
Diagnostics & Health Router
Provides minimal, unprivileged health check for uptime monitors and admin status.
Does NOT leak server paths, database credentials, or external API keys.
Fake acceptance test runner has been permanently removed in favor of automated pytest suite.
"""

import time
from typing import Any, Dict
from fastapi import APIRouter

from backend.config import settings
from backend.database.db import get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.datasets.dataset_manager import dataset_manager
from backend.investigation.ai_provider import ai_provider
from backend.database.supabase_client import supabase_service

router = APIRouter(prefix="/diagnostics", tags=["Diagnostics & System Health"])

@router.get("/health")
def system_health():
    """
    Public health check endpoint returning high-level component availability
    without exposing server paths, hostnames, or credentials.
    """
    db_ok = False
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    graph_stat = graph_manager.get_status()
    gemini_active = ai_provider.is_gemini_active()
    supabase_configured = supabase_service.is_configured()

    return {
        "status": "healthy" if db_ok else "degraded",
        "timestamp": time.time(),
        "database_connected": db_ok,
        "graph_backend": "FalkorDB" if graph_stat.get("falkordb", {}).get("connected") else "Built-in Embedded Graph",
        "ai_engine": {
            "mode": f"AI Studio ({settings.GEMINI_MODEL})" if gemini_active else "Rule-based (offline)",
            "live_key_configured": gemini_active
        },
        "cloud_backup": {
            "configured": supabase_configured,
            "status": "Connected" if supabase_configured else "Local only"
        },
        "datasets_ready": len(dataset_manager.datasets_info) > 0,
        "crop_benchmark_model_ready": bool(dataset_manager.crop_model is not None)
    }
