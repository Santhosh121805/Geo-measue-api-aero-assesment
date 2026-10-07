"""File upload, status, and measurement endpoints."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Feature, UploadedFile
from app.schemas import (
    FeatureResponse,
    FileMeasurementsResponse,
    FileResponse,
    FileSummary,
    Measurements,
    UploadResponse,
)
from app.services.file_reader import validate_zip_safety
from app.services.measurements import ACRES_PER_SQUARE_METER
from app.services.processor import process_file

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/", response_model=UploadResponse, status_code=202)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
) -> UploadResponse | JSONResponse:
    """Validate and queue a KML or zipped Shapefile for processing."""
    original_name = PurePosixPath((file.filename or "").replace("\\", "/")).name
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".kml", ".zip"}:
        raise HTTPException(status_code=400, detail="Only .kml and .zip files are accepted.")
    if not original_name or original_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="A valid filename is required.")

    file_type = "kml" if suffix == ".kml" else "shapefile"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid4())
    stored_path = settings.upload_dir / f"{upload_id}{suffix}"
    digest = hashlib.sha256()
    file_size = 0
    try:
        with stored_path.open("wb") as output:
            while chunk := await file.read(settings.upload_chunk_size_bytes):
                file_size += len(chunk)
                if file_size > settings.max_file_size_bytes:
                    if settings.max_file_size_bytes >= 1024 * 1024:
                        limit_description = (
                            f"{settings.max_file_size_bytes / (1024 * 1024):g} MB"
                        )
                    else:
                        limit_description = f"{settings.max_file_size_bytes} bytes"
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the upload limit of {limit_description}.",
                    )
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if file_size == 0:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if file_type == "shapefile":
        try:
            validate_zip_safety(stored_path)
        except ValueError as error:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(error)) from error

    existing = session.scalar(
        select(UploadedFile)
        .where(UploadedFile.file_hash == digest.hexdigest(), UploadedFile.status == "COMPLETED")
        .order_by(UploadedFile.created_at.desc())
    )
    if existing is not None:
        stored_path.unlink(missing_ok=True)
        return JSONResponse(
            status_code=200,
            content=UploadResponse(id=existing.id, status=existing.status, duplicate=True).model_dump(),
        )

    record = UploadedFile(
        id=upload_id,
        filename=original_name,
        file_type=file_type,
        file_hash=digest.hexdigest(),
        status="PENDING",
    )
    session.add(record)
    session.commit()
    background_tasks.add_task(process_file, record.id, str(stored_path))
    return UploadResponse(id=record.id, status=record.status)


@router.get("/", response_model=list[FileResponse])
def list_files(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[UploadedFile]:
    """Return recent uploads with limit/offset pagination."""
    statement = select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(statement))


@router.get("/{file_id}/", response_model=FileResponse)
def get_file(file_id: str, session: Session = Depends(get_db)) -> UploadedFile:
    """Return upload metadata and current processing status."""
    record = session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return record


@router.get("/{file_id}/measurements/", response_model=FileMeasurementsResponse)
def get_file_measurements(
    file_id: str,
    geometry_type: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> FileMeasurementsResponse:
    """Return aggregate and per-feature measurements for a completed upload."""
    record = session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    if record.status == "FAILED":
        raise HTTPException(status_code=422, detail=record.error_message or "File processing failed.")
    if record.status != "COMPLETED":
        raise HTTPException(status_code=409, detail=f"File processing is {record.status}.")

    statement = select(Feature).where(Feature.file_id == file_id).order_by(Feature.feature_index)
    if geometry_type:
        statement = statement.where(func.lower(Feature.geometry_type) == geometry_type.lower())
    features = list(session.scalars(statement))

    counts: dict[str, int] = {}
    for feature in features:
        counts[feature.geometry_type] = counts.get(feature.geometry_type, 0) + 1
    total_area = sum(feature.area_m2 or 0 for feature in features)
    total_length = sum(feature.length_m or 0 for feature in features)
    summary = FileSummary(
        total_area_m2=round(total_area, 2),
        total_area_hectares=round(total_area / 10_000, 4),
        total_area_acres=round(total_area * ACRES_PER_SQUARE_METER, 4),
        total_length_m=round(total_length, 2),
        total_length_km=round(total_length / 1_000, 4),
        counts_per_geometry_type=counts,
        unsupported_feature_count=sum(not feature.is_supported for feature in features),
        repaired_feature_count=sum(feature.was_repaired for feature in features),
    )
    serialized_features = [
        FeatureResponse(
            feature_id=feature.feature_index,
            geometry_type=feature.geometry_type,
            crs=record.crs or "EPSG:4326",
            properties=feature.properties,
            geometry=feature.geometry,
            measurements=(
                Measurements(
                    area_m2=feature.area_m2,
                    area_hectares=round(feature.area_m2 / 10_000, 4)
                    if feature.area_m2 is not None else None,
                    area_acres=round(feature.area_m2 * ACRES_PER_SQUARE_METER, 4)
                    if feature.area_m2 is not None else None,
                    length_m=feature.length_m,
                    length_km=round(feature.length_m / 1_000, 4)
                    if feature.length_m is not None else None,
                    geodesic_area_m2=feature.geodesic_area_m2,
                    geodesic_length_m=feature.geodesic_length_m,
                    difference_percent=feature.difference_percent,
                )
                if feature.is_supported and feature.geometry_type not in {"Point", "MultiPoint"}
                else None
            ),
            is_supported=feature.is_supported,
            warnings=feature.warnings,
        )
        for feature in features
    ]
    return FileMeasurementsResponse(
        file_id=record.id,
        filename=record.filename,
        crs=record.crs,
        projected_crs_used=record.projected_crs_used,
        summary=summary,
        features=serialized_features,
    )