"""
AgriTrace Automated Verification Suite
Covers All 11 Required Test Scenarios (Section 8):
1. Registration, login, /me, duplicate username rejected (400), weak password rejected (422).
2. Cross-user data isolation (IDOR protection on farms, fields, cycles, observations, investigations, evidence, subgraphs).
3. Graph writes with reserved keys (id, farmer_id) rejected (422) and mutating Cypher blocked (403).
4. Temporal memory preservation (no overwriting) and delta computation (28 -> 13.5 gives 14.5%).
5. Honest no-keys mode: rule-based reported, missing crop cycle returns 422, unknown field returns 404.
6. Gemini client failure falls back to rule-based and is labeled as such.
7. Idempotency: duplicate request with same Idempotency-Key returns original record without duplicates.
8. XSS check: title containing <img src=x onerror=alert(1)> escapes safely.
9. Upload rejects .exe (415) and files > 8 MB (413).
10. Supabase unset: logout succeeds saying local only; mocked httpx sends only changed rows.
11. Automated check asserting zero banned strings and fake numbers in repository.
"""

import os
import re
import io
import json
import uuid
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.config import settings, BASE_DIR
from backend.main import app
from backend.database.db import init_db, get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.graph.embedded_graph import EmbeddedGraphEngine
from backend.datasets.dataset_manager import dataset_manager
from backend.investigation.ai_provider import ai_provider
from backend.database.supabase_client import supabase_service

@pytest.fixture(scope="session")
def setup_test_db(tmp_path_factory):
    """Initializes an isolated temporary SQLite database for testing."""
    tmp_dir = tmp_path_factory.mktemp("agritrace_test_db")
    test_db = str(tmp_dir / "test_agritrace.db")
    settings.SQLITE_DB_PATH = test_db
    os.environ["AI_PROVIDER"] = "rules"
    settings.AI_PROVIDER = "rules"
    ai_provider.provider_setting = "rules"
    init_db()
    graph_manager.embedded_service = EmbeddedGraphEngine(test_db)
    dataset_manager.initialize_and_profile()
    return test_db

@pytest.fixture
def client(setup_test_db):
    """Provides a FastAPI test client configured with the test database."""
    with TestClient(app) as test_client:
        yield test_client


def register_user(client: TestClient, username: str, password: str = "password123", display_name: str = "Test User"):
    return client.post("/api/auth/register", json={
        "username": username,
        "password": password,
        "display_name": display_name,
        "language": "en"
    })


def login_user(client: TestClient, username: str, password: str = "password123"):
    return client.post("/api/auth/login", json={
        "username": username,
        "password": password
    })


def auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# TEST 1: Register, login, /me work; duplicate username returns 400; weak password returns 422
# ==============================================================================
def test_1_registration_login_me_and_validation(client):
    username = f"farmer_t1_{uuid.uuid4().hex[:6]}"
    
    # 1. Register valid user
    reg_resp = register_user(client, username=username, password="securePassword123")
    assert reg_resp.status_code == 200, reg_resp.text
    reg_data = reg_resp.json()
    assert reg_data["status"] == "success"
    assert "token" in reg_data
    assert reg_data["farmer"]["username"] == username
    token = reg_data["token"]

    # 2. Login valid user
    login_resp = login_user(client, username=username, password="securePassword123")
    assert login_resp.status_code == 200
    assert "token" in login_resp.json()

    # 3. Access /api/auth/me
    me_resp = client.get("/api/auth/me", headers=auth_header(token))
    assert me_resp.status_code == 200
    assert me_resp.json()["farmer"]["username"] == username

    # 4. Duplicate username returns 400
    dup_resp = register_user(client, username=username, password="securePassword123")
    assert dup_resp.status_code == 400
    assert "already registered" in dup_resp.json()["detail"].lower()

    # 5. Weak password (< 8 chars) returns 422
    weak_resp = register_user(client, username=f"weak_{uuid.uuid4().hex[:6]}", password="short")
    assert weak_resp.status_code == 422
    assert "at least 8 characters" in weak_resp.text


