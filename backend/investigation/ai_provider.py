"""
Modular AI Provider Layer
Honors AI_PROVIDER environment setting ('auto', 'gemini', 'rules').
If Gemini is configured, sends minimized sanitized context (no farmer name, no username, no GPS coordinates),
wraps user text in untrusted delimiters, validates responses against Pydantic schemas,
clamps scores to [0.0, 1.0], and passes uploaded images when available.
If Gemini is not configured, provides a transparent deterministic rule engine based on rules.yaml.
Outputs are labeled as advice, not lab results.
"""

import os
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field

from backend.config import settings

logger = logging.getLogger("agritrace.ai")

RULES_FILE = Path(__file__).resolve().parent / "rules.yaml"

def load_rules() -> Dict[str, Any]:
    if RULES_FILE.exists():
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Could not load rules.yaml: {e}")
    return {}

# Pydantic schema for validating AI output
class AIHypothesis(BaseModel):
    title: str
    description: str
    status: str = "supported"  # supported | under_investigation | rejected
    evidence_strength: str = "Moderate"  # Strong | Moderate | Weak
    confidence: Optional[float] = None
    supporting_evidence: List[str] = Field(default_factory=list)

class AIFinding(BaseModel):
    diagnosis: str
    confidence_score: Optional[float] = None
    evidence_strength: str = "Moderate"
    recommended_actions: List[str] = Field(default_factory=list)
    uncertainties: List[str] = Field(default_factory=list)
    suggested_next_checks: List[str] = Field(default_factory=list)

class AIAnalysisResponse(BaseModel):
    summary: str
    detailed_reasoning: str
    hypotheses: List[AIHypothesis] = Field(default_factory=list)
    finding: AIFinding

class AIProvider:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        self.provider_setting = os.getenv("AI_PROVIDER", settings.AI_PROVIDER).lower()
        self.client = None
        self.rules_config = load_rules()

        if self.api_key and self.provider_setting != "rules":
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Could not initialize Google GenAI client: {e}")

    def is_gemini_active(self) -> bool:
        if self.provider_setting == "rules":
            return False
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
        dataset_matches: Dict[str, Any],
        ml_predictions: Optional[Dict[str, Any]] = None,
        image_bytes: Optional[bytes] = None,
        image_mime: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Orchestrates agricultural reasoning.
        Uses Gemini if configured, otherwise falls back to deterministic rule engine.
        Always tags execution mode and evidence sources.
        """
        if self.is_gemini_active():
            try:
                return self._call_gemini(
                    field_info=field_info,
                    crop_cycle=crop_cycle,
                    user_problem=user_problem,
                    current_obs=current_observations,
                    hist_obs=historical_observations,
                    graph_context=graph_context,
                    dataset_matches=dataset_matches,
                    ml_predictions=ml_predictions,
                    image_bytes=image_bytes,
                    image_mime=image_mime
                )
            except Exception as e:
                logger.warning(f"Gemini call failed, falling back to rule engine: {e}")
                res = self._deterministic_reasoning(
                    field_info=field_info,
                    crop_cycle=crop_cycle,
                    user_problem=user_problem,
                    current_obs=current_observations,
                    hist_obs=historical_observations,
                    graph_context=graph_context,
                    dataset_matches=dataset_matches,
                    ml_predictions=ml_predictions
                )
                res["fallback_reason"] = "Live AI service temporarily unavailable; rule-based guidance generated."
                return res
        else:
            return self._deterministic_reasoning(
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
        # Privacy minimization: Send NO farmer name, NO username, NO exact GPS coordinates
        minimized_context = {
            "field": {
                "soil_type": kwargs.get("field_info", {}).get("soil_type"),
                "drainage": kwargs.get("field_info", {}).get("drainage_condition"),
                "area_unit": kwargs.get("field_info", {}).get("area_unit")
            },
            "crop": {
                "crop_name": kwargs.get("crop_cycle", {}).get("crop_name"),
                "variety": kwargs.get("crop_cycle", {}).get("variety"),
                "growth_stage": kwargs.get("crop_cycle", {}).get("growth_stage")
            },
            "current_measurements": [o.get("data") for o in kwargs.get("current_obs", []) if o.get("data")],
            "historical_measurements": [o.get("data") for o in kwargs.get("hist_obs", []) if o.get("data")],
            "benchmark_matches": kwargs.get("dataset_matches", {}).get("summary_statistics", {})
        }

        # Put user text in a delimited untrusted block to prevent prompt injection
        user_text = kwargs.get("user_problem", "")
        clean_user_text = str(user_text)[:500].replace("```", "'''")

        prompt = f"""
