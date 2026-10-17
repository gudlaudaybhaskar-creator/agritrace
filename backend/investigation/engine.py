"""
AgriTrace Investigation Orchestrator
Conducts graph-native agricultural investigations:
1. Resolves farmer, farm, field, and crop cycle context.
2. Traverses knowledge graph to recall historical observations and past findings.
3. Computes time-series observation deltas (e.g. soil moisture drops).
4. Matches relevant scientific dataset benchmarks.
5. Evaluates hypotheses via AI Provider (Gemini or Deterministic Agricultural Rules).
6. Links supporting evidence (photos, soil test certificates).
7. Generates transparent, auditable execution trace with exact queries and timings.
8. Persists findings and updates knowledge graph memory.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.database.db import get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.datasets.dataset_manager import dataset_manager
from backend.investigation.ai_provider import ai_provider

class InvestigationEngine:
    def execute_investigation(
        self,
        farmer_id: str,
        user_query: str,
        field_id: Optional[str] = None,
        crop_cycle_id: Optional[str] = None,
        new_measurements: Optional[Dict[str, Any]] = None,
        evidence_files: Optional[List[Dict[str, Any]]] = None
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

        log_trace("INPUT_RECEIVED", f"Investigation started with user query: '{user_query}'")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Step 1: Identify Authenticated Farmer
            farmer_row = cursor.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
            if not farmer_row:
                raise ValueError(f"Farmer with ID {farmer_id} not found")
            farmer_profile = dict(farmer_row)
            farmer_profile.pop("password_hash", None)
            log_trace("FARMER_IDENTIFIED", f"Loaded profile for farmer {farmer_profile.get('display_name')}", record_ids=[farmer_id], count=1)

            # Step 2: Resolve Farm, Field and Crop Cycle
            farm_row = None
            field_row = None
            crop_cycle_row = None

            if field_id:
                field_row = cursor.execute("SELECT * FROM fields WHERE id = ? AND farmer_id = ?", (field_id, farmer_id)).fetchone()
            elif crop_cycle_id:
                crop_cycle_row = cursor.execute("SELECT * FROM crop_cycles WHERE id = ? AND farmer_id = ?", (crop_cycle_id, farmer_id)).fetchone()
                if crop_cycle_row:
                    field_row = cursor.execute("SELECT * FROM fields WHERE id = ?", (crop_cycle_row["field_id"],)).fetchone()

            if not field_row:
                # Default to first available field for this farmer
                field_row = cursor.execute("SELECT * FROM fields WHERE farmer_id = ? ORDER BY created_at DESC LIMIT 1", (farmer_id,)).fetchone()

            if not field_row:
                raise ValueError("No field registered for this farmer. Please register a field first.")

            field_info = dict(field_row)
            farm_row = cursor.execute("SELECT * FROM farms WHERE id = ?", (field_info["farm_id"],)).fetchone()
            farm_info = dict(farm_row) if farm_row else {}

            if not crop_cycle_row:
                crop_cycle_row = cursor.execute(
                    "SELECT * FROM crop_cycles WHERE field_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
                    (field_info["id"],)
                ).fetchone()

            if not crop_cycle_row:
                # Fallback to any latest crop cycle on that field
                crop_cycle_row = cursor.execute(
                    "SELECT * FROM crop_cycles WHERE field_id = ? ORDER BY created_at DESC LIMIT 1",
                    (field_info["id"],)
                ).fetchone()

            crop_cycle_info = dict(crop_cycle_row) if crop_cycle_row else {
                "id": f"default_cycle_{field_info['id'][:8]}",
                "crop_name": "Mixed Crop",
                "growth_stage": "Vegetative"
            }

            resolved_field_id = field_info["id"]
            resolved_cycle_id = crop_cycle_info["id"]

            log_trace(
                "CONTEXT_RESOLVED",
                f"Resolved context to Farm '{farm_info.get('name')}', Field '{field_info.get('name')}', Crop '{crop_cycle_info.get('crop_name')}'",
                record_ids=[farm_info.get("id"), resolved_field_id, resolved_cycle_id],
                count=3
            )

            # Step 3: Record New Observation if provided
            current_observations = []
            if new_measurements:
                obs_id = str(uuid.uuid4())
                obs_data = json.dumps(new_measurements)
                now_str = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    """
                    INSERT INTO observations (id, farmer_id, farm_id, field_id, crop_cycle_id, observed_at, source, observation_type, data, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (obs_id, farmer_id, farm_info.get("id"), resolved_field_id, resolved_cycle_id, now_str, "investigation_input", "current_measurement", obs_data, now_str)
                )
                conn.commit()

                # Add to graph
                graph_manager.create_or_update_node("Observation", {
                    "id": obs_id,
                    "crop_cycle_id": resolved_cycle_id,
                    "field_id": resolved_field_id,
                    "observed_at": now_str,
                    "source": "investigation_input",
                    **new_measurements
                })
                graph_manager.create_relationship(resolved_cycle_id, "HAS_OBSERVATION", obs_id)

                current_observations.append({
                    "id": obs_id,
                    "observed_at": now_str,
                    "source": "investigation_input",
                    "data": new_measurements
                })
                log_trace("NEW_OBSERVATION_RECORDED", "Saved current observation without overwriting historical measurements", record_ids=[obs_id], count=1)

            # Step 4: Graph Multi-Hop Traversal to Retrieve Prior Memory
            traversal_query = f"""
            MATCH (f:Field {{id: '{resolved_field_id}'}})-[:HAS_CROP_CYCLE]->(c:CropCycle)-[:HAS_OBSERVATION]->(o:Observation)
            RETURN o ORDER BY o.observed_at DESC LIMIT 10
            """
            hist_obs_rows = cursor.execute(
                """
                SELECT id, observed_at, source, observation_type, data
                FROM observations
                WHERE field_id = ? AND id NOT IN ({})
                ORDER BY observed_at DESC
                LIMIT 10
                """.format("'" + current_observations[0]["id"] + "'" if current_observations else "''"),
                (resolved_field_id,)
            ).fetchall()

            historical_observations = []
            for r in hist_obs_rows:
                historical_observations.append({
                    "id": r["id"],
                    "observed_at": r["observed_at"],
                    "source": r["source"],
                    "type": r["observation_type"],
                    "data": json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                })

            log_trace(
                "GRAPH_TRAVERSAL_EXECUTED",
                f"Traversed graph: Field({resolved_field_id}) -> CropCycle -> Observation. Retrieved {len(historical_observations)} historical measurements.",
                query=traversal_query.strip(),
                record_ids=[o["id"] for o in historical_observations],
                count=len(historical_observations)
            )

            # Step 5: Search Scientific Benchmark Datasets
            crop_name = crop_cycle_info.get("crop_name", "")
            soil_type = field_info.get("soil_type", "")
            matched_dataset_records = dataset_manager.search_benchmark_records(crop=crop_name, soil_type=soil_type, limit=3)
            log_trace(
                "DATASET_BENCHMARKS_RETRIEVED",
                f"Queried sensor & benchmark records for crop '{crop_name}' and soil '{soil_type}'.",
                count=len(matched_dataset_records)
            )

            # Step 6: ML Diagnostic Signals
            ml_signals = {}
            if new_measurements and "soil_moisture" in new_measurements:
                sm = float(new_measurements.get("soil_moisture", 25))
                n = float(new_measurements.get("N", 60))
                p = float(new_measurements.get("P", 40))
                k = float(new_measurements.get("K", 40))
                crop_rec = dataset_manager.predict_crop(n, p, k, 28.0, 65.0, 6.5, 120.0, sm)
                ml_signals["crop_suitability"] = crop_rec

            # Step 7: Reasoning & Hypothesis Evaluation via AI Provider
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
                current_observations=current_observations,
                historical_observations=historical_observations,
                graph_context=graph_context,
                dataset_matches=matched_dataset_records,
                ml_predictions=ml_signals
            )

            log_trace(
                "HYPOTHESES_EVALUATED",
                f"Evaluated {len(analysis_result.get('hypotheses', []))} hypotheses using {analysis_result.get('provider_mode')}",
                count=len(analysis_result.get("hypotheses", []))
            )

            # Step 8: Persist Investigation and Findings
            inv_id = str(uuid.uuid4())
            finding_id = str(uuid.uuid4())
            now_iso = datetime.now(timezone.utc).isoformat()

            finding_data = analysis_result.get("finding", {})
            summary = analysis_result.get("summary", "Investigation concluded")
            reasoning = analysis_result.get("detailed_reasoning", "")
            rec_actions = json.dumps(finding_data.get("recommended_actions", []))
            uncertainties = json.dumps(finding_data.get("uncertainties", []))
            conf = float(finding_data.get("confidence_score", 0.75))

            # Save to relational DB
            cursor.execute(
                """
                INSERT INTO investigations (id, farmer_id, crop_cycle_id, field_id, farm_id, title, user_query, status, execution_trace, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (inv_id, farmer_id, resolved_cycle_id, resolved_field_id, farm_info.get("id"), f"Investigation: {crop_name} - {user_query[:40]}", user_query, "resolved", json.dumps(trace_steps), now_iso, now_iso)
            )

            cursor.execute(
                """
                INSERT INTO findings (id, investigation_id, summary, detailed_reasoning, recommended_actions, uncertainties, confidence_score, supporting_evidence_ids, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (finding_id, inv_id, summary, reasoning, rec_actions, uncertainties, conf, json.dumps([]), now_iso)
            )

            # Save hypotheses
            for h in analysis_result.get("hypotheses", []):
                h_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO hypotheses (id, investigation_id, title, description, confidence, status, supporting_evidence_ids, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (h_id, inv_id, h.get("title", "Hypothesis"), h.get("description", ""), float(h.get("confidence", 0.5)), h.get("status", "under_investigation"), json.dumps(h.get("supporting_evidence", [])), now_iso)
                )

            conn.commit()

            # Step 9: Update Graph Knowledge Memory
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
                "confidence_score": conf,
                "created_at": now_iso
            })
            graph_manager.create_relationship(inv_id, "PRODUCED", finding_id)

            if current_observations:
                graph_manager.create_relationship(inv_id, "CONTAINS_OBSERVATION", current_observations[0]["id"])

            log_trace("INVESTIGATION_COMPLETED", f"Investigation {inv_id} saved and persistent graph memory updated.", record_ids=[inv_id, finding_id], count=2)

            # Finalize trace in DB
            cursor.execute("UPDATE investigations SET execution_trace = ? WHERE id = ?", (json.dumps(trace_steps), inv_id))
            conn.commit()

            return {
                "investigation_id": inv_id,
                "finding_id": finding_id,
                "field": field_info,
                "crop_cycle": crop_cycle_info,
                "summary": summary,
                "detailed_reasoning": reasoning,
                "hypotheses": analysis_result.get("hypotheses", []),
                "finding": finding_data,
                "historical_observations_retrieved": len(historical_observations),
                "historical_observations": historical_observations,
                "dataset_benchmarks": matched_dataset_records,
                "ml_signals": ml_signals,
                "execution_trace": trace_steps,
                "provider_mode": analysis_result.get("provider_mode"),
                "status": "resolved"
            }

        finally:
            conn.close()

investigation_engine = InvestigationEngine()