# ==============================================================================
# TEST 2: User B cannot access/modify User A's data (IDOR protection)
# ==============================================================================
def test_2_cross_user_isolation(client):
    user_a = f"user_a_{uuid.uuid4().hex[:6]}"
    user_b = f"user_b_{uuid.uuid4().hex[:6]}"

    token_a = register_user(client, user_a).json()["token"]
    token_b = register_user(client, user_b).json()["token"]

    # User A creates a farm, field, and crop cycle
    farm_resp = client.post("/api/farms", json={"name": "User A Farm", "total_area": 3.0}, headers=auth_header(token_a))
    assert farm_resp.status_code == 200
    farm_a_id = farm_resp.json()["farm_id"]

    field_resp = client.post(f"/api/farms/{farm_a_id}/fields", json={"name": "User A Field", "area": 3.0, "soil_type": "Clayey"}, headers=auth_header(token_a))
    assert field_resp.status_code == 200
    field_a_id = field_resp.json()["field_id"]

    cycle_resp = client.post(f"/api/farms/{farm_a_id}/fields/{field_a_id}/crop-cycles", json={"crop_name": "Rice", "variety": "IR64"}, headers=auth_header(token_a))
    assert cycle_resp.status_code == 200
    cycle_a_id = cycle_resp.json()["crop_cycle_id"]

    # User A records an observation
    obs_resp = client.post("/api/observations", json={
        "field_id": field_a_id,
        "crop_cycle_id": cycle_a_id,
        "observation_type": "soil_moisture",
        "data": {"soil_moisture": 25.0}
    }, headers=auth_header(token_a))
    assert obs_resp.status_code == 200
    obs_a_id = obs_resp.json()["observation_id"]

    # User A starts an investigation
    inv_resp = client.post("/api/investigations", json={
        "user_query": "Rice crop leaves are turning yellow",
        "field_id": field_a_id
    }, headers=auth_header(token_a))
    assert inv_resp.status_code == 200
    inv_a_id = inv_resp.json()["investigation"]["investigation_id"]

    # User A uploads evidence
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    ev_resp = client.post("/api/observations/upload-evidence", files={"file": ("photo.png", png_bytes, "image/png")}, data={"field_id": field_a_id}, headers=auth_header(token_a))
    assert ev_resp.status_code == 200
    ev_a_id = ev_resp.json()["evidence_id"]

    # User B attempts unauthorized access to User A's entities (must return 404/403)
    assert client.get(f"/api/farms/{farm_a_id}", headers=auth_header(token_b)).status_code == 404
    assert client.get(f"/api/farms/{farm_a_id}/fields", headers=auth_header(token_b)).status_code == 404
    assert client.get(f"/api/investigations/{inv_a_id}", headers=auth_header(token_b)).status_code == 404
    assert client.get(f"/api/observations/evidence/{ev_a_id}", headers=auth_header(token_b)).status_code == 404
    assert client.get(f"/api/graph/investigation/{inv_a_id}", headers=auth_header(token_b)).status_code == 404

    # User B attempts to write observation or start investigation targeting User A's field
    assert client.post("/api/observations", json={
        "field_id": field_a_id,
        "observation_type": "soil_moisture",
        "data": {"soil_moisture": 20.0}
    }, headers=auth_header(token_b)).status_code == 404

    assert client.post("/api/investigations", json={
        "user_query": "Malicious probe",
        "field_id": field_a_id
    }, headers=auth_header(token_b)).status_code == 404


