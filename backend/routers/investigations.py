"""
Investigations Router
Handles new investigations, historical memory recall, and full investigation details.
Ensures response shape includes structured field, crop_cycle, finding, and hypotheses
so old investigations load accurately without triggering hardcoded client fallbacks.
"""

import json
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field

from backend.database.db import get_db_connection
from backend.investigation.engine import investigation_engine
from backend.routers.auth import get_current_farmer

router = APIRouter(prefix="/investigations", tags=["Investigations"])

class StartInvestigationRequest(BaseModel):
    user_query: str = Field(..., min_length=3, description="Problem description")
    field_id: Optional[str] = None
    crop_cycle_id: Optional[str] = None
    new_measurements: Optional[Dict[str, Any]] = None
    location_data: Optional[Dict[str, Any]] = None
    weather_data: Optional[Dict[str, Any]] = None

@router.post("")
def start_investigation(
    req: StartInvestigationRequest,
    farmer: Dict[str, Any] = Depends(get_current_farmer),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    """Initiates an evidence-backed agricultural investigation."""
    conn = get_db_connection()

    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_records WHERE farmer_id = ? AND idempotency_key = ?",
            (farmer["id"], idempotency_key)
        ).fetchone()
        if cached:
            conn.close()
            return json.loads(cached["response_json"])
    conn.close()

    result = investigation_engine.execute_investigation(
        farmer_id=farmer["id"],
        user_query=req.user_query.strip(),
        field_id=req.field_id,
        crop_cycle_id=req.crop_cycle_id,
        new_measurements=req.new_measurements,
        idempotency_key=idempotency_key,
        location_data=req.location_data,
        weather_data=req.weather_data
    )

    response_payload = {"status": "success", "investigation": result}

    if idempotency_key:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO idempotency_records (id, farmer_id, endpoint, idempotency_key, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), farmer["id"], "/api/investigations", idempotency_key, 200, json.dumps(response_payload), result.get("execution_trace", [{}])[0].get("timestamp_ms", ""))
        )
        conn.commit()
        conn.close()

    return response_payload

