"""
AgriTrace Investigation Orchestrator
Conducts agricultural investigations:
1. Resolves farmer, farm, field, and crop cycle context (strictly raises 404 for missing fields, 422 for missing crop cycle).
2. Recalls earlier historical measurements from the persistent database.
3. Only calls ML models when all necessary measurements were actually taken.
4. Evaluates hypotheses via AI Provider (Gemini or Rule-based guidance).
5. Executes the entire pipeline in ONE single database transaction so failures never leave orphan observations.
6. Records real execution traces without fabricated Cypher queries.
7. Updates the knowledge graph with investigation, finding, and hypothesis nodes.
"""

import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status

from backend.database.db import get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.datasets.dataset_manager import dataset_manager
from backend.investigation.ai_provider import ai_provider

logger = logging.getLogger("agritrace.engine")

class InvestigationEngine:
    def execute_investigation(
        self,
        farmer_id: str,
        user_query: str,
        field_id: Optional[str] = None,
        crop_cycle_id: Optional[str] = None,
        new_measurements: Optional[Dict[str, Any]] = None,
        evidence_files: Optional[List[Dict[str, Any]]] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        start_time = time.time()
        trace_steps = []

        def log_trace(step_name: str, description: str, query: str = None, record_ids: List[str] = None, count: int = 0):
            elapsed_ms = int((time.time() - start_time) * 1000)
            trace_steps.append({
                "step": step_name,
                "description": description,
                "executed_query": query,
                "record_ids": record_ids or [],
                "result_count": count,
                "timestamp_ms": elapsed_ms
            })

        log_trace("INPUT_RECEIVED", f"Investigation started: '{user_query[:80]}'")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Step 1: Identify Authenticated Farmer
            farmer_row = cursor.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
            if not farmer_row:
                raise HTTPException(status_code=404, detail="Farmer not found")
            farmer_profile = dict(farmer_row)
            farmer_profile.pop("password_hash", None)
            log_trace("FARMER_IDENTIFIED", f"Loaded profile for farmer {farmer_profile.get('display_name')}", record_ids=[farmer_id], count=1)

            # Step 2: Resolve Farm, Field, and Crop Cycle
            field_row = None
            if field_id:
                field_row = cursor.execute("SELECT * FROM fields WHERE id = ? AND farmer_id = ?", (field_id, farmer_id)).fetchone()
                if not field_row:
                    raise HTTPException(status_code=404, detail=f"Field '{field_id}' not found for this farmer")
            else:
                field_row = cursor.execute("SELECT * FROM fields WHERE farmer_id = ? ORDER BY created_at DESC LIMIT 1", (farmer_id,)).fetchone()
                if not field_row:
                    raise HTTPException(status_code=422, detail="No field registered. Please add a field to your farm before starting an investigation.")

            field_info = dict(field_row)
            farm_row = cursor.execute("SELECT * FROM farms WHERE id = ?", (field_info["farm_id"],)).fetchone()
            farm_info = dict(farm_row) if farm_row else {}

            # Crop cycle resolution: DO NOT invent a fake crop cycle!
            crop_cycle_row = None
            if crop_cycle_id:
                crop_cycle_row = cursor.execute(
                    "SELECT * FROM crop_cycles WHERE id = ? AND field_id = ? AND farmer_id = ?",
                    (crop_cycle_id, field_info["id"], farmer_id)
                ).fetchone()
                if not crop_cycle_row:
                    raise HTTPException(status_code=404, detail=f"Crop cycle '{crop_cycle_id}' not found for this field")
            else:
                crop_cycle_row = cursor.execute(
                    "SELECT * FROM crop_cycles WHERE field_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
                    (field_info["id"],)
                ).fetchone()

            if not crop_cycle_row:
                raise HTTPException(
                    status_code=422,
                    detail=f"No crop cycle found for field '{field_info.get('name')}'. Please register a crop cycle first."
                )

            crop_cycle_info = dict(crop_cycle_row)
            resolved_field_id = field_info["id"]
            resolved_cycle_id = crop_cycle_info["id"]

            log_trace(
                "CONTEXT_RESOLVED",
                f"Resolved context: Farm '{farm_info.get('name')}', Field '{field_info.get('name')}', Crop '{crop_cycle_info.get('crop_name')}'",
                record_ids=[farm_info.get("id"), resolved_field_id, resolved_cycle_id],
                count=3
            )

            # Step 3: Load earlier measurements from database
            hist_obs_rows = cursor.execute(
                """
                SELECT id, observed_at, source, observation_type, data
                FROM observations
                WHERE field_id = ?
                ORDER BY observed_at DESC
                LIMIT 10
                """,
                (resolved_field_id,)
            ).fetchall()

            historical_observations = []
            for r in hist_obs_rows:
                d_val = r["data"]
                if isinstance(d_val, str):
                    try:
                        d_val = json.loads(d_val)
                    except Exception:
                        pass
                historical_observations.append({
                    "id": r["id"],
                    "observed_at": r["observed_at"],
                    "source": r["source"],
                    "type": r["observation_type"],
                    "data": d_val
                })

            log_trace(
                "HISTORICAL_READINGS_LOADED",
                f"Loaded {len(historical_observations)} earlier readings from database for field '{field_info.get('name')}'.",
                record_ids=[o["id"] for o in historical_observations],
                count=len(historical_observations)
            )

            # Step 4: Search benchmark reference statistics
            crop_name = crop_cycle_info.get("crop_name", "")
            soil_type = field_info.get("soil_type", "")
            benchmark_matches = dataset_manager.search_benchmark_records(crop=crop_name, soil_type=soil_type, limit=3)
            log_trace(
                "BENCHMARKS_RETRIEVED",
                f"Matched {benchmark_matches.get('matched_count', 0)} benchmark reference records for crop '{crop_name}'.",
                count=benchmark_matches.get("matched_count", 0)
            )

            # Step 5: Check ML predictions only if ALL 8 features are actually present
            ml_signals = {}
            if new_measurements:
                req_keys = ['N', 'P', 'K', 'temperature', 'humidity', 'ph', 'rainfall', 'soil_moisture']
                has_all_features = all(k in new_measurements and new_measurements[k] is not None for k in req_keys)
                if has_all_features:
                    crop_rec = dataset_manager.predict_crop(
                        float(new_measurements['N']),
                        float(new_measurements['P']),
                        float(new_measurements['K']),
                        float(new_measurements['temperature']),
                        float(new_measurements['humidity']),
                        float(new_measurements['ph']),
                        float(new_measurements['rainfall']),
                        float(new_measurements['soil_moisture'])
                    )
                    ml_signals["predicted_crop"] = crop_rec.get("recommended_crop")
                else:
                    ml_signals["note"] = "Skipped ML prediction: not enough measurements."

            # Step 6: Reasoning & Hypothesis Evaluation
            current_obs_list = []
            obs_id = None
            now_iso = datetime.now(timezone.utc).isoformat()

            if new_measurements:
                obs_id = str(uuid.uuid4())
                current_obs_list.append({
                    "id": obs_id,
                    "observed_at": now_iso,
                    "source": "investigation_input",
                    "data": new_measurements
                })

            graph_context = {
                "field_id": resolved_field_id,
                "crop_cycle_id": resolved_cycle_id,
                "historical_observations_count": len(historical_observations)
            }

            analysis_result = ai_provider.analyze_investigation(
                farmer_profile=farmer_profile,
                field_info=field_info,
                crop_cycle=crop_cycle_info,
                user_problem=user_query,
                current_observations=current_obs_list,
                historical_observations=historical_observations,
                graph_context=graph_context,
                dataset_matches=benchmark_matches,
                ml_predictions=ml_signals
            )

            log_trace(
                "HYPOTHESES_EVALUATED",
                f"Evaluated {len(analysis_result.get('hypotheses', []))} hypotheses using {analysis_result.get('provider_mode')}",
                count=len(analysis_result.get("hypotheses", []))
            )

            # Step 7: Single Database Transaction
            # Everything (observation, investigation, findings, hypotheses) committed together!
            inv_id = str(uuid.uuid4())
            finding_id = str(uuid.uuid4())

            if new_measurements and obs_id:
                cursor.execute(
                    """
                    INSERT INTO observations (id, farmer_id, farm_id, field_id, crop_cycle_id, observed_at, source, observation_type, data, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (obs_id, farmer_id, farm_info.get("id"), resolved_field_id, resolved_cycle_id, now_iso, "investigation_input", "current_measurement", json.dumps(new_measurements), now_iso, now_iso)
                )

            finding_data = analysis_result.get("finding", {})
            summary = analysis_result.get("summary", "Investigation concluded")
            reasoning = analysis_result.get("detailed_reasoning", "")
            rec_actions = json.dumps(finding_data.get("recommended_actions", []))
            uncertainties = json.dumps(finding_data.get("uncertainties", []))
            evidence_str = finding_data.get("evidence_strength", "Moderate")

            cursor.execute(
                """
                INSERT INTO investigations (id, farmer_id, crop_cycle_id, field_id, farm_id, title, user_query, status, execution_trace, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (inv_id, farmer_id, resolved_cycle_id, resolved_field_id, farm_info.get("id"), f"Problem: {user_query[:40]}", user_query, "resolved", json.dumps(trace_steps), idempotency_key, now_iso, now_iso)
            )

            cursor.execute(
                """
                INSERT INTO findings (id, investigation_id, summary, detailed_reasoning, recommended_actions, uncertainties, confidence_score, evidence_strength, supporting_evidence_ids, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (finding_id, inv_id, summary, reasoning, rec_actions, uncertainties, evidence_str, json.dumps([]), now_iso, now_iso)
            )

            hypotheses_records = []
            for h in analysis_result.get("hypotheses", []):
                h_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO hypotheses (id, investigation_id, title, description, confidence, evidence_strength, status, supporting_evidence_ids, created_at, updated_at)
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                    """,
                    (h_id, inv_id, h.get("title", "Hypothesis"), h.get("description", ""), h.get("evidence_strength", "Moderate"), h.get("status", "under_investigation"), json.dumps(h.get("supporting_evidence", [])), now_iso, now_iso)
                )
                hypotheses_records.append({**h, "id": h_id})

            log_trace("INVESTIGATION_COMPLETED", "Investigation and findings recorded safely.", record_ids=[inv_id, finding_id], count=2)
            cursor.execute("UPDATE investigations SET execution_trace = ? WHERE id = ?", (json.dumps(trace_steps), inv_id))

            # Commit the atomic transaction
            conn.commit()

            # Step 8: Update Graph Nodes
            graph_manager.create_or_update_node("Investigation", {
                "id": inv_id,
                "farmer_id": farmer_id,
                "field_id": resolved_field_id,
                "crop_cycle_id": resolved_cycle_id,
                "title": f"Investigation: {crop_name}",
                "status": "resolved",
                "created_at": now_iso
            })
            graph_manager.create_relationship(resolved_cycle_id, "HAS_INVESTIGATION", inv_id)

            graph_manager.create_or_update_node("Finding", {
                "id": finding_id,
                "investigation_id": inv_id,
                "summary": summary,
                "evidence_strength": evidence_str,
                "created_at": now_iso
            })
            graph_manager.create_relationship(inv_id, "PRODUCED", finding_id)

            for h in hypotheses_records:
                graph_manager.create_or_update_node("Hypothesis", {
                    "id": h["id"],
                    "investigation_id": inv_id,
                    "title": h["title"],
                    "status": h["status"],
                    "evidence_strength": h.get("evidence_strength", "Moderate")
                })
                graph_manager.create_relationship(inv_id, "CONSIDERED", h["id"])

            if obs_id:
                graph_manager.create_or_update_node("Observation", {
                    "id": obs_id,
                    "farmer_id": farmer_id,
                    "field_id": resolved_field_id,
                    "crop_cycle_id": resolved_cycle_id,
                    "observed_at": now_iso,
                    "source": "investigation_input",
                    "type": "current_measurement",
                    "data": json.dumps(new_measurements)
                })
                graph_manager.create_relationship(inv_id, "CONTAINS_OBSERVATION", obs_id)

            return {
                "investigation_id": inv_id,
                "finding_id": finding_id,
                "field": field_info,
                "crop_cycle": crop_cycle_info,
                "summary": summary,
                "detailed_reasoning": reasoning,
                "hypotheses": hypotheses_records,
                "finding": finding_data,
                "historical_observations_retrieved": len(historical_observations),
                "historical_observations": historical_observations,
                "dataset_benchmarks": benchmark_matches,
                "ml_signals": ml_signals,
                "execution_trace": trace_steps,
                "provider_mode": analysis_result.get("provider_mode"),
                "status": "resolved",
                "disclaimer": analysis_result.get("disclaimer")
            }

        finally:
            conn.close()

investigation_engine = InvestigationEngine()