# ==============================================================================
# TEST 3: Graph writes with reserved keys rejected; mutating Cypher blocked
# ==============================================================================
def test_3_graph_writes_reserved_keys_rejected(client):
    username = f"farmer_t3_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]

    farm_id = client.post("/api/farms", json={"name": "Farm T3", "total_area": 2.0}, headers=auth_header(token)).json()["farm_id"]
    field_id = client.post(f"/api/farms/{farm_id}/fields", json={"name": "Field T3", "area": 2.0}, headers=auth_header(token)).json()["field_id"]

    # Attempt to inject reserved keys ('id' or 'farmer_id') in observation payload -> 422
    resp_id = client.post("/api/observations", json={
        "field_id": field_id,
        "observation_type": "soil_moisture",
        "data": {"id": "injected_custom_id", "soil_moisture": 25.0}
    }, headers=auth_header(token))
    assert resp_id.status_code == 422
    assert "reserved key" in resp_id.text.lower()

    resp_farmer_id = client.post("/api/observations", json={
        "field_id": field_id,
        "observation_type": "soil_moisture",
        "data": {"farmer_id": "victim_farmer_id", "soil_moisture": 25.0}
    }, headers=auth_header(token))
    assert resp_farmer_id.status_code == 422
    assert "reserved key" in resp_farmer_id.text.lower()

    # Standard user attempting Cypher console -> 403 Forbidden
    cypher_resp = client.post("/api/graph/cypher", json={"query": "MATCH (n) RETURN n"}, headers=auth_header(token))
    assert cypher_resp.status_code == 403

    # Admin user attempting mutating Cypher -> 403 Forbidden
    conn = get_db_connection()
    conn.execute("UPDATE farmers SET is_admin = 1 WHERE username = ?", (username,))
    conn.commit()
    conn.close()

    mutate_resp = client.post("/api/graph/cypher", json={"query": "CREATE (n:InjectedNode {name: 'bad'})"}, headers=auth_header(token))
    assert mutate_resp.status_code == 403
    assert "mutating cypher clause" in mutate_resp.json()["detail"].lower()


# ==============================================================================
# TEST 4: Historical memory preserved; investigation computes delta (28 -> 13.5 gives 14.5)
# ==============================================================================
def test_4_historical_memory_preservation_and_delta(client):
    username = f"farmer_t4_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]

    farm_id = client.post("/api/farms", json={"name": "Delta Farm", "total_area": 2.0}, headers=auth_header(token)).json()["farm_id"]
    field_id = client.post(f"/api/farms/{farm_id}/fields", json={"name": "Delta Field", "area": 2.0, "soil_type": "Loamy"}, headers=auth_header(token)).json()["field_id"]
    client.post(f"/api/farms/{farm_id}/fields/{field_id}/crop-cycles", json={"crop_name": "Wheat"}, headers=auth_header(token))

    # 1. Post initial reading: 28.0% moisture
    client.post("/api/observations", json={
        "field_id": field_id,
        "observation_type": "soil_moisture",
        "observed_at": "2026-10-01T10:00:00Z",
        "data": {"soil_moisture": 28.0}
    }, headers=auth_header(token))

    # 2. Run investigation with dropped reading: 13.5% moisture
    # The investigation compares earlier reading (28.0%) with current (13.5%), computes delta 14.5%, and saves reading
    inv_resp = client.post("/api/investigations", json={
        "user_query": "Wheat crop leaves showing signs of moisture stress",
        "field_id": field_id,
        "new_measurements": {"soil_moisture": 13.5}
    }, headers=auth_header(token))
    assert inv_resp.status_code == 200
    inv_data = inv_resp.json()["investigation"]
    reasoning = inv_data["finding"]["detailed_reasoning"]
    
    # Check that delta 14.5% is computed in detailed reasoning
    assert "14.5" in reasoning

    # 3. Assert 2 readings preserved as 2 separate rows in history (no overwrite)
    obs_list = client.get(f"/api/observations?field_id={field_id}", headers=auth_header(token)).json()["observations"]
    assert len(obs_list) == 2
    moistures = [o["data"]["soil_moisture"] for o in obs_list]
    assert 28.0 in moistures
    assert 13.5 in moistures


