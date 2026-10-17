"""
Supabase Cloud Synchronization Router
Provides endpoints to trigger synchronization, inspect sync status,
and retrieve the Supabase SQL setup script.
Requires authentication to prevent server info leakage.
"""

from typing import Any, Dict
from fastapi import APIRouter, Depends
from backend.database.supabase_client import supabase_service, SUPABASE_TABLE_SCHEMAS_SQL
from backend.routers.auth import get_current_farmer

router = APIRouter(prefix="/sync", tags=["Supabase Cloud Synchronization"])

@router.get("/status")
def get_sync_status(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """Returns whether Supabase credentials are configured without exposing backend URLs."""
    configured = supabase_service.is_configured()
    return {
        "configured": configured,
        "mode": "Supabase PostgreSQL Cloud Sync" if configured else "Local server storage only",
        "notice": "When you log out, changes sync to Supabase. If unconfigured, all records stay safely on this local server."
    }

@router.post("/supabase")
def trigger_manual_sync(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """Triggers an explicit sync of current farmer's records to Supabase."""
    result = supabase_service.sync_farmer_session_on_logout(farmer["id"])
    return result

@router.get("/supabase-sql-script")
def get_supabase_sql_script(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """Returns the SQL table schema definition with RLS enabled to execute in Supabase SQL editor."""
    return {
        "sql": SUPABASE_TABLE_SCHEMAS_SQL
    }