You are an agricultural advisor assistant for smallholder farmers.
Analyze the farmer's problem using only the provided field measurements and historical records.
All advice must be practical, safe, low-cost, and labeled as guidance rather than laboratory confirmation.

--- BEGIN CONTEXT (ANONYMIZED FIELD DATA) ---
{json.dumps(minimized_context, indent=2)}
--- END CONTEXT ---

--- BEGIN UNTRUSTED USER OBSERVATION ---
\"\"\"{clean_user_text}\"\"\"
--- END UNTRUSTED USER OBSERVATION ---

Respond with JSON adhering to this schema:
{{
  "summary": "1-2 sentence plain-language summary of what was found",
  "detailed_reasoning": "Clear explanation connecting current vs prior readings",
  "hypotheses": [
    {{
      "title": "Cause name",
      "description": "Explanation",
      "status": "supported | under_investigation | rejected",
      "evidence_strength": "Strong | Moderate | Weak",
      "supporting_evidence": ["Evidence point 1"]
    }}
  ],
  "finding": {{
    "diagnosis": "Primary diagnosis or statement that more data is needed",
    "evidence_strength": "Strong | Moderate | Weak",
    "recommended_actions": ["Practical step 1", "Practical step 2"],
    "uncertainties": ["What cannot be confirmed without a soil or lab test"],
    "suggested_next_checks": ["What to check next on the farm"]
  }}
}}
"""

        contents: List[Any] = [prompt]

        # Pass multimodal image if available
        if kwargs.get("image_bytes") and kwargs.get("image_mime"):
            try:
                from google.genai import types
                part = types.Part.from_bytes(
                    data=kwargs["image_bytes"],
                    mime_type=kwargs["image_mime"]
                )
                contents.append(part)
            except Exception as e:
                logger.warning(f"Could not attach image to Gemini request: {e}")

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config={"response_mime_type": "application/json"}
        )

        if not response or not response.text:
            raise ValueError("Empty response received from AI model")

        raw_parsed = json.loads(response.text)
        validated = AIAnalysisResponse.model_validate(raw_parsed).model_dump()

        # Clamp any confidence scores and cap list lengths
        if validated.get("finding", {}).get("confidence_score") is not None:
            validated["finding"]["confidence_score"] = max(0.0, min(1.0, float(validated["finding"]["confidence_score"])))
        validated["finding"]["recommended_actions"] = validated["finding"]["recommended_actions"][:4]
        validated["finding"]["uncertainties"] = validated["finding"]["uncertainties"][:3]

        validated["provider_mode"] = f"AI-generated ({self.model_name})"
        validated["model"] = self.model_name
        validated["disclaimer"] = "Advice, not a lab result. Confirm with your local KVK or Kisan helpline before spraying or buying inputs."
        return validated

    def _deterministic_reasoning(
        self,
        field_info: Dict[str, Any],
        crop_cycle: Dict[str, Any],
        user_problem: str,
        current_obs: List[Dict[str, Any]],
        hist_obs: List[Dict[str, Any]],
        graph_context: Dict[str, Any],
        dataset_matches: Dict[str, Any],
        ml_predictions: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Rule-based agronomic guidance engine based on rules.yaml citations.
        Computes evidence strength (Strong, Moderate, Weak) based on real inputs backing each claim.
        Never fabricates hard-coded percentage confidences.
        """
        problem_lower = user_problem.lower()
        crop_name = crop_cycle.get("crop_name", "Crop").capitalize()

        # Extract real measurements safely (handling 0 properly)
        latest_moisture = None
        historical_moisture = None
        latest_n = None
        latest_p = None
        latest_k = None
        latest_ph = None

        for obs in current_obs:
            d = obs.get("data", {})
            if d.get("soil_moisture") is not None: latest_moisture = float(d["soil_moisture"])
            elif d.get("moisture") is not None: latest_moisture = float(d["moisture"])

            if d.get("nitrogen") is not None: latest_n = float(d["nitrogen"])
            elif d.get("N") is not None: latest_n = float(d["N"])

            if d.get("phosphorus") is not None: latest_p = float(d["phosphorus"])
            elif d.get("P") is not None: latest_p = float(d["P"])

            if d.get("potassium") is not None: latest_k = float(d["potassium"])
            elif d.get("K") is not None: latest_k = float(d["K"])

            if d.get("ph") is not None: latest_ph = float(d["ph"])
            elif d.get("pH") is not None: latest_ph = float(d["pH"])

        for obs in hist_obs:
            d = obs.get("data", {})
            if historical_moisture is None:
                if d.get("soil_moisture") is not None: historical_moisture = float(d["soil_moisture"])
                elif d.get("moisture") is not None: historical_moisture = float(d["moisture"])

        hypotheses = []
        uncertainties = []
        recommendations = []
        reasoning_points = []
        rules_applied = []

        # Rule 1: Soil Moisture Stress / Drop
        if latest_moisture is not None and historical_moisture is not None:
            drop = historical_moisture - latest_moisture
            reasoning_points.append(
                f"Earlier soil moisture was {historical_moisture:.1f}%; current reading is {latest_moisture:.1f}% (net change of {drop:.1f}%)."
            )
            if latest_moisture < 20.0 or (drop >= 10.0 and latest_moisture < 25.0):
                hypotheses.append({
                    "title": "Soil Moisture Deficit",
                    "description": f"Significant moisture decline ({historical_moisture:.1f}% to {latest_moisture:.1f}%) observed in field.",
                    "status": "supported",
                    "evidence_strength": "Strong",
                    "supporting_evidence": [
                        f"Earlier recorded reading: {historical_moisture:.1f}%",
                        f"Recent recorded reading: {latest_moisture:.1f}%"
                    ]
                })
                recommendations.append("Apply a light irrigation cycle if water is available; check root depth moisture.")
                rules_applied.append("Soil Moisture Threshold (FAO Guidelines No. 56)")
            else:
                hypotheses.append({
                    "title": "Soil Moisture Deficit",
                    "description": "Current moisture levels are within acceptable range for this crop.",
                    "status": "rejected",
                    "evidence_strength": "Strong",
                    "supporting_evidence": [f"Current soil moisture reading is {latest_moisture:.1f}%"]
                })
        elif latest_moisture is not None and latest_moisture < 20.0:
            hypotheses.append({
                "title": "Soil Moisture Deficit",
                "description": f"Current soil moisture reading ({latest_moisture:.1f}%) is in the low moisture range.",
                "status": "supported",
                "evidence_strength": "Moderate",
                "supporting_evidence": [f"Current reading: {latest_moisture:.1f}%"]
            })
            recommendations.append("Plan an irrigation cycle within 24 to 48 hours to avoid plant stress.")
            rules_applied.append("Soil Moisture Threshold (FAO Guidelines No. 56)")

        # Rule 2: Nitrogen Chlorosis
        yellowing_symptom = any(kw in problem_lower for kw in ["yellow", "chlorosis", "pale", "stunted", "wilting", "pila"])
        if yellowing_symptom:
            if latest_n is not None and latest_n < 100.0:
                hypotheses.append({
                    "title": "Nitrogen Deficiency",
                    "description": f"Measured nitrogen ({latest_n:.1f} kg/ha) is in the low rating category (< 100 kg/ha).",
                    "status": "supported",
                    "evidence_strength": "Strong",
                    "supporting_evidence": [
                        f"Soil Nitrogen reading: {latest_n:.1f} kg/ha",
                        "Visible lower-leaf yellowing reported"
                    ]
                })
                recommendations.append("Check with your local agricultural officer before applying nitrogen fertilizer.")
                rules_applied.append("Soil Health Card Nutrient Rating (Low Available N < 280 kg/ha)")
            else:
                hypotheses.append({
                    "title": "Nutrient Deficiency (Nitrogen or Micronutrients)",
                    "description": "Yellowing symptoms reported. A soil or leaf test is needed to determine the exact shortage.",
                    "status": "under_investigation",
                    "evidence_strength": "Weak",
                    "supporting_evidence": ["Visual leaf yellowing reported by farmer"]
                })
                uncertainties.append("Soil fertility test values are required to distinguish nitrogen deficit from micronutrient or water stress.")
                recommendations.append("Take a soil sample to your nearest Krishi Vigyan Kendra (KVK) for testing.")
                rules_applied.append("General Chlorosis Screening Protocol")

        # Rule 3: Foliar Symptoms
        foliar_symptoms = any(kw in problem_lower for kw in ["spot", "brown", "blast", "blight", "pest", "caterpillar", "hole", "rot", "keeda"])
        if foliar_symptoms:
            hypotheses.append({
                "title": f"Foliar Pathogen or Pest Screening in {crop_name}",
                "description": "Canopy symptoms reported on foliage. Visual field inspection is recommended.",
                "status": "under_investigation",
                "evidence_strength": "Weak",
                "supporting_evidence": ["Symptom description in farmer query"]
            })
            uncertainties.append("Close-up photo of leaf underside or laboratory confirmation required to identify specific pest or pathogen.")
            recommendations.append("Inspect the underside of leaves in daylight; avoid overhead sprinkler watering.")
            rules_applied.append("Foliar Symptom Screening Protocol")

        if not hypotheses:
            hypotheses.append({
                "title": f"Field Assessment for {crop_name}",
                "description": "General review based on available records.",
                "status": "under_investigation",
                "evidence_strength": "Weak",
                "supporting_evidence": ["Farmer query logged"]
            })

        # Add ML signals if available with genuine data
        if ml_predictions and "predicted_crop" in ml_predictions:
            rec_crop = ml_predictions["predicted_crop"]
            reasoning_points.append(f"Public benchmark comparison: Current conditions match '{rec_crop}' profiles in benchmark datasets.")

        # Determine overall evidence strength
        supported = [h for h in hypotheses if h["status"] == "supported"]
        if supported:
            primary_h = supported[0]
            diagnosis = f"Evidence indicates: {primary_h['title']}. {primary_h['description']}"
            overall_strength = primary_h["evidence_strength"]
        else:
            diagnosis = "Tentative guidance. Measurements are insufficient to confirm a single primary cause."
            overall_strength = "Weak"
            uncertainties.append("Multiple overlapping factors possible. Additional soil readings or photo would help clarify.")

        if not recommendations:
            recommendations = [
                "Monitor your crop over the next 48 hours for changes.",
                "Take a clear photo of affected leaves or log a new soil reading."
            ]

        detailed_reasoning = " ".join(reasoning_points) if reasoning_points else "Baseline agronomic guidance applied to your field records."

        return {
            "summary": f"Guidance for {crop_name}: {diagnosis}",
            "detailed_reasoning": detailed_reasoning,
            "hypotheses": hypotheses,
            "finding": {
                "diagnosis": diagnosis,
                "evidence_strength": overall_strength,
                "recommended_actions": recommendations,
                "uncertainties": uncertainties,
                "suggested_next_checks": [
                    "Check soil moisture at 15 cm depth.",
                    "Take a photo of affected leaves in daylight.",
                    "Verify local weather forecast before watering or applying inputs."
                ]
            },
            "provider_mode": "Rule-based guidance",
            "rules_applied": rules_applied,
            "disclaimer": "Advice, not a lab result. Check with your local KVK or the Kisan helpline (1800-180-1551) before spraying or buying inputs."
        }

ai_provider = AIProvider()
