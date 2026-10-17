"""
Investigations Router
Handles start of new investigation, historical memory comparison,
investigation reports, and persistent farm memory timeline.
"""

import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.database.db import get_db_connection
from backend.investigation.engine import investigation_engine
from backend.routers.auth import get_current_farmer

router = APIRouter(prefix="/investigations", tags=["Investigations"])

class StartInvestigationRequest(BaseModel):
    user_query: str
    field_id: Optional[str] = None
    crop_cycle_id: Optional[str] = None
    new_measurements: Optional[Dict[str, Any]] = None

@router.post("")
def start_investigation(req: StartInvestigationRequest, farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """
    Initiates a graph-native agricultural investigation:
    1. Resolves field & crop context
    2. Recalls historical measurements & prior memory
    3. Traverses FalkorDB knowledge graph
    4. Evaluates hypotheses via AI engine
    5. Returns findings with auditable execution trace
    """
    try:
        result = investigation_engine.execute_investigation(
            farmer_id=farmer["id"],
            user_query=req.user_query,
            field_id=req.field_id,
            crop_cycle_id=req.crop_cycle_id,
            new_measurements=req.new_measurements
        )
        return {"status": "success", "investigation": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("")
def list_investigations(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT i.id, i.title, i.user_query, i.status, i.created_at, f.name as field_name, c.crop_name,
               fn.summary as finding_summary, fn.confidence_score
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
    conn = get_db_connection()
    inv_row = conn.execute(
        """
        SELECT i.*, f.name as field_name, f.soil_type, c.crop_name, c.variety
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
        try: inv["execution_trace"] = json.loads(inv["execution_trace"])
        except Exception: pass

    # Get finding
    finding_row = conn.execute("SELECT * FROM findings WHERE investigation_id = ?", (inv_id,)).fetchone()
    finding = dict(finding_row) if finding_row else None
    if finding:
        for k in ["recommended_actions", "uncertainties", "supporting_evidence_ids"]:
            if isinstance(finding.get(k), str):
                try: finding[k] = json.loads(finding[k])
                except Exception: pass

    # Get hypotheses
    hyp_rows = conn.execute("SELECT * FROM hypotheses WHERE investigation_id = ?", (inv_id,)).fetchall()
    hypotheses = []
    for h in hyp_rows:
        hd = dict(h)
        if isinstance(hd.get("supporting_evidence_ids"), str):
            try: hd["supporting_evidence_ids"] = json.loads(hd["supporting_evidence_ids"])
            except Exception: pass
        hypotheses.append(hd)

    conn.close()

    return {
        "investigation": inv,
        "finding": finding,
        "hypotheses": hypotheses
    }

@router.get("/memory/timeline")
def get_memory_timeline(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """
    Returns a unified chronological memory timeline for the farmer showing:
    - Registered profile events
    - Field & crop cycle additions
    - Observations and sensor readings over time
    - Investigations conducted and findings reached
    """
    conn = get_db_connection()
    timeline = []

    # 1. Registration
    f = conn.execute("SELECT created_at, display_name FROM farmers WHERE id = ?", (farmer["id"],)).fetchone()
    if f:
        timeline.append({
            "timestamp": f["created_at"],
            "type": "REGISTRATION",
            "title": "Farmer Identity Established",
            "description": f"Root identity registered for {f['display_name']} with persistent graph anchoring.",
            "source": "registered_profile"
        })

    # 2. Fields
    fields = conn.execute("SELECT id, name, area, soil_type, created_at FROM fields WHERE farmer_id = ?", (farmer["id"],)).fetchall()
    for fld in fields:
        timeline.append({
            "timestamp": fld["created_at"],
            "type": "FIELD_CREATED",
            "title": f"Field Registered: {fld['name']}",
            "description": f"Parcel area: {fld['area']} acres, Soil: {fld['soil_type']}",
            "source": "registered_profile"
        })

    # 3. Crop Cycles
    cycles = conn.execute("SELECT c.*, f.name as field_name FROM crop_cycles c JOIN fields f ON c.field_id = f.id WHERE c.farmer_id = ?", (farmer["id"],)).fetchall()
    for cyc in cycles:
        timeline.append({
            "timestamp": cyc["created_at"],
            "type": "CROP_CYCLE",
            "title": f"Crop Cycle Sown: {cyc['crop_name']} ({cyc['variety'] or 'Standard'})",
            "description": f"Planted on field '{cyc['field_name']}'. Stage: {cyc['growth_stage']}",
            "source": "registered_profile"
        })

    # 4. Observations
    obs_rows = conn.execute("SELECT o.*, f.name as field_name FROM observations o LEFT JOIN fields f ON o.field_id = f.id WHERE o.farmer_id = ?", (farmer["id"],)).fetchall()
    for o in obs_rows:
        od = dict(o)
        data_preview = od.get("data")
        timeline.append({
            "timestamp": od["observed_at"],
            "type": "OBSERVATION",
            "title": f"Measurement Logged: {od['observation_type'].replace('_', ' ').capitalize()}",
            "description": f"Field '{od.get('field_name')}': {data_preview}",
            "source": od["source"]
        })

    # 5. Investigations
    inv_rows = conn.execute("SELECT i.*, fn.summary, fn.confidence_score FROM investigations i LEFT JOIN findings fn ON fn.investigation_id = i.id WHERE i.farmer_id = ?", (farmer["id"],)).fetchall()
    for inv in inv_rows:
        timeline.append({
            "timestamp": inv["created_at"],
            "type": "INVESTIGATION",
            "title": inv["title"],
            "description": f"Finding: {inv['summary'] or 'Investigation completed'} (Confidence: {int((inv['confidence_score'] or 0.7)*100)}%)",
            "source": "ai_investigation"
        })

    conn.close()

    # Sort chronological
    timeline.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"timeline": timeline}
