"""
Metadata & Validation Limits Router
Exposes server-side range constraints, enum vocabularies, and upload limits
so frontend clients can provide helpful inline hints while keeping server validation
as the single definitive source of truth.
"""

from typing import Any, Dict
from fastapi import APIRouter
from backend.routers.observations import MAX_FILE_SIZE
from backend.datasets.dataset_manager import SOIL_VOCABULARY_MAP

router = APIRouter(prefix="/meta", tags=["Metadata & Client Limits"])

CANONICAL_SOILS = sorted(list(set(SOIL_VOCABULARY_MAP.values())))

VALIDATION_LIMITS = {
    "measurements": {
        "soil_moisture": {"min": 0.0, "max": 100.0, "unit": "%", "label": "Soil Moisture"},
        "soil_ph": {"min": 0.0, "max": 14.0, "unit": "pH", "label": "Soil pH"},
        "nitrogen": {"min": 0.0, "max": 500.0, "unit": "kg/ha", "label": "Available Nitrogen (N)"},
        "phosphorus": {"min": 0.0, "max": 250.0, "unit": "kg/ha", "label": "Available Phosphorus (P)"},
        "potassium": {"min": 0.0, "max": 500.0, "unit": "kg/ha", "label": "Available Potassium (K)"}
    },
    "soil_types": CANONICAL_SOILS,
    "area_units": ["acres", "hectares", "bigha"],
    "irrigation_methods": ["canal", "borewell", "drip", "sprinkler", "rainfed"],
    "uploads": {
        "max_bytes": MAX_FILE_SIZE,
        "max_mb": 8,
        "allowed_formats": ["JPEG", "PNG", "WebP", "PDF"],
        "allowed_mime_types": ["image/jpeg", "image/png", "image/webp", "application/pdf"]
    },
    "password": {
        "min_length": 8
    }
}

@router.get("/limits")
def get_validation_limits():
    """Returns single source of truth for all measurement limits and options."""
    return VALIDATION_LIMITS
