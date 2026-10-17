"""
Dataset Management & Benchmark Exploration Router
Allows exploration of dataset schemas and sample records (capped at 50 max).
Fertilizer prediction removed due to dataset's chance-level distribution (used only as benchmark lookup).
Crop suitability prediction enabled using the public benchmark dataset model.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.datasets.dataset_manager import dataset_manager
from backend.routers.auth import get_current_farmer

router = APIRouter(prefix="/datasets", tags=["Datasets & Benchmarks"])

class CropPredictionRequest(BaseModel):
    n: float = Field(..., ge=0, description="Available Nitrogen (kg/ha)")
    p: float = Field(..., ge=0, description="Available Phosphorus (kg/ha)")
    k: float = Field(..., ge=0, description="Available Potassium (kg/ha)")
    temperature: float = Field(..., ge=-10, le=60, description="Ambient Temperature (°C)")
    humidity: float = Field(..., ge=0, le=100, description="Relative Humidity (%)")
    ph: float = Field(..., ge=0, le=14, description="Soil pH")
    rainfall: float = Field(..., ge=0, le=2000, description="Seasonal Rainfall (mm)")
    soil_moisture: float = Field(..., ge=0, le=100, description="Soil Moisture (%)")

@router.get("")
def list_datasets(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """Returns dataset inventory, row counts, provenance, and ML benchmark model status."""
    return {
        "datasets": dataset_manager.datasets_info,
        "models": {
            "crop_recommendation": {
                "benchmark_model": "RandomForestClassifier",
                "accuracy_on_public_benchmark": dataset_manager.crop_model_metrics.get("accuracy"),
                "status": "Available as public benchmark reference"
            }
        }
    }

@router.get("/{dataset_id}/schema")
def get_dataset_schema(dataset_id: str, farmer: Dict[str, Any] = Depends(get_current_farmer)):
    if dataset_id not in dataset_manager.datasets_info:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return {
        "dataset_id": dataset_id,
        "metadata": dataset_manager.datasets_info[dataset_id],
        "data_dictionary": dataset_manager.datasets_info[dataset_id].get("dictionary", [])
    }

@router.get("/{dataset_id}/sample")
def get_dataset_sample(
    dataset_id: str,
    limit: int = Query(default=15, ge=1, le=50),
    farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    """
    Returns sampled records from cached in-memory dataset frames.
    Capped at maximum 50 records to prevent denial of service and high bandwidth consumption.
    """
    df = dataset_manager.get_dataset_dataframe(dataset_id)
    if df is None:
        raise HTTPException(status_code=404, detail="Dataset not found or not initialized")

    sample_df = df.head(limit).fillna("")
    return {
        "dataset_id": dataset_id,
        "total_rows": len(df),
        "columns": list(df.columns),
        "records": sample_df.to_dict(orient="records")
    }

@router.post("/predict-crop")
def predict_crop(req: CropPredictionRequest, farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """
    Evaluates crop suitability on public benchmark model.
    Clearly marked as reference benchmark accuracy, not uncalibrated field prediction.
    """
    res = dataset_manager.predict_crop(
        n=req.n, p=req.p, k=req.k,
        temp=req.temperature, humidity=req.humidity,
        ph=req.ph, rainfall=req.rainfall,
        moisture=req.soil_moisture
    )
    return res
