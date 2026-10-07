"""Pydantic response schemas for the public API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UploadResponse(BaseModel):
    """Response returned after a file is queued or deduplicated."""

    id: str
    status: str
    duplicate: bool = False


class FileResponse(BaseModel):
    """Public file processing state."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    projected_crs_used: str | None
    status: str
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class Measurements(BaseModel):
    """Per-feature measurements using metric units."""

    area_m2: float | None
    area_hectares: float | None
    area_acres: float | None
    length_m: float | None
    length_km: float | None
    geodesic_area_m2: float | None
    geodesic_length_m: float | None
    difference_percent: float | None


class FeatureResponse(BaseModel):
    """Feature geometry, properties, and quality information."""

    feature_id: int = Field(description="Zero-based feature index within the uploaded file.")
    geometry_type: str
    crs: str | None
    properties: dict[str, Any]
    geometry: dict[str, Any] | None
    measurements: Measurements | None
    is_supported: bool
    warnings: list[str]


class FileSummary(BaseModel):
    """Aggregated measurements and feature counts for one uploaded file."""

    total_area_m2: float
    total_area_hectares: float
    total_area_acres: float
    total_length_m: float
    total_length_km: float
    counts_per_geometry_type: dict[str, int]
    unsupported_feature_count: int
    repaired_feature_count: int


class FileMeasurementsResponse(BaseModel):
    """Aggregate and per-feature measurements for a completed file."""

    file_id: str
    filename: str
    crs: str | None
    projected_crs_used: str | None
    summary: FileSummary
    features: list[FeatureResponse]