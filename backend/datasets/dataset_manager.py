"""
AgriTrace Dataset Ingestion & Scientific Benchmarking Pipeline
Profiles and indexes public agronomic datasets:
1. Crop_recommendationV2.csv (Public Kaggle crop suitability benchmark)
2. data_core.csv (Empirical soil fertilizer lookup benchmark)
3. sensor_Crop_Dataset (1).csv (Multi-variety benchmark observations)
4. AgriNet_Dataset-G7TD2K.csv (KVK Bareilly Soil Test Certificate dataset, academic/research use)

All DataFrames are loaded ONCE into memory at startup to avoid repeated disk reads.
Controlled soil vocabulary normalizes naming discrepancies across datasets.
"""

import os
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

from backend.config import settings, DATA_DIR
from backend.graph.graph_manager import graph_manager

logger = logging.getLogger("agritrace.datasets")

# Controlled soil vocabulary and mapping table
SOIL_VOCABULARY_MAP = {
    "clay": "Clayey",
    "clayey": "Clayey",
    "silt": "Silt",
    "silt-loam": "Silt",
    "silt loam": "Silt",
    "loam": "Loamy",
    "loamy": "Loamy",
    "sandy": "Sandy",
    "sand": "Sandy",
    "alluvial": "Alluvial",
    "black": "Black",
    "red": "Red",
    "peaty": "Peaty",
    "saline": "Saline"
}

def normalize_soil_name(soil: Optional[str]) -> str:
    if not soil:
        return "Loamy"
    clean = str(soil).strip().lower()
    return SOIL_VOCABULARY_MAP.get(clean, soil.capitalize())

