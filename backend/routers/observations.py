"""
Observations & Evidence Router
Enforces strict input validation on measurements (Pydantic), rejects reserved keys
in graph writes, enforces an 8 MB upload cap with magic bytes verification and EXIF GPS stripping,
and protects evidence files and redacted certificates with authentication and ownership checks.
"""

import os
import io
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from PIL import Image

from backend.config import settings
from backend.database.db import get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.routers.auth import get_current_farmer

router = APIRouter(prefix="/observations", tags=["Observations & Evidence"])

ALLOWED_MAGIC_BYTES = {
    "jpeg": b"\xff\xd8\xff",
    "png": b"\x89PNG\r\n\x1a\n",
    "webp": b"RIFF",  # additionally check for WEBP at byte 8
    "pdf": b"%PDF-"
}
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB

RESERVED_GRAPH_KEYS = {
    "id", "farmer_id", "field_id", "crop_cycle_id", "farm_id",
    "observed_at", "source", "type", "label", "created_at", "updated_at"
}

def detect_and_validate_file_type(content: bytes) -> str:
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of 8 MB (received {len(content) / (1024*1024):.1f} MB)"
        )
    if content.startswith(ALLOWED_MAGIC_BYTES["jpeg"]):
        return "image/jpeg"
    elif content.startswith(ALLOWED_MAGIC_BYTES["png"]):
        return "image/png"
    elif content.startswith(b"RIFF") and len(content) > 12 and content[8:12] == b"WEBP":
        return "image/webp"
    elif content.startswith(ALLOWED_MAGIC_BYTES["pdf"]):
        return "application/pdf"
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file format. Only JPEG, PNG, WebP, and PDF files are accepted."
        )

def strip_exif_gps_and_optimize(content: bytes, mime_type: str) -> tuple[bytes, str]:
    """Strips EXIF data (including GPS coordinates) from uploaded images."""
    if mime_type.startswith("image/"):
        try:
            image = Image.open(io.BytesIO(content))
            # Create a clean copy without EXIF metadata
            clean_io = io.BytesIO()
            if mime_type == "image/jpeg":
                image_clean = Image.new(image.mode, image.size)
                image_clean.putdata(list(image.getdata()))
                image_clean.save(clean_io, format="JPEG", quality=85, optimize=True)
            elif mime_type == "image/png":
                image.save(clean_io, format="PNG", optimize=True)
            elif mime_type == "image/webp":
                image.save(clean_io, format="WEBP", quality=85)
            else:
                return content, mime_type
            return clean_io.getvalue(), mime_type
        except Exception:
            return content, mime_type
    return content, mime_type

class ObservationCreate(BaseModel):
    field_id: str
    crop_cycle_id: Optional[str] = None
    observation_type: str = "soil_moisture"  # soil_moisture, sensor_reading, soil_test, visual_symptom
    source: str = "farmer_reported"
    observed_at: Optional[str] = None
    data: Dict[str, Any]

    @field_validator("data")
    def validate_measurements(cls, d: Dict[str, Any]) -> Dict[str, Any]:
        # Reject reserved keys to prevent cross-user injection
        for reserved in ["id", "farmer_id"]:
            if reserved in d:
                raise ValueError(f"Reserved key '{reserved}' is not permitted in measurement data")

        # Validate soil moisture (0 - 100%)
        for k in ["soil_moisture", "moisture"]:
            if k in d and d[k] is not None:
                try:
                    val = float(d[k])
                    if not (0.0 <= val <= 100.0):
                        raise ValueError(f"Soil moisture must be between 0% and 100% (got {val})")
                    d[k] = val
                except ValueError as err:
                    raise ValueError(str(err))

        # Validate soil pH (0 - 14)
        for k in ["ph", "pH"]:
            if k in d and d[k] is not None:
                try:
                    val = float(d[k])
                    if not (0.0 <= val <= 14.0):
                        raise ValueError(f"Soil pH must be between 0 and 14 (got {val})")
                    d[k] = val
                except ValueError as err:
                    raise ValueError(str(err))

        # Validate N, P, K (>= 0 kg/ha)
        for k in ["nitrogen", "N", "phosphorus", "P", "potassium", "K"]:
            if k in d and d[k] is not None:
                try:
                    val = float(d[k])
                    if val < 0.0:
                        raise ValueError(f"Nutrient {k} cannot be negative (got {val})")
                    d[k] = val
                except ValueError as err:
                    raise ValueError(str(err))

        return d