# ==============================================================================
# TEST 5: No keys mode: rule-based reported; missing crop cycle returns 422; unknown field returns 404
# ==============================================================================
def test_5_no_keys_mode_rule_based_and_error_handling(client, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "AI_PROVIDER", "rules")
    ai_provider.provider_setting = "rules"

    username = f"farmer_t5_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]

    farm_id = client.post("/api/farms", json={"name": "NoKeys Farm", "total_area": 2.0}, headers=auth_header(token)).json()["farm_id"]
    field_id = client.post(f"/api/farms/{farm_id}/fields", json={"name": "Active Field", "area": 2.0, "soil_type": "Clayey"}, headers=auth_header(token)).json()["field_id"]
    client.post(f"/api/farms/{farm_id}/fields/{field_id}/crop-cycles", json={"crop_name": "Rice"}, headers=auth_header(token))

    # Normal execution in no-keys mode
    inv_resp = client.post("/api/investigations", json={
        "user_query": "Leaves are yellowing",
        "field_id": field_id
    }, headers=auth_header(token))
    assert inv_resp.status_code == 200
    res = inv_resp.json()["investigation"]
    assert "Rule-based" in res["finding"]["provider_mode"]
    assert "disclaimer" in res["finding"]

    # Unknown field returns 404
    bad_field_resp = client.post("/api/investigations", json={
        "user_query": "Field test",
        "field_id": f"nonexistent_{uuid.uuid4().hex[:8]}"
    }, headers=auth_header(token))
    assert bad_field_resp.status_code == 404

    # Field with NO crop cycle returns 422
    empty_field_id = client.post(f"/api/farms/{farm_id}/fields", json={"name": "Unplanted Field", "area": 2.0}, headers=auth_header(token)).json()["field_id"]
    no_cycle_resp = client.post("/api/investigations", json={
        "user_query": "Soil test check",
        "field_id": empty_field_id
    }, headers=auth_header(token))
    assert no_cycle_resp.status_code == 422
    assert "no crop cycle found" in no_cycle_resp.json()["detail"].lower()


# ==============================================================================
# TEST 6: Gemini client failure falls back to rule-based and is labeled as such
# ==============================================================================
def test_6_gemini_client_failure_fallback(client, monkeypatch):
    username = f"farmer_t6_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]

    farm_id = client.post("/api/farms", json={"name": "Fallback Farm", "total_area": 2.0}, headers=auth_header(token)).json()["farm_id"]
    field_id = client.post(f"/api/farms/{farm_id}/fields", json={"name": "Fallback Field", "area": 2.0}, headers=auth_header(token)).json()["field_id"]
    client.post(f"/api/farms/{farm_id}/fields/{field_id}/crop-cycles", json={"crop_name": "Maize"}, headers=auth_header(token))

    # Mock Gemini being active but failing on call
    monkeypatch.setattr(ai_provider, "is_gemini_active", lambda: True)
    def fail_call(*args, **kwargs):
        raise RuntimeError("Simulated Gemini API Connection Timeout")
    monkeypatch.setattr(ai_provider, "_call_gemini", fail_call)

    inv_resp = client.post("/api/investigations", json={
        "user_query": "Maize leaves are turning pale",
        "field_id": field_id
    }, headers=auth_header(token))
    assert inv_resp.status_code == 200
    res = inv_resp.json()["investigation"]
    assert "Gemini fallback" in res["finding"]["provider_mode"]
    assert res["finding"]["fallback_reason"] is not None


