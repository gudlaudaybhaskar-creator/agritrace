import os
import sys
import secrets
import logging
from pathlib import Path
from typing import List
from pydantic import BaseModel
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

logger = logging.getLogger("agritrace.config")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
EXTRACTED_CERTS_DIR = BASE_DIR / "extracted_certificates"
EXTRACTED_CERTS_DIR.mkdir(exist_ok=True)

# Secure secret key resolution
INSECURE_PUBLIC_KEYS = {
    "insecure_default_secret_key_do_not_use",
    "replace_with_your_own_secure_random_key_min_32_chars",
    "secret",
    "changeme"
}

_raw_secret = os.getenv("SECRET_KEY", "").strip()
if _raw_secret in INSECURE_PUBLIC_KEYS:
    raise ValueError(
        "Refusing to start with insecure or placeholder SECRET_KEY. "
        "Generate a secure key using: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
    )
elif not _raw_secret:
    # App starts cleanly with empty .env by generating a secure random session key
    _resolved_secret = secrets.token_urlsafe(48)
    logger.warning(
        "No SECRET_KEY configured in environment. Generated an ephemeral secure random key. "
        "To persist sessions across restarts, set SECRET_KEY via: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
    )
else:
    _resolved_secret = _raw_secret

_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
_cors_list = [o.strip() for o in _cors_env.split(",") if o.strip()]

class Settings(BaseModel):
    PROJECT_NAME: str = "AgriTrace"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"
    SECRET_KEY: str = _resolved_secret
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    CORS_ORIGINS: List[str] = _cors_list
    UPLOADS_DIR: Path = UPLOADS_DIR
    DATA_DIR: Path = DATA_DIR
    EXTRACTED_CERTS_DIR: Path = EXTRACTED_CERTS_DIR

    # FalkorDB settings
    FALKORDB_HOST: str = os.getenv("FALKORDB_HOST", "localhost")
    FALKORDB_PORT: int = int(os.getenv("FALKORDB_PORT", "6379"))
    FALKORDB_PASSWORD: str = os.getenv("FALKORDB_PASSWORD", "")
    FALKORDB_GRAPH_NAME: str = os.getenv("FALKORDB_GRAPH_NAME", "agritrace_graph")

    # Local SQLite persistent DB path (for conversational memory and offline cache)
    SQLITE_DB_PATH: str = str(DATA_DIR / "agritrace.db")

    # Supabase Settings (Cloud persistence)
    # New data accumulated during session will sync to Supabase when the user logs out
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # AI settings (Gemini API from Google AI Studio)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "auto")  # gemini or fallback

    # Dataset file paths
    DATASET_CROP_REC: str = str(BASE_DIR / "Crop_recommendationV2.csv")
    DATASET_DATA_CORE: str = str(BASE_DIR / "data_core.csv")
    DATASET_SENSOR_CROP: str = str(BASE_DIR / "sensor_Crop_Dataset (1).csv")
    DATASET_AGRINET_DIR: str = str(BASE_DIR / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet_dataset")
    DATASET_AGRINET_CSV: str = str(BASE_DIR / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet_dataset" / "AgriNet_Dataset-G7TD2K.csv")
    DATASET_AGRINET_PDF: str = str(BASE_DIR / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet_dataset" / "soilTestDataset-WAIJfM.pdf")
    DATASET_AGRINET_README: str = str(BASE_DIR / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet A Soil Fertility Dataset for Deep Learning" / "AgriNet_dataset" / "README-LNH1mX.md")

settings = Settings()