@router.get("")
def list_observations(
    field_id: Optional[str] = None,
    crop_cycle_id: Optional[str] = None,
    farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    conn = get_db_connection()
    query = "SELECT * FROM observations WHERE farmer_id = ?"
    params = [farmer["id"]]

    if field_id:
        query += " AND field_id = ?"
        params.append(field_id)
    if crop_cycle_id:
        query += " AND crop_cycle_id = ?"
        params.append(crop_cycle_id)

    query += " ORDER BY observed_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("data"), str):
            try:
                d["data"] = json.loads(d["data"])
            except Exception:
                pass
        result.append(d)

    return {"observations": result}

@router.post("")
def add_observation(
    req: ObservationCreate,
    farmer: Dict[str, Any] = Depends(get_current_farmer),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    """
    Records a time-stamped observation.
    Preserves temporal history without overwriting prior measurements.
    """
    conn = get_db_connection()

    # Idempotency check
    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_records WHERE farmer_id = ? AND idempotency_key = ?",
            (farmer["id"], idempotency_key)
        ).fetchone()
        if cached:
            conn.close()
            return json.loads(cached["response_json"])

    # IDOR check: Field must belong to caller
    field = conn.execute("SELECT farm_id FROM fields WHERE id = ? AND farmer_id = ?", (req.field_id, farmer["id"])).fetchone()
    if not field:
        conn.close()
        raise HTTPException(status_code=404, detail="Field not found for this farmer")

    farm_id = field["farm_id"]
    cycle_id = req.crop_cycle_id

    # If crop_cycle_id supplied, check it belongs to field and farmer
    if cycle_id:
        cycle = conn.execute(
            "SELECT id FROM crop_cycles WHERE id = ? AND field_id = ? AND farmer_id = ?",
            (cycle_id, req.field_id, farmer["id"])
        ).fetchone()
        if not cycle:
            conn.close()
            raise HTTPException(status_code=404, detail="Crop cycle not found for this field")
    else:
        # Link to active cycle if present
        cycle_row = conn.execute(
            "SELECT id FROM crop_cycles WHERE field_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (req.field_id,)
        ).fetchone()
        cycle_id = cycle_row["id"] if cycle_row else None

    obs_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    obs_time = req.observed_at or now

    # Normalize observed_at
    try:
        dt = datetime.fromisoformat(obs_time.replace("Z", "+00:00"))
        obs_time = dt.astimezone(timezone.utc).isoformat()
    except Exception:
        obs_time = now

    data_json = json.dumps(req.data)

    conn.execute(
        """
        INSERT INTO observations (id, farmer_id, farm_id, field_id, crop_cycle_id, observed_at, source, observation_type, data, idempotency_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (obs_id, farmer["id"], farm_id, req.field_id, cycle_id, obs_time, req.source, req.observation_type, data_json, idempotency_key, now, now)
    )

    response_payload = {
        "status": "success",
        "observation_id": obs_id,
        "observed_at": obs_time,
        "message": "Observation saved to persistent memory and knowledge graph."
    }

    if idempotency_key:
        conn.execute(
            """
            INSERT INTO idempotency_records (id, farmer_id, endpoint, idempotency_key, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), farmer["id"], "/api/observations", idempotency_key, 200, json.dumps(response_payload), now)
        )

    conn.commit()
    conn.close()

    # Graph write: reject reserved keys to prevent cross-user property injection
    safe_data = {k: v for k, v in req.data.items() if k not in RESERVED_GRAPH_KEYS}
    props = {
        "id": obs_id,
        "farmer_id": farmer["id"],
        "field_id": req.field_id,
        "crop_cycle_id": cycle_id or "",
        "observed_at": obs_time,
        "source": req.source,
        "type": req.observation_type,
        "data": json.dumps(safe_data)
    }
    graph_manager.create_or_update_node("Observation", props)

    if cycle_id:
        graph_manager.create_relationship(cycle_id, "HAS_OBSERVATION", obs_id)
    else:
        graph_manager.create_relationship(req.field_id, "HAS_OBSERVATION", obs_id)

    return response_payload