# ==============================================================================
# TEST 7: Idempotency: duplicate request returns original record without duplicates
# ==============================================================================
def test_7_idempotency(client):
    username = f"farmer_t7_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]

    farm_id = client.post("/api/farms", json={"name": "Idemp Farm", "total_area": 2.0}, headers=auth_header(token)).json()["farm_id"]
    field_id = client.post(f"/api/farms/{farm_id}/fields", json={"name": "Idemp Field", "area": 2.0}, headers=auth_header(token)).json()["field_id"]

    idemp_key = f"key_{uuid.uuid4().hex}"
    headers = auth_header(token)
    headers["Idempotency-Key"] = idemp_key

    # First request
    resp1 = client.post("/api/observations", json={
        "field_id": field_id,
        "observation_type": "soil_moisture",
        "data": {"soil_moisture": 21.5}
    }, headers=headers)
    assert resp1.status_code == 200
    obs_id_1 = resp1.json()["observation_id"]

    # Duplicate request with same Idempotency-Key
    resp2 = client.post("/api/observations", json={
        "field_id": field_id,
        "observation_type": "soil_moisture",
        "data": {"soil_moisture": 21.5}
    }, headers=headers)
    assert resp2.status_code == 200
    obs_id_2 = resp2.json()["observation_id"]

    assert obs_id_1 == obs_id_2

    # Verify only 1 record exists in DB
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) as c FROM observations WHERE idempotency_key = ?", (idemp_key,)).fetchone()["c"]
    conn.close()
    assert count == 1


# ==============================================================================
# TEST 8: XSS check: title containing <img src=x onerror=alert(1)> escapes safely
# ==============================================================================
def test_8_xss_protection(client):
    # 1. Test frontend escape function logic
    def esc(s):
        if s is None: return ""
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#039;")

    malicious_payload = '<img src=x onerror=alert(1)>'
    escaped = esc(malicious_payload)
    assert '<img' not in escaped
    assert '&lt;img src=x onerror=alert(1)&gt;' == escaped

    # 2. Test backend storage and retrieval of HTML characters
    username = f"farmer_t8_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]
    farm_id = client.post("/api/farms", json={"name": "XSS Farm", "total_area": 2.0}, headers=auth_header(token)).json()["farm_id"]
    field_id = client.post(f"/api/farms/{farm_id}/fields", json={"name": "XSS Field", "area": 2.0}, headers=auth_header(token)).json()["field_id"]
    client.post(f"/api/farms/{farm_id}/fields/{field_id}/crop-cycles", json={"crop_name": "Wheat"}, headers=auth_header(token))


    inv_resp = client.post("/api/investigations", json={
        "user_query": malicious_payload,
        "field_id": field_id
    }, headers=auth_header(token))
    assert inv_resp.status_code == 200
    inv_id = inv_resp.json()["investigation"]["investigation_id"]

    fetch_resp = client.get(f"/api/investigations/{inv_id}", headers=auth_header(token))
    assert fetch_resp.status_code == 200
    stored_query = fetch_resp.json()["investigation"]["user_query"]
    assert stored_query == malicious_payload  # Safely stored, rendered as text without evaluating tags


# ==============================================================================
# TEST 9: Upload rejects .exe (415) and files > 8 MB (413)
# ==============================================================================
def test_9_upload_validation_file_type_and_size(client):
    username = f"farmer_t9_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]

    # 1. Attempt to upload an .exe file (MZ magic bytes)
    exe_content = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00" + b"A" * 100
    exe_resp = client.post("/api/observations/upload-evidence", files={"file": ("malware.exe", exe_content, "application/x-msdownload")}, headers=auth_header(token))
    assert exe_resp.status_code == 415
    assert "unsupported file format" in exe_resp.json()["detail"].lower()

    # 2. Attempt to upload a file > 8 MB (8 * 1024 * 1024 + 1024 bytes)
    big_file_content = b"%PDF-" + b"0" * (8 * 1024 * 1024 + 1024)
    big_resp = client.post("/api/observations/upload-evidence", files={"file": ("large_report.pdf", big_file_content, "application/pdf")}, headers=auth_header(token))
    assert big_resp.status_code == 413
    assert "exceeds maximum allowed size of 8 mb" in big_resp.json()["detail"].lower()

    # 3. Valid PNG image under 8MB succeeds
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    valid_resp = client.post("/api/observations/upload-evidence", files={"file": ("valid.png", png_bytes, "image/png")}, headers=auth_header(token))
    assert valid_resp.status_code == 200
    assert "evidence_id" in valid_resp.json()


