"""Geometry validation, projected measurement, and geodesic cross-checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyproj import CRS, Geod, Transformer
from shapely import make_valid
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.validation import explain_validity

from app.services.crs import select_projected_crs

GEOD = Geod(ellps="WGS84")
ACRES_PER_SQUARE_METER = 1 / 4046.8564224


@dataclass
class MeasurementResult:
    """Measurement values and geometry quality information for a feature."""

    geometry: BaseGeometry
    geometry_type: str
    source_crs: str | None
    projected_crs: str | None
    is_supported: bool
    was_repaired: bool
    warnings: list[str]
    area_m2: float | None = None
    length_m: float | None = None
    geodesic_area_m2: float | None = None
    geodesic_length_m: float | None = None
    difference_percent: float | None = None


def _unsupported(
    geometry: BaseGeometry, source_crs: str | None, warnings: list[str]
) -> MeasurementResult:
    return MeasurementResult(
        geometry=geometry,
        geometry_type=geometry.geom_type if geometry is not None else "Unknown",
        source_crs=source_crs,
        projected_crs=None,
        is_supported=False,
        was_repaired=False,
        warnings=warnings,
    )


def _project(
    geometry: BaseGeometry, source_crs: CRS, target_crs: CRS
) -> BaseGeometry:
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    return transform(transformer.transform, geometry)


def measure_geometry(
    geometry: BaseGeometry | None, source_crs_value: CRS | str | None
) -> MeasurementResult:
    """Measure one geometry, repairing invalid shapes and checking geodesic accuracy."""
    if geometry is None:
        return _unsupported(geometry, str(source_crs_value) if source_crs_value else None,
                            ["Geometry is null or unsupported."])
    warnings: list[str] = []
    if geometry.is_empty:
        return _unsupported(geometry, str(source_crs_value) if source_crs_value else None,
                            ["Geometry is empty or unsupported."])

    was_repaired = False
    if not geometry.is_valid:
        reason = explain_validity(geometry)
        geometry = make_valid(geometry)
        was_repaired = True
        warnings.append(f"Invalid geometry repaired: {reason}")

    geometry_type = geometry.geom_type
    supported_types = {"Polygon", "MultiPolygon", "LineString", "MultiLineString", "Point", "MultiPoint"}
    if geometry_type not in supported_types:
        result = _unsupported(geometry, str(source_crs_value) if source_crs_value else None,
                              [*warnings, f"Unsupported geometry type: {geometry_type}."])
        result.was_repaired = was_repaired
        return result

    try:
        effective_source, projected_crs, crs_warnings = select_projected_crs(
            geometry, source_crs_value
        )
    except ValueError as error:
        raise ValueError(str(error)) from error
    warnings.extend(crs_warnings)
    result = MeasurementResult(
        geometry=geometry,
        geometry_type=geometry_type,
        source_crs=effective_source.to_string(),
        projected_crs=projected_crs.to_string(),
        is_supported=True,
        was_repaired=was_repaired,
        warnings=warnings,
    )
    if geometry_type in {"Point", "MultiPoint"}:
        return result

    projected = _project(geometry, effective_source, projected_crs)
    unit_factor = projected_crs.axis_info[0].unit_conversion_factor
    if unit_factor is None:
        raise ValueError("Projected CRS has no convertible linear units.")
    geographic = _project(geometry, effective_source, CRS.from_epsg(4326))

    if geometry_type in {"Polygon", "MultiPolygon"}:
        projected_area = projected.area * unit_factor**2
        geodesic_area = abs(GEOD.geometry_area_perimeter(geographic)[0])
        result.area_m2 = round(projected_area, 2)
        result.geodesic_area_m2 = round(geodesic_area, 2)
        comparison = (projected_area, geodesic_area)
    else:
        projected_length = projected.length * unit_factor
        geodesic_length = GEOD.geometry_length(geographic)
        result.length_m = round(projected_length, 2)
        result.geodesic_length_m = round(geodesic_length, 2)
        comparison = (projected_length, geodesic_length)

    projected_value, geodesic_value = comparison
    if geodesic_value > 0:
        result.difference_percent = round(
            abs(projected_value - geodesic_value) / geodesic_value * 100, 4
        )
        if result.difference_percent > 0.5:
            warnings.append(
                "Projected and geodesic measurements differ by more than 0.5%."
            )
    return result


def serialize_measurements(result: MeasurementResult) -> dict[str, Any] | None:
    """Build the API measurement payload, or null for point/unsupported features."""
    if not result.is_supported or result.geometry_type in {"Point", "MultiPoint"}:
        return None
    return {
        "area_m2": result.area_m2,
        "area_hectares": round(result.area_m2 / 10_000, 4) if result.area_m2 is not None else None,
        "area_acres": round(result.area_m2 * ACRES_PER_SQUARE_METER, 4) if result.area_m2 is not None else None,
        "length_m": result.length_m,
        "length_km": round(result.length_m / 1_000, 4) if result.length_m is not None else None,
        "geodesic_area_m2": result.geodesic_area_m2,
        "geodesic_length_m": result.geodesic_length_m,
        "difference_percent": result.difference_percent,
    }