"""CRS inference and per-feature projected CRS selection."""

from __future__ import annotations

from math import floor, isfinite

from pyproj import CRS, Transformer
from shapely import get_coordinates
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


def _assume_wgs84_if_valid(geometry: BaseGeometry) -> tuple[CRS, list[str]]:
    coordinates = get_coordinates(geometry)
    if not len(coordinates) or not all(isfinite(float(value)) for value in coordinates.flat):
        raise ValueError("CRS is missing and coordinates cannot be validated as longitude/latitude.")
    if any(
        longitude < -180 or longitude > 180 or latitude < -90 or latitude > 90
        for longitude, latitude in coordinates[:, :2]
    ):
        raise ValueError("CRS is missing and coordinates are outside longitude/latitude bounds.")
    return CRS.from_epsg(4326), ["CRS missing, assumed EPSG:4326"]


def _to_wgs84(geometry: BaseGeometry, source_crs: CRS) -> BaseGeometry:
    if source_crs.equals(CRS.from_epsg(4326)):
        return geometry
    transformer = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
    return transform(transformer.transform, geometry)


def _laea_crs(longitude: float, latitude: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=laea +lat_0={latitude:.8f} +lon_0={longitude:.8f} "
        "+datum=WGS84 +units=m +no_defs +type=crs"
    )


def _is_distorting_mercator(source_crs: CRS) -> bool:
    operation = source_crs.coordinate_operation
    method_name = operation.method_name.casefold() if operation else ""
    return "mercator" in method_name and "transverse mercator" not in method_name


def select_projected_crs(
    geometry: BaseGeometry, source_crs_value: CRS | str | None
) -> tuple[CRS, CRS, list[str]]:
    """Return effective source CRS, a suitable projected CRS, and warnings."""
    warnings: list[str] = []
    try:
        source_crs = CRS.from_user_input(source_crs_value) if source_crs_value else None
    except Exception as error:
        raise ValueError("Source CRS is invalid.") from error
    if source_crs is None:
        source_crs, warnings = _assume_wgs84_if_valid(geometry)

    mercator_source = source_crs.is_projected and _is_distorting_mercator(source_crs)
    if source_crs.is_projected and not mercator_source:
        return source_crs, source_crs, warnings
    if not source_crs.is_geographic and not mercator_source:
        raise ValueError("Source CRS must be geographic or projected.")

    geographic = _to_wgs84(geometry, source_crs)
    min_x, min_y, max_x, max_y = geographic.bounds
    center = geographic.centroid
    longitude = ((center.x + 180) % 360) - 180
    latitude = center.y
    longitude_span = max_x - min_x

    if longitude_span > 6 or latitude < -80 or latitude > 84:
        projected_crs = _laea_crs(longitude, latitude)
        if mercator_source:
            warnings.append(
                "Source CRS distorts area (Mercator); reprojected to "
                f"{projected_crs.to_string()} for measurement"
            )
        if longitude_span > 6:
            warnings.append(
                "Feature spans more than 6 degrees of longitude; Lambert Azimuthal Equal Area used."
            )
        else:
            warnings.append("Polar latitude; Lambert Azimuthal Equal Area used instead of UTM.")
        return source_crs, projected_crs, warnings

    zone = min(60, max(1, floor((longitude + 180) / 6) + 1))
    epsg = (32600 if latitude >= 0 else 32700) + zone
    projected_crs = CRS.from_epsg(epsg)
    if mercator_source:
        warnings.append(
            "Source CRS distorts area (Mercator); reprojected to "
            f"{projected_crs.to_string()} for measurement"
        )
    return source_crs, projected_crs, warnings