class DatasetManager:
    def __init__(self):
        self.datasets_info: Dict[str, Any] = {}
        self.cached_dfs: Dict[str, pd.DataFrame] = {}
        self.crop_model: Optional[RandomForestClassifier] = None
        self.crop_model_metrics: Dict[str, Any] = {}

    def initialize_and_profile(self):
        """Loads all datasets into memory once, computes profiles, and prepares benchmark models."""
        logger.info("Ingesting and caching datasets into memory...")
        self._load_and_profile_crop_recommendation()
        self._load_and_profile_data_core()
        self._load_and_profile_sensor_crop()
        self._load_and_profile_agrinet()
        self._train_crop_benchmark_model()
        self._populate_dataset_graph_nodes()
        logger.info("Dataset ingestion and benchmarking ready.")

    def get_dataset_dataframe(self, dataset_id: str) -> Optional[pd.DataFrame]:
        return self.cached_dfs.get(dataset_id)

    def _load_and_profile_crop_recommendation(self):
        path = settings.DATASET_CROP_REC
        if not os.path.exists(path):
            return
        df = pd.read_csv(path)
        self.cached_dfs["Crop_recommendationV2"] = df
        dict_profile = self._build_data_dictionary(df)
        self.datasets_info["Crop_recommendationV2"] = {
            "name": "Crop Recommendation Dataset",
            "file": "Crop_recommendationV2.csv",
            "rows": len(df),
            "columns": len(df.columns),
            "target": "label",
            "classes": sorted(df["label"].unique().tolist()),
            "dictionary": dict_profile,
            "provenance": "Kaggle Public Agronomic Benchmark (Atharva Ingle)",
            "license": "Open Data Commons Public Domain Dedication and License (PDDL)"
        }

    def _load_and_profile_data_core(self):
        path = settings.DATASET_DATA_CORE
        if not os.path.exists(path):
            return
        df = pd.read_csv(path)
        self.cached_dfs["data_core"] = df
        dict_profile = self._build_data_dictionary(df)
        self.datasets_info["data_core"] = {
            "name": "Soil Nutrient & Fertilizer Lookup Benchmark",
            "file": "data_core.csv",
            "rows": len(df),
            "columns": len(df.columns),
            "target": "Fertilizer Name",
            "classes": sorted(df["Fertilizer Name"].unique().tolist()),
            "dictionary": dict_profile,
            "provenance": "Source unknown, benchmark data. Feature distributions are uniform across classes (accuracy ~14.7%, chance level); utilized strictly as a reference lookup table, not a trained predictive classifier.",
            "license": "Public Benchmark Reference"
        }

    def _load_and_profile_sensor_crop(self):
        path = settings.DATASET_SENSOR_CROP
        if not os.path.exists(path):
            return
        df = pd.read_csv(path)
        # Normalize soil column in cached DF
        if "Soil_Type" in df.columns:
            df["Normalized_Soil"] = df["Soil_Type"].apply(normalize_soil_name)
        self.cached_dfs["sensor_Crop"] = df
        dict_profile = self._build_data_dictionary(df)
        self.datasets_info["sensor_Crop"] = {
            "name": "Multi-Variety Crop & Soil Sensor Benchmark",
            "file": "sensor_Crop_Dataset (1).csv",
            "rows": len(df),
            "columns": len(df.columns),
            "target": "Crop",
            "classes": sorted(df["Crop"].unique().tolist()) if "Crop" in df.columns else [],
            "dictionary": dict_profile,
            "provenance": "Public Agronomic Benchmark Dataset. Tabular records without timestamps or sensor identifiers; not a continuous telemetric stream.",
            "license": "Open Benchmark Data"
        }

    def _load_and_profile_agrinet(self):
        path = settings.DATASET_AGRINET_CSV
        if not os.path.exists(path):
            return
        df = pd.read_csv(path)
        self.cached_dfs["AgriNet_Dataset"] = df
        dict_profile = self._build_data_dictionary(df)
        self.datasets_info["AgriNet_Dataset"] = {
            "name": "AgriNet Soil Fertility Research Dataset (KVK Bareilly)",
            "file": "AgriNet_Dataset-G7TD2K.csv",
            "rows": len(df),
            "columns": len(df.columns),
            "target": "OC",
            "dictionary": dict_profile,
            "provenance": "ICAR-Indian Veterinary Research Institute (IVRI), Krishi Vigyan Kendra Bareilly. Soil test certificate records compiled for research and academic study.",
            "license": "Academic and Research Use Only",
            "redacted_sample_cards_available": 8
        }

    def _build_data_dictionary(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        entries = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            nulls = int(df[col].isnull().sum())
            unique_count = int(df[col].nunique())
            entry = {
                "column": col,
                "dtype": dtype,
                "null_count": nulls,
                "unique_values": unique_count
            }
            if pd.api.types.is_numeric_dtype(df[col]):
                entry["min"] = float(round(df[col].min(), 2))
                entry["max"] = float(round(df[col].max(), 2))
                entry["mean"] = float(round(df[col].mean(), 2))
                entry["std"] = float(round(df[col].std(), 2))
            else:
                top_vals = df[col].value_counts().head(5).to_dict()
                entry["top_samples"] = {str(k): int(v) for k, v in top_vals.items()}
            entries.append(entry)
        return entries

    def _train_crop_benchmark_model(self):
        """
        Trains public benchmark model in memory without storing untrusted pickles.
        Reports performance honestly as public benchmark accuracy, not field accuracy.
        """
        df = self.cached_dfs.get("Crop_recommendationV2")
        if df is None:
            return

        features = ['N', 'P', 'K', 'temperature', 'humidity', 'ph', 'rainfall', 'soil_moisture']
        X = df[features]
        y = df['label']

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
        clf = RandomForestClassifier(n_estimators=50, max_depth=10, random_state=42)
        clf.fit(X_train, y_train)

        y_pred = clf.predict(X_test)
        acc = float(accuracy_score(y_test, y_pred))

        self.crop_model = clf
        self.crop_model_metrics = {
            "model_type": "RandomForestClassifier",
            "accuracy": round(acc, 4),
            "scope": "accuracy on the public benchmark dataset",
            "features": features,
            "test_samples": len(y_test)
        }
        logger.info(f"Crop benchmark model trained on public dataset. Accuracy: {acc*100:.2f}%")

    def predict_crop(self, n: float, p: float, k: float, temp: float, humidity: float, ph: float, rainfall: float, moisture: float) -> Dict[str, Any]:
        if not self.crop_model:
            return {"error": "Crop benchmark model not initialized"}
        X_in = pd.DataFrame([{
            'N': n, 'P': p, 'K': k,
            'temperature': temp, 'humidity': humidity,
            'ph': ph, 'rainfall': rainfall,
            'soil_moisture': moisture
        }])
        pred = self.crop_model.predict(X_in)[0]
        probs = self.crop_model.predict_proba(X_in)[0]
        classes = self.crop_model.classes_
        top_indices = np.argsort(probs)[::-1][:3]
        top_predictions = [
            {"crop": classes[i], "probability": round(float(probs[i]), 4)}
            for i in top_indices
        ]
        return {
            "recommended_crop": pred,
            "top_candidates": top_predictions,
            "benchmark_accuracy": self.crop_model_metrics.get("accuracy"),
            "disclaimer": "Advice based on public benchmark dataset; not a laboratory test."
        }

    def search_benchmark_records(self, crop: str = None, soil_type: str = None, limit: int = 5) -> Dict[str, Any]:
        """
        Queries in-memory cached benchmark records for matching crop and normalized soil.
        Returns summary statistics (min, median, max) and honest example rows.
        """
        df = self.cached_dfs.get("sensor_Crop")
        if df is None:
            return {"summary_statistics": {}, "example_rows": []}

        cond = pd.Series([True] * len(df))
        if crop:
            cond = cond & (df['Crop'].str.lower() == crop.lower())
        if soil_type:
            norm_soil = normalize_soil_name(soil_type)
            if "Normalized_Soil" in df.columns:
                cond = cond & (df['Normalized_Soil'].str.lower() == norm_soil.lower())
            elif "Soil_Type" in df.columns:
                cond = cond & (df['Soil_Type'].str.lower() == norm_soil.lower())

        matched = df[cond]
        count = len(matched)

        summary = {}
        for col in ['Moisture', 'Temperature', 'Humidity']:
            if col in matched.columns and not matched[col].empty:
                summary[col.lower()] = {
                    "min": round(float(matched[col].min()), 1),
                    "median": round(float(matched[col].median()), 1),
                    "max": round(float(matched[col].max()), 1)
                }

        examples = matched.head(min(limit, 10)).fillna("").to_dict(orient="records")

        return {
            "matched_count": count,
            "summary_statistics": summary,
            "example_rows": examples
        }

    def _populate_dataset_graph_nodes(self):
        """Creates Dataset graph nodes in GraphManager."""
        for ds_id, meta in self.datasets_info.items():
            graph_manager.create_or_update_node("Dataset", {
                "id": f"dataset_{ds_id}",
                "name": meta["name"],
                "file": meta["file"],
                "rows": meta["rows"],
                "columns": meta["columns"],
                "provenance": meta["provenance"]
            })

dataset_manager = DatasetManager()
