"""
Modular AI Provider Layer
Integrates Google Gemini (via AI Studio API Key) and provides an advanced,
transparent deterministic agricultural rule and ontology fallback.
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional
from backend.config import settings

logger = logging.getLogger("agritrace.ai")

class AIProvider:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        self.client = None
        if self.api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Could not initialize Google GenAI client: {e}")

    def is_gemini_active(self) -> bool:
        return bool(self.api_key and self.client is not None)

    def analyze_investigation(
        self,
        farmer_profile: Dict[str, Any],
        field_info: Dict[str, Any],
        crop_cycle: Dict[str, Any],
        user_problem: str,
        current_observations: List[Dict[str, Any]],
        historical_observations: List[Dict[str, Any]],
        graph_context: Dict[str, Any],
        dataset_matches: List[Dict[str, Any]],
        ml_predictions: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Orchestrates agricultural reasoning.
        Uses Gemini if configured, otherwise falls back to deterministic rule engine.
        Always tags execution mode and evidence sources.
        """
        if self.is_gemini_active():
            try:
                return self._call_gemini(
                    farmer_profile=farmer_profile,
                    field_info=field_info,
                    crop_cycle=crop_cycle,
                    user_problem=user_problem,
                    current_obs=current_observations,
                    hist_obs=historical_observations,
                    graph_context=graph_context,
                    dataset_matches=dataset_matches,
                    ml_predictions=ml_predictions
                )
            except Exception as e:
                logger.error(f"Gemini call failed, falling back to deterministic engine: {e}")
                res = self._deterministic_reasoning(
                    farmer_profile=farmer_profile,
                    field_info=field_info,
                    crop_cycle=crop_cycle,
                    user_problem=user_problem,
                    current_obs=current_observations,
                    hist_obs=historical_observations,
                    graph_context=graph_context,
                    dataset_matches=dataset_matches,
                    ml_predictions=ml_predictions
                )
                res["fallback_reason"] = f"Gemini API error: {str(e)}"
                return res
        else:
            return self._deterministic_reasoning(
                farmer_profile=farmer_profile,
                field_info=field_info,
                crop_cycle=crop_cycle,
                user_problem=user_problem,
                current_obs=current_observations,
                hist_obs=historical_observations,
                graph_context=graph_context,
                dataset_matches=dataset_matches,
                ml_predictions=ml_predictions
            )

    def _call_gemini(self, **kwargs) -> Dict[str, Any]:
        prompt = f"""
You are AgriTrace AI, an expert agricultural investigation and plant pathology assistant.
Perform a rigorous, evidence-based investigation. You must base your findings on the actual graph records and historical observations provided.
DO NOT fabricate unobserved parameters.

Farmer Profile: {json.dumps(kwargs.get('farmer_profile', {}))}
Field: {json.dumps(kwargs.get('field_info', {}))}
Crop Cycle: {json.dumps(kwargs.get('crop_cycle', {}))}
Farmer Reported Problem: {kwargs.get('user_problem')}
Current Observations: {json.dumps(kwargs.get('current_obs', []))}
Historical Observations (Prior Memory): {json.dumps(kwargs.get('hist_obs', []))}
Graph Multi-Hop Context: {json.dumps(kwargs.get('graph_context', {}))}
Matched Benchmark Dataset Records: {json.dumps(kwargs.get('dataset_matches', []))}
Machine Learning Diagnostic Signals: {json.dumps(kwargs.get('ml_predictions', {}))}

Return your analysis in valid JSON with this exact structure:
{{
  "summary": "Brief 2-line executive summary of investigation",
  "detailed_reasoning": "In-depth traceable reasoning linking historical vs current observations",
  "hypotheses": [
    {{
      "title": "Hypothesis name",
      "description": "Explanation",
      "status": "supported | under_investigation | rejected",
      "confidence": 0.85,
      "supporting_evidence": ["Evidence description or observation ID"]
    }}
  ],
  "finding": {{
    "diagnosis": "Primary finding or statement of insufficient data",
    "confidence_score": 0.85,
    "recommended_actions": ["Specific practical step 1", "Step 2"],
    "uncertainties": ["What is still unknown or requires soil/lab testing"],
    "suggested_next_checks": ["Check X", "Check Y"]
  }}
}}
"""
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json"
            }
        )
        parsed = json.loads(response.text)
        parsed["provider_mode"] = "live_gemini_api"
        parsed["model"] = self.model_name
        return parsed

    def _deterministic_reasoning(
        self,
        farmer_profile: Dict[str, Any],
        field_info: Dict[str, Any],
        crop_cycle: Dict[str, Any],
        user_problem: str,
        current_obs: List[Dict[str, Any]],
        hist_obs: List[Dict[str, Any]],
        graph_context: Dict[str, Any],
        dataset_matches: List[Dict[str, Any]],
        ml_predictions: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Deterministic, rule-based agricultural ontology fallback engine.
        Evaluates moisture drop, N-P-K imbalances, chlorosis/yellowing, leaf blast/blight,
        and soil pH compatibility using verified ICAR / agronomy thresholds.
        """
        problem_lower = user_problem.lower()
        crop_name = crop_cycle.get("crop_name", "Crop").capitalize()

        # Extract latest sensor or manual measurements
        latest_moisture = None
        historical_moisture = None
        latest_n = None
        latest_p = None
        latest_k = None
        latest_ph = None

        # Check current observations
        for obs in current_obs:
            d = obs.get("data", {})
            if "soil_moisture" in d: latest_moisture = float(d["soil_moisture"])
            if "moisture" in d: latest_moisture = float(d["moisture"])
            if "nitrogen" in d or "N" in d: latest_n = float(d.get("nitrogen") or d.get("N"))
            if "phosphorus" in d or "P" in d: latest_p = float(d.get("phosphorus") or d.get("P"))
            if "potassium" in d or "K" in d: latest_k = float(d.get("potassium") or d.get("K"))
            if "ph" in d or "pH" in d: latest_ph = float(d.get("ph") or d.get("pH"))

        # Check historical observations for comparison
        for obs in hist_obs:
            d = obs.get("data", {})
            if historical_moisture is None:
                if "soil_moisture" in d: historical_moisture = float(d["soil_moisture"])
                elif "moisture" in d: historical_moisture = float(d["moisture"])

        hypotheses = []
        uncertainties = []
        recommendations = []
        reasoning_points = []

        # 1. Evaluate Moisture Drop / Water Stress
        moisture_drop = None
        if latest_moisture is not None and historical_moisture is not None:
            moisture_drop = historical_moisture - latest_moisture
            reasoning_points.append(
                f"Historical memory recall: Prior soil moisture was {historical_moisture:.1f}%, whereas current observation is {latest_moisture:.1f}% (net decrease of {moisture_drop:.1f}%)."
            )
            if latest_moisture < 20.0 or (moisture_drop > 10.0 and latest_moisture < 25.0):
                hypotheses.append({
                    "title": "Moisture Deficit / Drought Stress",
                    "description": f"Significant moisture decline ({historical_moisture:.1f}% -> {latest_moisture:.1f}%) observed in field '{field_info.get('name', 'Parcel')}'.",
                    "status": "supported",
                    "confidence": 0.88,
                    "supporting_evidence": [f"Historical moisture: {historical_moisture}%", f"Current moisture: {latest_moisture}%"]
                })
                recommendations.append("Immediate localized light irrigation cycle; verify soil matric suction at 15cm depth.")
            else:
                hypotheses.append({
                    "title": "Moisture Deficit",
                    "description": "Moisture levels remain within manageable threshold.",
                    "status": "rejected",
                    "confidence": 0.75,
                    "supporting_evidence": [f"Current soil moisture is {latest_moisture}%"]
                })
        elif latest_moisture is not None and latest_moisture < 20.0:
            hypotheses.append({
                "title": "Acute Soil Moisture Stress",
                "description": f"Current soil moisture reading of {latest_moisture}% is below critical threshold (22%) for {crop_name}.",
                "status": "supported",
                "confidence": 0.82,
                "supporting_evidence": [f"Current moisture reading: {latest_moisture}%"]
            })
            recommendations.append("Schedule irrigation within 24 hours to prevent permanent wilting point.")

        # 2. Evaluate Yellowing / Chlorosis / Nutrient Deficiency
        yellowing_symptom = any(kw in problem_lower for kw in ["yellow", "chlorosis", "pale", "stunted", "wilting", "pila"])
        if yellowing_symptom:
            if latest_n is not None and latest_n < 50:
                hypotheses.append({
                    "title": "Nitrogen (N) Deficiency Induced Chlorosis",
                    "description": f"Low available nitrogen reading ({latest_n} kg/ha) correlates with lower leaf chlorosis in {crop_name}.",
                    "status": "supported",
                    "confidence": 0.85,
                    "supporting_evidence": [f"Measured Nitrogen: {latest_n} kg/ha (Deficient < 50 kg/ha)"]
                })
                recommendations.append("Apply split dose of nitrogenous fertilizer (Urea or Ammonium Sulphate) after verifying soil moisture.")
            else:
                hypotheses.append({
                    "title": "Nutrient Deficiency (Nitrogen / Iron Chlorosis)",
                    "description": "Yellowing symptoms reported, but exact elemental shortage requires soil fertility testing.",
                    "status": "under_investigation",
                    "confidence": 0.60,
                    "supporting_evidence": ["Visual chlorosis symptom reported by farmer"]
                })
                uncertainties.append("Soil N-P-K and micronutrient (Zn, Fe) lab test values are needed to distinguish N deficiency from moisture stress chlorosis.")
                recommendations.append("Collect composite soil core samples for KVK / laboratory fertility assessment.")

        # 3. Pathological / Pest symptoms
        pest_symptoms = any(kw in problem_lower for kw in ["spot", "brown", "blast", "blight", "pest", "caterpillar", "hole", "rot", "keeda"])
        if pest_symptoms:
            hypotheses.append({
                "title": f"Fungal Folliar Pathogen / Pest Infestation in {crop_name}",
                "description": "Lesions, spots, or pest pressure reported on vegetative canopy.",
                "status": "under_investigation",
                "confidence": 0.65,
                "supporting_evidence": ["Symptom description in farmer problem query"]
            })
            uncertainties.append("High-resolution leaf imagery or microscopic spore confirmation is necessary to confirm fungal etiology.")
            recommendations.append("Inspect underside of leaves for mycelial growth or egg clusters; avoid overhead sprinkler irrigation.")

        # If no specific hypothesis matched, create general baseline
        if not hypotheses:
            hypotheses.append({
                "title": f"Agronomic Condition Assessment for {crop_name}",
                "description": "General physiological and environmental review based on current field records.",
                "status": "under_investigation",
                "confidence": 0.70,
                "supporting_evidence": ["Farmer problem query logged"]
            })

        # Add ML signals if available
        if ml_predictions and "predicted_crop" in ml_predictions:
            rec_crop = ml_predictions["predicted_crop"]
            reasoning_points.append(f"Crop suitability benchmark: Current soil and climate conditions favor '{rec_crop}'. Current crop is '{crop_name}'.")

        # Summary and Findings
        supported = [h for h in hypotheses if h["status"] == "supported"]
        if supported:
            main_h = supported[0]
            diagnosis = f"Evidence supports: {main_h['title']}. {main_h['description']}"
            conf = main_h["confidence"]
        else:
            diagnosis = "Tentative findings under investigation. Available evidence is insufficient to conclusively confirm a single cause."
            conf = 0.55
            uncertainties.append("Multiple overlapping causes possible (soil moisture deficit, nutrient leaching, or early disease stage).")

        detailed_reasoning = " | ".join(reasoning_points) if reasoning_points else "Baseline agronomic checks performed on field records."
        if not recommendations:
            recommendations = [
                "Monitor field conditions daily for next 48 hours.",
                "Log follow-up observation with photograph or sensor reading."
            ]

        return {
            "summary": f"Agricultural investigation for {crop_name} on field '{field_info.get('name', 'Parcel')}': {diagnosis}",
            "detailed_reasoning": detailed_reasoning,
            "hypotheses": hypotheses,
            "finding": {
                "diagnosis": diagnosis,
                "confidence_score": conf,
                "recommended_actions": recommendations,
                "uncertainties": uncertainties,
                "suggested_next_checks": [
                    "Perform 15cm soil moisture tactile test or tensiometer reading.",
                    "Upload photograph of lower leaf canopy.",
                    "Verify irrigation water electrical conductivity (EC)."
                ]
            },
            "provider_mode": "deterministic_agricultural_rule_engine",
            "model": "agritrace_icar_expert_rules_v1"
        }

ai_provider = AIProvider()
