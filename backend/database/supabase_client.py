"""
Supabase Integration & Cloud Synchronization Service
Syncs only changed/new rows (where synced_at IS NULL OR updated_at > synced_at)
to Supabase on farmer logout while keeping conversational turns and local scratch purely local.
Enforces Row Level Security (RLS) on all tables and respects farmer GPS consent.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from backend.config import settings
from backend.database.db import get_db_connection

logger = logging.getLogger("agritrace.supabase")

SUPABASE_TABLE_SCHEMAS_SQL = """
-- Run this in Supabase SQL Editor if creating tables for the first time:

CREATE TABLE IF NOT EXISTS farmers (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    language TEXT DEFAULT 'en',
    village TEXT,
    district TEXT,
    state TEXT,
    country TEXT DEFAULT 'India',
    latitude REAL,
    longitude REAL,
    consent_gps INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE farmers ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS farms (
    id TEXT PRIMARY KEY,
    farmer_id TEXT REFERENCES farmers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    total_area REAL,
    area_unit TEXT DEFAULT 'acres',
    farm_type TEXT DEFAULT 'mixed',
    irrigation_methods JSONB,
    soil_type TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE farms ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS fields (
    id TEXT PRIMARY KEY,
    farm_id TEXT REFERENCES farms(id) ON DELETE CASCADE,
    farmer_id TEXT REFERENCES farmers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    area REAL,
    area_unit TEXT DEFAULT 'acres',
    soil_type TEXT,
    drainage_condition TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE fields ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS crop_cycles (
    id TEXT PRIMARY KEY,
    field_id TEXT REFERENCES fields(id) ON DELETE CASCADE,
    farm_id TEXT REFERENCES farms(id) ON DELETE CASCADE,
    farmer_id TEXT REFERENCES farmers(id) ON DELETE CASCADE,
    crop_name TEXT NOT NULL,
    variety TEXT,
    sowing_date TEXT,
    growth_stage TEXT,
    expected_harvest_date TEXT,
    historical_problems TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE crop_cycles ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    farmer_id TEXT REFERENCES farmers(id) ON DELETE CASCADE,
    farm_id TEXT,
    field_id TEXT,
    crop_cycle_id TEXT,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    observation_type TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE observations ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    farmer_id TEXT REFERENCES farmers(id) ON DELETE CASCADE,
    observation_id TEXT,
    investigation_id TEXT,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    extracted_text TEXT,
    source_label TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE evidence ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    farmer_id TEXT REFERENCES farmers(id) ON DELETE CASCADE,
    crop_cycle_id TEXT,
    field_id TEXT,
    farm_id TEXT,
    title TEXT NOT NULL,
    user_query TEXT NOT NULL,
    status TEXT DEFAULT 'resolved',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE investigations ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    investigation_id TEXT REFERENCES investigations(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    confidence REAL,
    evidence_strength TEXT DEFAULT 'Moderate',
    status TEXT DEFAULT 'supported',
    supporting_evidence_ids JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE hypotheses ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    investigation_id TEXT REFERENCES investigations(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    detailed_reasoning TEXT NOT NULL,
    recommended_actions JSONB,
    uncertainties JSONB,
    confidence_score REAL,
    evidence_strength TEXT DEFAULT 'Moderate',
    supporting_evidence_ids JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE findings ENABLE ROW LEVEL SECURITY;
"""

class SupabaseService:
    def __init__(self):
        self.url = settings.SUPABASE_URL.rstrip('/') if settings.SUPABASE_URL else ""
        self.key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY

    def is_configured(self) -> bool:
        return bool(self.url and self.key)

    def get_headers(self) -> Dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates"
        }

    def sync_records(self, table: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Upsert records into Supabase table via REST API."""
        if not self.is_configured():
            return {
                "status": "pending_configuration",
                "table": table,
                "count": len(records),
                "message": "Kept on this server, not yet backed up"
            }

        if not records:
            return {"status": "success", "table": table, "count": 0}

        endpoint = f"{self.url}/rest/v1/{table}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    endpoint,
                    headers=self.get_headers(),
                    json=records
                )
                if resp.status_code in (200, 201):
                    return {"status": "success", "table": table, "count": len(records)}
                else:
                    logger.warning(f"Supabase sync failed for {table}: {resp.status_code} - {resp.text}")
                    return {
                        "status": "error",
                        "table": table,
                        "status_code": resp.status_code,
                        "error": resp.text
                    }
        except Exception as e:
            logger.warning(f"Supabase connection exception for {table}: {e}")
            return {"status": "error", "table": table, "error": str(e)}

    def sync_farmer_session_on_logout(self, farmer_id: str) -> Dict[str, Any]:
        """
        Executes on farmer logout:
        Synchronizes ONLY changed or unsynced records (synced_at IS NULL OR updated_at > synced_at).
        Updates synced_at on successful sync.
        Returns per-table breakdown and transparent errors if any fail.
        """
        if not self.is_configured():
            return {
                "farmer_id": farmer_id,
                "supabase_configured": False,
                "message": "Kept on this server, not yet backed up",
                "sync_summary": {}
            }

        conn = get_db_connection()
        now_sync = datetime.now(timezone.utc).isoformat()
        results = {}

        try:
            # 1. Farmer profile (only if consent_gps, send lat/lon)
            farmer_row = conn.execute(
                "SELECT * FROM farmers WHERE id = ? AND (synced_at IS NULL OR updated_at > synced_at)",
                (farmer_id,)
            ).fetchone()
            if farmer_row:
                f_dict = dict(farmer_row)
                f_dict.pop("password_hash", None)
                f_dict.pop("token_version", None)
                f_dict.pop("is_admin", None)
                f_dict.pop("synced_at", None)
                # Respect consent_gps: do not send precise coordinates unless permitted
                if not f_dict.get("consent_gps"):
                    f_dict["latitude"] = None
                    f_dict["longitude"] = None
                res = self.sync_records("farmers", [f_dict])
                results["farmers"] = res
                if res.get("status") == "success":
                    conn.execute("UPDATE farmers SET synced_at = ? WHERE id = ?", (now_sync, farmer_id))

            # Helper for sub-entities
            def sync_table_delta(table_name: str, select_sql: str, id_col: str = "id"):
                rows = conn.execute(select_sql, (farmer_id,)).fetchall()
                if rows:
                    records = []
                    row_ids = []
                    for r in rows:
                        d = dict(r)
                        row_ids.append(d[id_col])
                        d.pop("idempotency_key", None)
                        d.pop("synced_at", None)
                        # Parse JSON fields
                        for json_key in ["irrigation_methods", "data", "recommended_actions", "uncertainties", "supporting_evidence_ids"]:
                            if json_key in d and isinstance(d[json_key], str):
                                try:
                                    d[json_key] = json.loads(d[json_key])
                                except Exception:
                                    pass
                        records.append(d)

                    res = self.sync_records(table_name, records)
                    results[table_name] = res
                    if res.get("status") == "success":
                        placeholders = ",".join(["?"] * len(row_ids))
                        conn.execute(f"UPDATE {table_name} SET synced_at = ? WHERE {id_col} IN ({placeholders})", (now_sync, *row_ids))

            # 2. Farms
            sync_table_delta("farms", "SELECT * FROM farms WHERE farmer_id = ? AND (synced_at IS NULL OR updated_at > synced_at)")

            # 3. Fields
            sync_table_delta("fields", "SELECT * FROM fields WHERE farmer_id = ? AND (synced_at IS NULL OR updated_at > synced_at)")

            # 4. Crop Cycles
            sync_table_delta("crop_cycles", "SELECT * FROM crop_cycles WHERE farmer_id = ? AND (synced_at IS NULL OR updated_at > synced_at)")

            # 5. Observations
            sync_table_delta("observations", "SELECT * FROM observations WHERE farmer_id = ? AND (synced_at IS NULL OR updated_at > synced_at)")

            # 6. Evidence Metadata
            sync_table_delta("evidence", "SELECT * FROM evidence WHERE farmer_id = ? AND (synced_at IS NULL OR updated_at > synced_at)")

            # 7. Investigations
            sync_table_delta("investigations", "SELECT id, farmer_id, crop_cycle_id, field_id, farm_id, title, user_query, status, created_at, updated_at FROM investigations WHERE farmer_id = ? AND (synced_at IS NULL OR updated_at > synced_at)")

            # 8. Hypotheses
            sync_table_delta(
                "hypotheses",
                """
                SELECT h.* FROM hypotheses h
                JOIN investigations i ON h.investigation_id = i.id
                WHERE i.farmer_id = ? AND (h.synced_at IS NULL OR h.updated_at > h.synced_at)
                """
            )

            # 9. Findings
            sync_table_delta(
                "findings",
                """
                SELECT f.* FROM findings f
                JOIN investigations i ON f.investigation_id = i.id
                WHERE i.farmer_id = ? AND (f.synced_at IS NULL OR f.updated_at > f.synced_at)
                """
            )

            conn.commit()

        finally:
            conn.close()

        has_errors = any(v.get("status") == "error" for v in results.values())
        return {
            "farmer_id": farmer_id,
            "supabase_configured": True,
            "sync_status": "partial_error" if has_errors else "success",
            "sync_summary": results
        }

supabase_service = SupabaseService()
