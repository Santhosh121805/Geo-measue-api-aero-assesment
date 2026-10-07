"""Background orchestration for uploads, feature measurements, and status."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry import mapping

from app import database
from app.models import Feature, UploadedFile
from app.services.file_reader import read_uploaded_file
from app.services.measurements import measure_geometry

logger = logging.getLogger(__name__)


def _json_value(value: Any) -> Any:
    """Convert pandas and NumPy scalar values into JSON-safe properties."""
    if value is None:
        return None
    try:
        missing = pd.isna(value)
        if not hasattr(missing, "__len__") and bool(missing):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return _json_value(value.item())
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _properties(row: pd.Series, geometry_name: str) -> dict[str, Any]:
    return {
        str(key): _json_value(value)
        for key, value in row.items()
        if key != geometry_name
    }


def process_file(file_id: str, source_path: str) -> None:
    """Process an upload using a dedicated database session and persist its outcome."""
    path = Path(source_path)
    session = database.SessionLocal()
    try:
        uploaded_file = session.get(UploadedFile, file_id)
        if uploaded_file is None:
            logger.error("Background task could not find uploaded file %s", file_id)
            return

        uploaded_file.status = "PROCESSING"
        session.commit()

        result = read_uploaded_file(path, uploaded_file.file_type)
        frame = result.frame
        if frame.empty:
            raise ValueError("The uploaded file contains no features.")

        source_crs = frame.crs
        uploaded_file.crs = source_crs.to_string() if source_crs else None
        geometry_name = frame.geometry.name
        projected_crs_values: set[str] = set()

        for feature_index, (_, row) in enumerate(frame.iterrows()):
            geometry = row[geometry_name]
            measurement = measure_geometry(geometry, source_crs)
            if uploaded_file.crs is None and measurement.source_crs:
                uploaded_file.crs = measurement.source_crs
            if measurement.projected_crs:
                projected_crs_values.add(measurement.projected_crs)
            geometry_json = mapping(measurement.geometry) if measurement.geometry is not None else None
            session.add(
                Feature(
                    file_id=file_id,
                    feature_index=feature_index,
                    geometry_type=measurement.geometry_type,
                    geometry=geometry_json,
                    properties=_properties(row, geometry_name),
                    is_supported=measurement.is_supported,
                    area_m2=measurement.area_m2,
                    length_m=measurement.length_m,
                    geodesic_area_m2=measurement.geodesic_area_m2,
                    geodesic_length_m=measurement.geodesic_length_m,
                    difference_percent=measurement.difference_percent,
                    was_repaired=measurement.was_repaired,
                    warnings=[*result.warnings, *measurement.warnings],
                )
            )

        uploaded_file.feature_count = len(frame)
        uploaded_file.projected_crs_used = "; ".join(sorted(projected_crs_values)) or None
        uploaded_file.status = "COMPLETED"
        uploaded_file.error_message = None
        uploaded_file.completed_at = datetime.now(timezone.utc)
        session.commit()
    except Exception as error:
        session.rollback()
        logger.exception("Failed to process uploaded file %s", file_id)
        uploaded_file = session.get(UploadedFile, file_id)
        if uploaded_file is not None:
            uploaded_file.status = "FAILED"
            uploaded_file.error_message = str(error) or "File processing failed."
            uploaded_file.completed_at = datetime.now(timezone.utc)
            session.commit()
    finally:
        session.close()
        path.unlink(missing_ok=True)