"""
Authentication & Farmer Identity Router
Uses argon2id password hashing, JWT with token versioning and HttpOnly cookie support,
rate-limiting, strict input validation, and optional initial farm creation.
"""

import time
import json
import uuid
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response, status
from pydantic import BaseModel, field_validator
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from backend.config import settings
from backend.database.db import get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.database.supabase_client import supabase_service

router = APIRouter(prefix="/auth", tags=["Authentication & Identity"])
hasher = PasswordHasher()

# In-memory sliding window rate limiter
RATE_LIMIT_WINDOW = 60  # seconds
MAX_AUTH_ATTEMPTS = 15  # requests per window
_ip_attempts: Dict[str, List[float]] = defaultdict(list)

def check_rate_limit(request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    now = time.time()
    # Prune old timestamps
    _ip_attempts[client_ip] = [t for t in _ip_attempts[client_ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_ip_attempts[client_ip]) >= MAX_AUTH_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Please wait a minute and try again."
        )
    _ip_attempts[client_ip].append(now)

def hash_password(password: str) -> str:
    return hasher.hash(password)

def verify_and_rehash(password: str, stored_hash: str) -> tuple[bool, Optional[str]]:
    """
    Verifies password against stored argon2id hash or legacy SHA-256.
    Returns (is_valid, new_hash_if_rehash_needed).
    """
    if stored_hash.startswith("$argon2"):
        try:
            hasher.verify(stored_hash, password)
            if hasher.check_needs_rehash(stored_hash):
                return True, hasher.hash(password)
            return True, None
        except (VerifyMismatchError, InvalidHashError):
            return False, None
    else:
        # Legacy SHA-256 compatibility: verify and automatically rehash to argon2id
        legacy_sha = hashlib.sha256(password.encode()).hexdigest()
        if legacy_sha == stored_hash:
            return True, hasher.hash(password)
        return False, None

def create_access_token(farmer_id: str, username: str, token_version: int = 1) -> str:
    payload = {
        "sub": farmer_id,
        "username": username,
        "token_version": token_version,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def get_current_farmer(
    request: Request,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
    elif "agritrace_token" in request.cookies:
        token = request.cookies.get("agritrace_token")

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authentication token")

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        farmer_id = payload.get("sub")
        token_ver = payload.get("token_version", 1)

        conn = get_db_connection()
        row = conn.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
        conn.close()

        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Farmer not found")

        farmer_dict = dict(row)
        # Check token versioning for instant token invalidation on logout
        if farmer_dict.get("token_version", 1) != token_ver:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked upon logout")

        farmer_dict.pop("password_hash", None)
        return farmer_dict
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired or invalid")

def get_current_admin(farmer: Dict[str, Any] = Depends(get_current_farmer)) -> Dict[str, Any]:
    if not farmer.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator privileges required")
    return farmer

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str
    language: Optional[str] = "en"
    village: Optional[str] = ""
    district: Optional[str] = ""
    state: Optional[str] = ""
    country: Optional[str] = "India"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    consent_gps: Optional[bool] = False

    # All Farm & Land details are purely optional — never invent defaults
    farm_name: Optional[str] = None
    total_area: Optional[float] = None
    area_unit: Optional[str] = "acres"
    farm_type: Optional[str] = "mixed"
    irrigation_methods: Optional[List[str]] = None
    soil_type: Optional[str] = None

    field_name: Optional[str] = None
    crop_name: Optional[str] = None
    variety: Optional[str] = None
    growth_stage: Optional[str] = None

    @field_validator("password")
    def validate_password_strength(cls, v: str) -> str:
        if len(v.strip()) < 8:
            raise ValueError("Password must be at least 8 characters in length")
        return v

    @field_validator("username")
    def validate_username(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Username cannot be empty")
        return clean

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/register")
def register_farmer(
    req: RegisterRequest,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    check_rate_limit(request)
    conn = get_db_connection()
    cursor = conn.cursor()

    # Idempotency check
    if idempotency_key:
        cached = cursor.execute(
            "SELECT response_json FROM idempotency_records WHERE idempotency_key = ?",
            (idempotency_key,)
        ).fetchone()
        if cached:
            conn.close()
            return json.loads(cached["response_json"])

    existing = cursor.execute("SELECT id FROM farmers WHERE username = ?", (req.username.strip(),)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Username already registered. Please sign in.")

    farmer_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    pwd_hash = hash_password(req.password)

    # Insert Farmer
    cursor.execute(
        """
        INSERT INTO farmers (id, username, password_hash, display_name, language, village, district, state, country, latitude, longitude, consent_gps, token_version, is_admin, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
        """,
        (farmer_id, req.username.strip(), pwd_hash, req.display_name.strip(), req.language, req.village or "", req.district or "", req.state or "", req.country or "India", req.latitude, req.longitude, 1 if req.consent_gps else 0, now, now)
    )

    created_farm_id = None
    created_field_id = None
    created_cycle_id = None

    # Only create Farm, Field, and Crop if the user actually provided them
    if req.farm_name and req.farm_name.strip():
        created_farm_id = str(uuid.uuid4())
        area_val = req.total_area if req.total_area is not None else 0.0
        irrigation_json = json.dumps(req.irrigation_methods or [])
        cursor.execute(
            """
            INSERT INTO farms (id, farmer_id, name, total_area, area_unit, farm_type, irrigation_methods, soil_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_farm_id, farmer_id, req.farm_name.strip(), area_val, req.area_unit or "acres", req.farm_type or "mixed", irrigation_json, req.soil_type, now, now)
        )

        if req.field_name and req.field_name.strip():
            created_field_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO fields (id, farm_id, farmer_id, name, area, area_unit, soil_type, drainage_condition, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (created_field_id, created_farm_id, farmer_id, req.field_name.strip(), area_val, req.area_unit or "acres", req.soil_type, "Standard", now, now)
            )

            if req.crop_name and req.crop_name.strip():
                created_cycle_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO crop_cycles (id, field_id, farm_id, farmer_id, crop_name, variety, sowing_date, growth_stage, expected_harvest_date, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (created_cycle_id, created_field_id, created_farm_id, farmer_id, req.crop_name.strip(), req.variety or "", now[:10], req.growth_stage or "Vegetative", None, "active", now, now)
                )

    conn.commit()

    # Synchronize to Graph
    graph_manager.create_or_update_node("Farmer", {
        "id": farmer_id,
        "username": req.username.strip(),
        "display_name": req.display_name.strip(),
        "district": req.district or "",
        "state": req.state or "",
        "created_at": now
    })

    if created_farm_id:
        graph_manager.create_or_update_node("Farm", {
            "id": created_farm_id,
            "farmer_id": farmer_id,
            "name": req.farm_name.strip(),
            "total_area": req.total_area,
            "soil_type": req.soil_type
        })
        graph_manager.create_relationship(farmer_id, "MANAGES", created_farm_id)

        if created_field_id:
            graph_manager.create_or_update_node("Field", {
                "id": created_field_id,
                "farm_id": created_farm_id,
                "farmer_id": farmer_id,
                "name": req.field_name.strip(),
                "soil_type": req.soil_type
            })
            graph_manager.create_relationship(created_farm_id, "CONTAINS", created_field_id)

            if created_cycle_id:
                graph_manager.create_or_update_node("CropCycle", {
                    "id": created_cycle_id,
                    "field_id": created_field_id,
                    "crop_name": req.crop_name.strip(),
                    "variety": req.variety or "",
                    "status": "active"
                })
                graph_manager.create_relationship(created_field_id, "HAS_CROP_CYCLE", created_cycle_id)

    if req.village or req.district or req.state or (req.latitude is not None and req.longitude is not None):
        loc_parts = [p for p in [req.village, req.district, req.state] if p]
        loc_tag = "📍 " + (", ".join(loc_parts) if loc_parts else f"GPS ({req.latitude:.4f}, {req.longitude:.4f})")
        loc_node_id = f"loc_{farmer_id}"
        graph_manager.create_or_update_node("Location", {
            "id": loc_node_id,
            "tag": loc_tag,
            "village": req.village or "",
            "district": req.district or "",
            "state": req.state or "",
            "country": req.country or "India",
            "latitude": req.latitude,
            "longitude": req.longitude,
            "created_at": now
        })
        graph_manager.create_relationship(farmer_id, "LOCATED_IN", loc_node_id)

    token = create_access_token(farmer_id, req.username.strip(), token_version=1)

    result_payload = {
        "status": "success",
        "message": "Account created successfully",
        "token": token,
        "farmer": {
            "id": farmer_id,
            "username": req.username.strip(),
            "display_name": req.display_name.strip(),
            "farm_id": created_farm_id,
            "field_id": created_field_id,
            "crop_cycle_id": created_cycle_id
        }
    }

    if idempotency_key:
        cursor.execute(
            """
            INSERT INTO idempotency_records (id, farmer_id, endpoint, idempotency_key, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), farmer_id, "/api/auth/register", idempotency_key, 200, json.dumps(result_payload), now)
        )
        conn.commit()

    conn.close()

    # Set secure HttpOnly SameSite cookie
    response.set_cookie(
        key="agritrace_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # Can be configured True behind HTTPS
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )

    return result_payload

@router.post("/login")
def login_farmer(req: LoginRequest, request: Request, response: Response):
    check_rate_limit(request)
    conn = get_db_connection()
    cursor = conn.cursor()

    farmer = cursor.execute("SELECT * FROM farmers WHERE username = ?", (req.username.strip(),)).fetchone()

    if not farmer:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    f_dict = dict(farmer)
    is_valid, new_hash = verify_and_rehash(req.password, f_dict["password_hash"])

    if not is_valid:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # If password needed re-hashing (or migrated from SHA-256 to argon2id), update it
    if new_hash:
        cursor.execute("UPDATE farmers SET password_hash = ? WHERE id = ?", (new_hash, f_dict["id"]))
        conn.commit()

    conn.close()

    f_dict.pop("password_hash", None)
    token = create_access_token(f_dict["id"], f_dict["username"], token_version=f_dict.get("token_version", 1))

    response.set_cookie(
        key="agritrace_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )

    return {
        "status": "success",
        "token": token,
        "farmer": f_dict
    }

@router.post("/logout")
def logout_farmer(
    response: Response,
    current_farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    """
    Invalidates existing tokens by incrementing token_version.
    Clears the HttpOnly auth cookie.
    Triggers non-blocking Supabase sync if configured.
    """
    farmer_id = current_farmer["id"]
    conn = get_db_connection()
    conn.execute("UPDATE farmers SET token_version = token_version + 1 WHERE id = ?", (farmer_id,))
    conn.commit()
    conn.close()

    response.delete_cookie("agritrace_token")

    # Sync to Supabase in a non-blocking manner
    sync_result = supabase_service.sync_farmer_session_on_logout(farmer_id)

    return {
        "status": "success",
        "message": "Logged out successfully",
        "supabase_sync": sync_result
    }

@router.get("/me")
def get_me(current_farmer: Dict[str, Any] = Depends(get_current_farmer)):
    return {"farmer": current_farmer}

@router.get("/reverse-geocode")
def reverse_geocode(lat: float, lon: float):
    """
    Reverse geocodes latitude/longitude to Village, District, State via OpenStreetMap Nominatim.
    Free, no API key required.
    """
    import urllib.request
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=14"
        req = urllib.request.Request(url, headers={"User-Agent": "AgriTrace/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addr = data.get("address", {})
            village = addr.get("village") or addr.get("suburb") or addr.get("town") or addr.get("city") or ""
            district = addr.get("county") or addr.get("state_district") or addr.get("district") or addr.get("city") or ""
            state = addr.get("state") or ""
            country = addr.get("country") or "India"
            return {
                "status": "success",
                "village": village,
                "district": district,
                "state": state,
                "country": country,
                "display_name": data.get("display_name", "")
            }
    except Exception as e:
        return {
            "status": "partial",
            "village": "",
            "district": "",
            "state": "",
            "country": "India",
            "error": str(e)
        }