@router.get("")
def list_investigations(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT i.id, i.title, i.user_query, i.status, i.created_at, f.name as field_name, c.crop_name,
               fn.summary as finding_summary, fn.evidence_strength
        FROM investigations i
        LEFT JOIN fields f ON i.field_id = f.id
        LEFT JOIN crop_cycles c ON i.crop_cycle_id = c.id
        LEFT JOIN findings fn ON fn.investigation_id = i.id
        WHERE i.farmer_id = ?
        ORDER BY i.created_at DESC
        """,
        (farmer["id"],)
    ).fetchall()
    conn.close()

    return {"investigations": [dict(r) for r in rows]}

@router.get("/{inv_id}")
def get_investigation_details(inv_id: str, farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """
    Returns complete structured investigation details.
    Fixes the response shape so History items cleanly render actual diagnoses,
    hypotheses, and real field/crop metadata.
    """
    conn = get_db_connection()
    inv_row = conn.execute(
        """
        SELECT i.*, f.id as field_id, f.name as field_name, f.soil_type, c.id as crop_cycle_id, c.crop_name, c.variety
        FROM investigations i
        LEFT JOIN fields f ON i.field_id = f.id
        LEFT JOIN crop_cycles c ON i.crop_cycle_id = c.id
        WHERE i.id = ? AND i.farmer_id = ?
        """,
        (inv_id, farmer["id"])
    ).fetchone()

    if not inv_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Investigation not found or unauthorized")

    inv = dict(inv_row)
    if isinstance(inv.get("execution_trace"), str):
        try:
            inv["execution_trace"] = json.loads(inv["execution_trace"])
        except Exception:
            inv["execution_trace"] = []

    # Get finding
    finding_row = conn.execute("SELECT * FROM findings WHERE investigation_id = ?", (inv_id,)).fetchone()
    finding = dict(finding_row) if finding_row else {}
    for k in ["recommended_actions", "uncertainties", "supporting_evidence_ids"]:
        if isinstance(finding.get(k), str):
            try:
                finding[k] = json.loads(finding[k])
            except Exception:
                finding[k] = []

    # Ensure diagnosis field is populated from summary if absent
    if "diagnosis" not in finding or not finding["diagnosis"]:
        finding["diagnosis"] = finding.get("summary", "Investigation concluded")

    # Get hypotheses
    hyp_rows = conn.execute("SELECT * FROM hypotheses WHERE investigation_id = ?", (inv_id,)).fetchall()
    hypotheses = []
    for h in hyp_rows:
        hd = dict(h)
        if isinstance(hd.get("supporting_evidence_ids"), str):
            try:
                hd["supporting_evidence_ids"] = json.loads(hd["supporting_evidence_ids"])
            except Exception:
                hd["supporting_evidence_ids"] = []
        hypotheses.append(hd)

    # Attach nested field and crop_cycle objects
    inv["field"] = {
        "id": inv.get("field_id"),
        "name": inv.get("field_name") or "Field",
        "soil_type": inv.get("soil_type") or "Loamy"
    }
    inv["crop_cycle"] = {
        "id": inv.get("crop_cycle_id"),
        "crop_name": inv.get("crop_name") or "Crop",
        "variety": inv.get("variety") or ""
    }
    inv["summary"] = finding.get("summary", inv.get("title", ""))
    inv["detailed_reasoning"] = finding.get("detailed_reasoning", "")

    conn.close()

    return {
        "investigation": inv,
        "finding": finding,
        "hypotheses": hypotheses
    }

@router.get("/memory/timeline")
def get_memory_timeline(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """
    Returns a unified chronological memory timeline for the farmer without fabricated numbers.
    """
    conn = get_db_connection()
    timeline = []

    # 1. Registration
    f = conn.execute("SELECT created_at, display_name FROM farmers WHERE id = ?", (farmer["id"],)).fetchone()
    if f:
        timeline.append({
            "timestamp": f["created_at"],
            "type": "REGISTRATION",
            "title": "Account Created",
            "description": f"Farm profile registered for {f['display_name']}.",
            "source": "farmer_profile"
        })

    # 2. Fields
    fields = conn.execute("SELECT id, name, area, area_unit, soil_type, created_at FROM fields WHERE farmer_id = ?", (farmer["id"],)).fetchall()
    for fld in fields:
        timeline.append({
            "timestamp": fld["created_at"],
            "type": "FIELD_CREATED",
            "title": f"Field Added: {fld['name']}",
            "description": f"Area: {fld['area']} {fld['area_unit'] or 'acres'}, Soil: {fld['soil_type'] or 'Standard'}",
            "source": "farmer_profile"
        })

    # 3. Crop Cycles
    cycles = conn.execute("SELECT c.*, f.name as field_name FROM crop_cycles c JOIN fields f ON c.field_id = f.id WHERE c.farmer_id = ?", (farmer["id"],)).fetchall()
    for cyc in cycles:
        timeline.append({
            "timestamp": cyc["created_at"],
            "type": "CROP_CYCLE",
            "title": f"Crop Cycle: {cyc['crop_name']} ({cyc['variety'] or 'Standard'})",
            "description": f"Field: {cyc['field_name']}. Stage: {cyc['growth_stage'] or 'Vegetative'}",
            "source": "farmer_profile"
        })

    # 4. Observations
    obs_rows = conn.execute("SELECT o.*, f.name as field_name FROM observations o LEFT JOIN fields f ON o.field_id = f.id WHERE o.farmer_id = ?", (farmer["id"],)).fetchall()
    for o in obs_rows:
        od = dict(o)
        data_preview = od.get("data")
        timeline.append({
            "timestamp": od["observed_at"],
            "type": "OBSERVATION",
            "title": f"Reading: {od['observation_type'].replace('_', ' ').capitalize()}",
            "description": f"Field '{od.get('field_name')}': {data_preview}",
            "source": od["source"]
        })

    # 5. Investigations (no fake percentages)
    inv_rows = conn.execute("SELECT i.*, fn.summary, fn.evidence_strength FROM investigations i LEFT JOIN findings fn ON fn.investigation_id = i.id WHERE i.farmer_id = ?", (farmer["id"],)).fetchall()
    for inv in inv_rows:
        evidence_badge = inv['evidence_strength'] or 'Evidence evaluated'
        timeline.append({
            "timestamp": inv["created_at"],
            "type": "INVESTIGATION",
            "title": inv["title"],
            "description": f"Finding: {inv['summary'] or 'Investigation completed'} (Evidence: {evidence_badge})",
            "source": "investigation"
        })

    conn.close()

    # Sort chronological descending
    timeline.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"timeline": timeline}