@router.post("/upload-evidence")
async def upload_evidence(
    file: UploadFile = File(...),
    field_id: Optional[str] = Form(None),
    observation_id: Optional[str] = Form(None),
    investigation_id: Optional[str] = Form(None),
    farmer: Dict[str, Any] = Depends(get_current_farmer),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Magic bytes check & 8MB cap
    mime_type = detect_and_validate_file_type(content)

    # Strip EXIF GPS metadata for user privacy
    clean_bytes, mime_type = strip_exif_gps_and_optimize(content, mime_type)

    conn = get_db_connection()

    # IDOR checks on linked entities if specified
    if field_id:
        f = conn.execute("SELECT id FROM fields WHERE id = ? AND farmer_id = ?", (field_id, farmer["id"])).fetchone()
        if not f:
            conn.close()
            raise HTTPException(status_code=404, detail="Referenced field not found or unauthorized")

    if observation_id:
        o = conn.execute("SELECT id FROM observations WHERE id = ? AND farmer_id = ?", (observation_id, farmer["id"])).fetchone()
        if not o:
            conn.close()
            raise HTTPException(status_code=404, detail="Referenced observation not found or unauthorized")

    if investigation_id:
        i = conn.execute("SELECT id FROM investigations WHERE id = ? AND farmer_id = ?", (investigation_id, farmer["id"])).fetchone()
        if not i:
            conn.close()
            raise HTTPException(status_code=404, detail="Referenced investigation not found or unauthorized")

    # Idempotency check
    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_records WHERE farmer_id = ? AND idempotency_key = ?",
            (farmer["id"], idempotency_key)
        ).fetchone()
        if cached:
            conn.close()
            return json.loads(cached["response_json"])

    ev_id = str(uuid.uuid4())
    ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}
    safe_ext = ext_map.get(mime_type, ".dat")
    safe_filename = f"{ev_id}{safe_ext}"
    dest_path = settings.UPLOADS_DIR / safe_filename

    with open(dest_path, "wb") as f_out:
        f_out.write(clean_bytes)

    file_size = len(clean_bytes)
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO evidence (id, farmer_id, observation_id, investigation_id, file_name, file_path, file_type, file_size, extracted_text, source_label, idempotency_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ev_id, farmer["id"], observation_id, investigation_id, Path(file.filename or "upload").name, str(dest_path), mime_type, file_size, None, "User Upload", idempotency_key, now, now)
    )

    response_payload = {
        "status": "success",
        "evidence_id": ev_id,
        "file_name": Path(file.filename or "upload").name,
        "file_size": file_size,
        "content_type": mime_type,
        "file_url": f"/api/observations/evidence/{ev_id}"
    }

    if idempotency_key:
        conn.execute(
            """
            INSERT INTO idempotency_records (id, farmer_id, endpoint, idempotency_key, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), farmer["id"], "/api/observations/upload-evidence", idempotency_key, 200, json.dumps(response_payload), now)
        )

    conn.commit()
    conn.close()

    # Graph
    graph_manager.create_or_update_node("Evidence", {
        "id": ev_id,
        "farmer_id": farmer["id"],
        "file_name": Path(file.filename or "upload").name,
        "file_type": mime_type,
        "created_at": now
    })
    if observation_id:
        graph_manager.create_relationship(observation_id, "SUPPORTED_BY", ev_id)
    if investigation_id:
        graph_manager.create_relationship(investigation_id, "SUPPORTED_BY", ev_id)

    return response_payload

@router.get("/evidence/{ev_id}")
def get_evidence_file(
    ev_id: str,
    farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    """
    Authenticated evidence file retrieval.
    Enforces that the caller owns the evidence record to prevent IDOR access.
    """
    conn = get_db_connection()
    row = conn.execute("SELECT file_path, file_type, file_name, farmer_id FROM evidence WHERE id = ?", (ev_id,)).fetchone()
    conn.close()

    if not row or row["farmer_id"] != farmer["id"]:
        raise HTTPException(status_code=404, detail="Evidence file not found or access denied")

    fpath = Path(row["file_path"])
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="File missing from storage")

    return FileResponse(str(fpath), media_type=row["file_type"], filename=row["file_name"])

@router.get("/bareilly-certificates")
def list_bareilly_certificates(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """
    Lists redacted research soil health cards from KVK Bareilly.
    Requires authentication to protect dataset privacy and farmer confidentiality.
    """
    certs = []
    if settings.EXTRACTED_CERTS_DIR.exists():
        # Look for thumbnails or available cards
        for i in range(1, 9):
            thumb_name = f"cert_page_{i}_thumb.webp"
            img_name = f"cert_page_{i}.png"
            if (settings.EXTRACTED_CERTS_DIR / img_name).exists():
                certs.append({
                    "id": f"bareilly_cert_{i}",
                    "name": f"Soil Health Record #{i} (Redacted)",
                    "thumbnail_url": f"/api/observations/bareilly-certificates/{thumb_name}" if (settings.EXTRACTED_CERTS_DIR / thumb_name).exists() else f"/api/observations/bareilly-certificates/{img_name}",
                    "full_image_url": f"/api/observations/bareilly-certificates/{img_name}",
                    "provenance": "KVK Bareilly Soil Test Certificate (ICAR-IVRI) - Academic and Research Use Only",
                    "citation": "AgriNet Soil Fertility Dataset, ICAR-IVRI / KVK Bareilly",
                    "license": "Academic and Research Use"
                })
    return {
        "certificates": certs,
        "total": len(certs),
        "notice": "All personal farmer identifying details (name, village, Aadhaar, mobile) are strictly redacted for privacy."
    }

@router.get("/bareilly-certificates/{filename}")
def get_bareilly_certificate_image(
    filename: str,
    farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    """Authenticated endpoint serving redacted certificate pages and WebP thumbnails."""
    safe_name = Path(filename).name  # Prevent directory traversal
    fpath = settings.EXTRACTED_CERTS_DIR / safe_name
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Certificate image not found")

    media_type = "image/webp" if safe_name.endswith(".webp") else "image/png"
    return FileResponse(str(fpath), media_type=media_type)
