"""
Farm, Field, and Crop Cycle Management Router
Strictly enforces farmer ownership on all farms, fields, and cycles (preventing IDOR).
Supports include=fields query for single-roundtrip loading.
Enforces idempotency and stores irrigation_methods as valid JSON.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from pydantic import BaseModel, field_validator

from backend.database.db import get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.routers.auth import get_current_farmer

router = APIRouter(prefix="/farms", tags=["Farms & Fields"])

class FarmCreate(BaseModel):
    name: str
    total_area: float
    area_unit: Optional[str] = "acres"
    farm_type: Optional[str] = "mixed"
    irrigation_methods: Optional[List[str]] = None
    soil_type: Optional[str] = None

    @field_validator("name")
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Farm name cannot be empty")
        return clean

    @field_validator("total_area")
    def validate_area(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Total area cannot be negative")
        return v

class FieldCreate(BaseModel):
    name: str
    area: float
    area_unit: Optional[str] = "acres"
    soil_type: Optional[str] = None
    drainage_condition: Optional[str] = "Standard"

    @field_validator("name")
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Field name cannot be empty")
        return clean

    @field_validator("area")
    def validate_area(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Field area cannot be negative")
        return v

class CropCycleCreate(BaseModel):
    crop_name: str
    variety: Optional[str] = ""
    sowing_date: Optional[str] = ""
    growth_stage: Optional[str] = "Vegetative"
    expected_harvest_date: Optional[str] = ""
    historical_problems: Optional[str] = ""

    @field_validator("crop_name")
    def validate_crop_name(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Crop name cannot be empty")
        return clean

@router.get("")
def list_farms(
    include: Optional[str] = Query(None),
    farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    conn = get_db_connection()
    farms_rows = conn.execute(
        "SELECT * FROM farms WHERE farmer_id = ? ORDER BY created_at DESC",
        (farmer["id"],)
    ).fetchall()

    farms = []
    for r in farms_rows:
        f = dict(r)
        if isinstance(f.get("irrigation_methods"), str):
            try:
                f["irrigation_methods"] = json.loads(f["irrigation_methods"])
            except Exception:
                f["irrigation_methods"] = []
        farms.append(f)

    # Efficient single-endpoint loading of fields and active cycles
    if include == "fields" and farms:
        farm_ids = [f["id"] for f in farms]
        placeholders = ",".join(["?"] * len(farm_ids))
        fields_rows = conn.execute(
            f"SELECT * FROM fields WHERE farm_id IN ({placeholders}) AND farmer_id = ? ORDER BY created_at DESC",
            (*farm_ids, farmer["id"])
        ).fetchall()

        fields_by_farm = {fid: [] for fid in farm_ids}
        field_ids = []
        for fld in fields_rows:
            fld_d = dict(fld)
            fld_d["crop_cycles"] = []
            fields_by_farm[fld_d["farm_id"]].append(fld_d)
            field_ids.append(fld_d["id"])

        if field_ids:
            fld_placeholders = ",".join(["?"] * len(field_ids))
            cycles_rows = conn.execute(
                f"SELECT * FROM crop_cycles WHERE field_id IN ({fld_placeholders}) AND farmer_id = ? ORDER BY created_at DESC",
                (*field_ids, farmer["id"])
            ).fetchall()

            cycles_by_field = {fid: [] for fid in field_ids}
            for cyc in cycles_rows:
                cycles_by_field[cyc["field_id"]].append(dict(cyc))

            for fid in farm_ids:
                for fld in fields_by_farm[fid]:
                    fld["crop_cycles"] = cycles_by_field.get(fld["id"], [])

        for farm in farms:
            farm["fields"] = fields_by_farm.get(farm["id"], [])

    conn.close()
    return {"farms": farms}

@router.post("")
def create_farm(
    req: FarmCreate,
    farmer: Dict[str, Any] = Depends(get_current_farmer),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()

    # Idempotency check
    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_records WHERE farmer_id = ? AND idempotency_key = ?",
            (farmer["id"], idempotency_key)
        ).fetchone()
        if cached:
            conn.close()
            return json.loads(cached["response_json"])

    farm_id = str(uuid.uuid4())
    irrigation_json = json.dumps(req.irrigation_methods or [])

    conn.execute(
        """
        INSERT INTO farms (id, farmer_id, name, total_area, area_unit, farm_type, irrigation_methods, soil_type, idempotency_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (farm_id, farmer["id"], req.name, req.total_area, req.area_unit, req.farm_type, irrigation_json, req.soil_type, idempotency_key, now, now)
    )

    response_payload = {"status": "success", "farm_id": farm_id, "message": "Farm created"}

    if idempotency_key:
        conn.execute(
            """
            INSERT INTO idempotency_records (id, farmer_id, endpoint, idempotency_key, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), farmer["id"], "/api/farms", idempotency_key, 200, json.dumps(response_payload), now)
        )

    conn.commit()
    conn.close()

    # Graph
    graph_manager.create_or_update_node("Farm", {
        "id": farm_id,
        "farmer_id": farmer["id"],
        "name": req.name,
        "total_area": req.total_area,
        "soil_type": req.soil_type or ""
    })
    graph_manager.create_relationship(farmer["id"], "MANAGES", farm_id)

    return response_payload

@router.get("/{farm_id}/fields")
def list_fields(farm_id: str, farmer: Dict[str, Any] = Depends(get_current_farmer)):
    conn = get_db_connection()
    # Check farm ownership first
    farm = conn.execute("SELECT id FROM farms WHERE id = ? AND farmer_id = ?", (farm_id, farmer["id"])).fetchone()
    if not farm:
        conn.close()
        raise HTTPException(status_code=404, detail="Farm not found or access denied")

    fields = [dict(r) for r in conn.execute("SELECT * FROM fields WHERE farm_id = ? AND farmer_id = ?", (farm_id, farmer["id"])).fetchall()]
    conn.close()
    return {"fields": fields}

@router.post("/{farm_id}/fields")
def create_field(
    farm_id: str,
    req: FieldCreate,
    farmer: Dict[str, Any] = Depends(get_current_farmer),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    conn = get_db_connection()
    # IDOR check: verify farm belongs to this farmer!
    farm = conn.execute("SELECT id FROM farms WHERE id = ? AND farmer_id = ?", (farm_id, farmer["id"])).fetchone()
    if not farm:
        conn.close()
        raise HTTPException(status_code=404, detail="Farm not found or access denied")

    # Idempotency check
    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_records WHERE farmer_id = ? AND idempotency_key = ?",
            (farmer["id"], idempotency_key)
        ).fetchone()
        if cached:
            conn.close()
            return json.loads(cached["response_json"])

    field_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO fields (id, farm_id, farmer_id, name, area, area_unit, soil_type, drainage_condition, idempotency_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (field_id, farm_id, farmer["id"], req.name, req.area, req.area_unit, req.soil_type, req.drainage_condition, idempotency_key, now, now)
    )

    response_payload = {"status": "success", "field_id": field_id, "message": "Field created"}

    if idempotency_key:
        conn.execute(
            """
            INSERT INTO idempotency_records (id, farmer_id, endpoint, idempotency_key, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), farmer["id"], f"/api/farms/{farm_id}/fields", idempotency_key, 200, json.dumps(response_payload), now)
        )

    conn.commit()
    conn.close()

    # Graph
    graph_manager.create_or_update_node("Field", {
        "id": field_id,
        "farm_id": farm_id,
        "farmer_id": farmer["id"],
        "name": req.name,
        "soil_type": req.soil_type or ""
    })
    graph_manager.create_relationship(farm_id, "CONTAINS", field_id)

    return response_payload

