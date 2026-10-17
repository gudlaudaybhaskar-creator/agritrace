"""
AgriTrace Demo Seeding Script
Creates a clearly labelled 'demo_farmer' with sample land parcel and readings.
The UI and responses display a visible DEMO DATA indicator.
"""

import uuid
from datetime import datetime, timezone
from backend.database.db import get_db_connection
from backend.routers.auth import hash_password
from backend.graph.graph_manager import graph_manager

def seed_demo():
    conn = get_db_connection()
    cursor = conn.cursor()

    demo_id = "demo_farmer_001"
    now = datetime.now(timezone.utc).isoformat()

    # Check if exists
    cursor.execute("DELETE FROM farmers WHERE id = ? OR username = 'demo_farmer'", (demo_id,))

    pwd_hash = hash_password("demoPass123")
    cursor.execute(
        """
        INSERT INTO farmers (id, username, password_hash, display_name, language, village, district, state, country, consent_gps, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'en', 'Demo Village', 'Demo District', 'Demo State', 'India', 0, ?, ?)
        """,
        (demo_id, "demo_farmer", pwd_hash, "Demo Farmer (DEMO DATA)", now, now)
    )

    farm_id = "demo_farm_001"
    cursor.execute(
        """
        INSERT INTO farms (id, farmer_id, name, total_area, area_unit, farm_type, soil_type, created_at, updated_at)
        VALUES (?, ?, 'Demo Farm', 3.0, 'acres', 'mixed', 'Loamy', ?, ?)
        """,
        (farm_id, demo_id, now, now)
    )

    field_id = "demo_field_001"
    cursor.execute(
        """
        INSERT INTO fields (id, farm_id, farmer_id, name, area, area_unit, soil_type, drainage_condition, created_at, updated_at)
        VALUES (?, ?, ?, 'South Field', 3.0, 'acres', 'Loamy', 'Standard', ?, ?)
        """,
        (field_id, farm_id, demo_id, now, now)
    )

    cycle_id = "demo_cycle_001"
    cursor.execute(
        """
        INSERT INTO crop_cycles (id, field_id, farm_id, farmer_id, crop_name, variety, sowing_date, growth_stage, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'Wheat', 'Standard Variety', ?, 'Vegetative', 'active', ?, ?)
        """,
        (cycle_id, field_id, farm_id, demo_id, now[:10], now, now)
    )

    # Baseline observation (28%)
    obs_id_1 = "demo_obs_001"
    cursor.execute(
        """
        INSERT INTO observations (id, farmer_id, farm_id, field_id, crop_cycle_id, observed_at, source, observation_type, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, '2026-10-01T10:00:00Z', 'sensor', 'soil_reading', '{"soil_moisture": 28.0, "N": 65}', '2026-10-01T10:00:00Z', '2026-10-01T10:00:00Z')
        """,
        (obs_id_1, demo_id, farm_id, field_id, cycle_id)
    )

    # Second observation (13.5%)
    obs_id_2 = "demo_obs_002"
    cursor.execute(
        """
        INSERT INTO observations (id, farmer_id, farm_id, field_id, crop_cycle_id, observed_at, source, observation_type, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, '2026-10-08T10:00:00Z', 'sensor', 'soil_reading', '{"soil_moisture": 13.5, "N": 42}', '2026-10-08T10:00:00Z', '2026-10-08T10:00:00Z')
        """,
        (obs_id_2, demo_id, farm_id, field_id, cycle_id)
    )

    conn.commit()
    conn.close()

    # Graph
    graph_manager.create_or_update_node("Farmer", {"id": demo_id, "name": "Demo Farmer (DEMO DATA)"})
    graph_manager.create_or_update_node("Farm", {"id": farm_id, "name": "Demo Farm"})
    graph_manager.create_relationship(demo_id, "MANAGES", farm_id)
    graph_manager.create_or_update_node("Field", {"id": field_id, "name": "South Field"})
    graph_manager.create_relationship(farm_id, "CONTAINS", field_id)
    graph_manager.create_or_update_node("CropCycle", {"id": cycle_id, "crop_name": "Wheat"})
    graph_manager.create_relationship(field_id, "HAS_CROP_CYCLE", cycle_id)
    graph_manager.create_or_update_node("Observation", {"id": obs_id_1, "moisture": 28.0})
    graph_manager.create_relationship(cycle_id, "HAS_OBSERVATION", obs_id_1)
    graph_manager.create_or_update_node("Observation", {"id": obs_id_2, "moisture": 13.5})
    graph_manager.create_relationship(cycle_id, "HAS_OBSERVATION", obs_id_2)

    print("=== DEMO DATA SEEDED ===")
    print("Username: demo_farmer")
    print("Password: demoPass123")
    print("Label: Demo Farmer (DEMO DATA)")

if __name__ == "__main__":
    seed_demo()