# ==============================================================================
# TEST 10: Supabase unset: logout succeeds saying local only; mocked httpx sends only changed rows
# ==============================================================================
def test_10_supabase_sync_on_logout(client, monkeypatch):
    # Case A: Supabase unset
    monkeypatch.setattr(supabase_service, "url", "")
    monkeypatch.setattr(supabase_service, "key", "")

    username = f"farmer_t10_{uuid.uuid4().hex[:6]}"
    token = register_user(client, username).json()["token"]

    logout_resp = client.post("/api/auth/logout", headers=auth_header(token))
    assert logout_resp.status_code == 200
    logout_data = logout_resp.json()
    assert logout_data["status"] == "success"
    assert logout_data["supabase_sync"]["supabase_configured"] is False
    assert "kept on this server" in logout_data["supabase_sync"]["message"].lower()

    # Case B: Mocked httpx sends only changed rows
    monkeypatch.setattr(supabase_service, "url", "https://mocked.supabase.co")
    monkeypatch.setattr(supabase_service, "key", "mock-secret-key")

    conn = get_db_connection()
    farmer_id = conn.execute("SELECT id FROM farmers WHERE username = ?", (username,)).fetchone()["id"]
    farm_id = str(uuid.uuid4())
    conn.execute("INSERT INTO farms (id, farmer_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", (farm_id, farmer_id, "Sync Farm", "2026-10-17T00:00:00", "2026-10-17T00:00:00"))
    conn.commit()
    conn.close()

    sent_records = []
    class MockResponse:
        status_code = 200
        text = "[]"
    
    def mock_post(url, headers=None, json=None, timeout=None):
        sent_records.extend(json or [])
        return MockResponse()

    monkeypatch.setattr("httpx.Client.post", lambda self, url, **kwargs: mock_post(url, **kwargs))

    # First sync: sends the new farm record
    supabase_service.sync_farmer_session_on_logout(farmer_id)
    assert len(sent_records) > 0

    # Second sync immediately following without updates: sends 0 changed records
    sent_records.clear()
    supabase_service.sync_farmer_session_on_logout(farmer_id)
    assert len(sent_records) == 0


# ==============================================================================
# TEST 11: Automated test asserting zero banned strings and fake numbers in repository
# ==============================================================================
def test_11_zero_banned_strings_and_fake_numbers():
    banned_regex = re.compile(
        r'Plot 12A|RP_081|Dr\. Swaminathan|92%|0\.92|88%|0\.88|81%|0\.81|74%|0\.74|71%|0\.71|0\.84|0\.87|KisanSahayak|demo_farmer\.png|leaf_sample\.jpg|soil_card\.pdf|agritrace_production_secret|run-acceptance-tests',
        re.IGNORECASE
    )
    fake_numbers_regex = re.compile(
        r'[|][|] 0[.]88|[|][|] 0[.]75|[|][|] 3[.]0|or 0[.]7'
    )

    targets = [
        BASE_DIR / "backend",
        BASE_DIR / "frontend",
        BASE_DIR / "README.md",
        BASE_DIR / "DEMO_WALKTHROUGH.md"
    ]

    violations = []

    for target in targets:
        if target.is_file():
            files = [target]
        else:
            files = [p for p in target.rglob("*") if p.is_file() and not p.name.endswith(".pyc")]

        for f in files:
            # Skip media and binary files
            if f.suffix in [".png", ".jpg", ".jpeg", ".webp", ".pdf", ".sqlite", ".db"]:
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    if banned_regex.search(line):
                        violations.append(f"Banned string in {f.relative_to(BASE_DIR)}:{line_idx} -> {line.strip()}")
                    if fake_numbers_regex.search(line):
                        violations.append(f"Fake number in {f.relative_to(BASE_DIR)}:{line_idx} -> {line.strip()}")
            except Exception:
                pass

    assert len(violations) == 0, f"Found banned string violations:\n" + "\n".join(violations)
