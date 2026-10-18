import json
import sqlite3
import uuid
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from backend.config import settings

logger = logging.getLogger("agritrace.db")

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.executescript('''
    CREATE TABLE IF NOT EXISTS farmers (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        language TEXT DEFAULT 'en',
        village TEXT,
        district TEXT,
        state TEXT,
        country TEXT DEFAULT 'India',
        latitude REAL,
        longitude REAL,
        consent_gps INTEGER DEFAULT 0,
        token_version INTEGER DEFAULT 1,
        is_admin INTEGER DEFAULT 0,
        synced_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS farms (
        id TEXT PRIMARY KEY,
        farmer_id TEXT NOT NULL,
        name TEXT NOT NULL,
        total_area REAL,
        area_unit TEXT DEFAULT 'acres',
        farm_type TEXT DEFAULT 'mixed',
        irrigation_methods TEXT,
        soil_type TEXT,
        idempotency_key TEXT,
        synced_at TEXT,
        updated_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS fields (
        id TEXT PRIMARY KEY,
        farm_id TEXT NOT NULL,
        farmer_id TEXT NOT NULL,
        name TEXT NOT NULL,
        area REAL,
        area_unit TEXT DEFAULT 'acres',
        soil_type TEXT,
        drainage_condition TEXT,
        idempotency_key TEXT,
        synced_at TEXT,
        updated_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(farm_id) REFERENCES farms(id) ON DELETE CASCADE,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS crop_cycles (
        id TEXT PRIMARY KEY,
        field_id TEXT NOT NULL,
        farm_id TEXT NOT NULL,
        farmer_id TEXT NOT NULL,
        crop_name TEXT NOT NULL,
        variety TEXT,
        sowing_date TEXT,
        growth_stage TEXT,
        expected_harvest_date TEXT,
        historical_problems TEXT,
        status TEXT DEFAULT 'active',
        idempotency_key TEXT,
        synced_at TEXT,
        updated_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(field_id) REFERENCES fields(id) ON DELETE CASCADE,
        FOREIGN KEY(farm_id) REFERENCES farms(id) ON DELETE CASCADE,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS observations (
        id TEXT PRIMARY KEY,
        farmer_id TEXT NOT NULL,
        farm_id TEXT,
        field_id TEXT,
        crop_cycle_id TEXT,
        observed_at TEXT NOT NULL,
        source TEXT NOT NULL,
        observation_type TEXT NOT NULL,
        data TEXT NOT NULL,
        idempotency_key TEXT,
        synced_at TEXT,
        updated_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY,
        farmer_id TEXT NOT NULL,
        observation_id TEXT,
        investigation_id TEXT,
        file_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        extracted_text TEXT,
        source_label TEXT,
        idempotency_key TEXT,
        synced_at TEXT,
        updated_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS investigations (
        id TEXT PRIMARY KEY,
        farmer_id TEXT NOT NULL,
        crop_cycle_id TEXT,
        field_id TEXT,
        farm_id TEXT,
        title TEXT NOT NULL,
        user_query TEXT NOT NULL,
        status TEXT DEFAULT 'resolved',
        execution_trace TEXT,
        idempotency_key TEXT,
        synced_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS hypotheses (
        id TEXT PRIMARY KEY,
        investigation_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        confidence REAL,
        evidence_strength TEXT DEFAULT 'Moderate',
        status TEXT DEFAULT 'supported',
        supporting_evidence_ids TEXT,
        synced_at TEXT,
        updated_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS findings (
        id TEXT PRIMARY KEY,
        investigation_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        detailed_reasoning TEXT NOT NULL,
        recommended_actions TEXT,
        uncertainties TEXT,
        confidence_score REAL,
        evidence_strength TEXT DEFAULT 'Moderate',
        supporting_evidence_ids TEXT,
        synced_at TEXT,
        updated_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS idempotency_records (
        id TEXT PRIMARY KEY,
        farmer_id TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        status_code INTEGER DEFAULT 200,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(farmer_id, idempotency_key)
    );

    CREATE TABLE IF NOT EXISTS dataset_records_index (
        id TEXT PRIMARY KEY,
        dataset_name TEXT NOT NULL,
        record_id TEXT NOT NULL,
        crop TEXT,
        soil_type TEXT,
        features TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id TEXT PRIMARY KEY,
        farmer_id TEXT,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        details TEXT,
        timestamp TEXT NOT NULL
    );
    ''')

    # Safe additive column migrations for existing databases
    def add_col_if_missing(table: str, col_def: str, col_name: str):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # Already exists

    add_col_if_missing("farmers", "token_version INTEGER DEFAULT 1", "token_version")
    add_col_if_missing("farmers", "is_admin INTEGER DEFAULT 0", "is_admin")
    add_col_if_missing("farmers", "synced_at TEXT", "synced_at")

    add_col_if_missing("farms", "idempotency_key TEXT", "idempotency_key")
    add_col_if_missing("farms", "synced_at TEXT", "synced_at")
    add_col_if_missing("farms", "updated_at TEXT", "updated_at")

    add_col_if_missing("fields", "idempotency_key TEXT", "idempotency_key")
    add_col_if_missing("fields", "synced_at TEXT", "synced_at")
    add_col_if_missing("fields", "updated_at TEXT", "updated_at")

    add_col_if_missing("crop_cycles", "idempotency_key TEXT", "idempotency_key")
    add_col_if_missing("crop_cycles", "synced_at TEXT", "synced_at")
    add_col_if_missing("crop_cycles", "updated_at TEXT", "updated_at")

    add_col_if_missing("observations", "idempotency_key TEXT", "idempotency_key")
    add_col_if_missing("observations", "synced_at TEXT", "synced_at")
    add_col_if_missing("observations", "updated_at TEXT", "updated_at")

    add_col_if_missing("evidence", "idempotency_key TEXT", "idempotency_key")
    add_col_if_missing("evidence", "synced_at TEXT", "synced_at")
    add_col_if_missing("evidence", "updated_at TEXT", "updated_at")

    add_col_if_missing("investigations", "idempotency_key TEXT", "idempotency_key")
    add_col_if_missing("investigations", "synced_at TEXT", "synced_at")
    add_col_if_missing("investigations", "location_tag TEXT", "location_tag")
    add_col_if_missing("investigations", "weather_tag TEXT", "weather_tag")
    add_col_if_missing("investigations", "weather_data TEXT", "weather_data")

    add_col_if_missing("hypotheses", "evidence_strength TEXT DEFAULT 'Moderate'", "evidence_strength")
    add_col_if_missing("hypotheses", "synced_at TEXT", "synced_at")
    add_col_if_missing("hypotheses", "updated_at TEXT", "updated_at")

    add_col_if_missing("findings", "evidence_strength TEXT DEFAULT 'Moderate'", "evidence_strength")
    add_col_if_missing("findings", "synced_at TEXT", "synced_at")
    add_col_if_missing("findings", "updated_at TEXT", "updated_at")

    conn.commit()
    conn.close()

# Helper dict serializer
def row_to_dict(row: sqlite3.Row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    # Automatically decode known JSON columns if present
    for k in ["irrigation_methods", "data", "execution_trace", "supporting_evidence_ids", "recommended_actions", "uncertainties", "features"]:
        if k in d and d[k] is not None:
            if isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
    return d