@router.get("/fields/{field_id}/crop-cycles")
def list_crop_cycles(field_id: str, farmer: Dict[str, Any] = Depends(get_current_farmer)):
    conn = get_db_connection()
    # IDOR check: verify field belongs to caller
    field = conn.execute("SELECT id FROM fields WHERE id = ? AND farmer_id = ?", (field_id, farmer["id"])).fetchone()
    if not field:
        conn.close()
        raise HTTPException(status_code=404, detail="Field not found or access denied")

    cycles = [dict(r) for r in conn.execute("SELECT * FROM crop_cycles WHERE field_id = ? AND farmer_id = ? ORDER BY created_at DESC", (field_id, farmer["id"])).fetchall()]
    conn.close()
    return {"crop_cycles": cycles}

@router.post("/fields/{field_id}/crop-cycles")
def add_crop_cycle(
    field_id: str,
    req: CropCycleCreate,
    farmer: Dict[str, Any] = Depends(get_current_farmer),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    conn = get_db_connection()
    # IDOR check: verify field belongs to caller
    field = conn.execute("SELECT id, farm_id FROM fields WHERE id = ? AND farmer_id = ?", (field_id, farmer["id"])).fetchone()
    if not field:
        conn.close()
        raise HTTPException(status_code=404, detail="Field not found or access denied")

    # Idempotency check
    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_records WHERE farmer_id = ? AND idempotency_key = ?",
            (farmer["id"], idempotency_key)
        ).fetchone()
        if cached:
            conn.close()
            return json.loads(cached["response_json"])

    cycle_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    sowing = req.sowing_date or now[:10]

    conn.execute(
        """
        INSERT INTO crop_cycles (id, field_id, farm_id, farmer_id, crop_name, variety, sowing_date, growth_stage, expected_harvest_date, historical_problems, status, idempotency_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (cycle_id, field_id, field["farm_id"], farmer["id"], req.crop_name, req.variety, sowing, req.growth_stage, req.expected_harvest_date, req.historical_problems, "active", idempotency_key, now, now)
    )

    response_payload = {"status": "success", "crop_cycle_id": cycle_id, "message": "Crop cycle added"}

    if idempotency_key:
        conn.execute(
            """
            INSERT INTO idempotency_records (id, farmer_id, endpoint, idempotency_key, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), farmer["id"], f"/api/farms/fields/{field_id}/crop-cycles", idempotency_key, 200, json.dumps(response_payload), now)
        )

    conn.commit()
    conn.close()

    # Graph
    graph_manager.create_or_update_node("CropCycle", {
        "id": cycle_id,
        "field_id": field_id,
        "crop_name": req.crop_name,
        "variety": req.variety or "",
        "growth_stage": req.growth_stage or "",
        "status": "active"
    })
    graph_manager.create_relationship(field_id, "HAS_CROP_CYCLE", cycle_id)

    return response_payload
