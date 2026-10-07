"""CRS fallback, wide-feature, and unit conversion tests."""

import pytest
from pyproj import Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform

from app.services.crs import select_projected_crs
from app.services.measurements import measure_geometry


def test_missing_crs_assumes_wgs84_only_for_lon_lat_coordinates() -> None:
    source, projected, warnings = select_projected_crs(Point(77.59, 12.97), None)

    assert source.to_string() == "EPSG:4326"
    assert projected.to_string() == "EPSG:32643"
    assert warnings == ["CRS missing, assumed EPSG:4326"]


def test_missing_crs_rejects_coordinates_outside_lon_lat_bounds() -> None:
    with pytest.raises(ValueError, match="outside longitude/latitude bounds"):
        select_projected_crs(Point(500_000, 1_400_000), None)


def test_wide_feature_uses_lambert_azimuthal_equal_area() -> None:
    wide_polygon = Polygon([(-10, 0), (0, 0), (0, 2), (-10, 2), (-10, 0)])

    _, projected, warnings = select_projected_crs(wide_polygon, "EPSG:4326")

    assert projected.is_projected
    assert "Lambert Azimuthal Equal Area" in projected.coordinate_operation.method_name
    assert any("more than 6 degrees" in warning for warning in warnings)


def test_non_metre_projected_crs_converts_measurements_to_meters() -> None:
    feet_square = Polygon(
        [(600_000, 200_000), (601_000, 200_000), (601_000, 201_000),
         (600_000, 201_000), (600_000, 200_000)]
    )

    result = measure_geometry(feet_square, "EPSG:2263")

    assert result.area_m2 is not None
    assert result.area_m2 == pytest.approx(92_903.41, rel=0.001)
    assert result.projected_crs == "EPSG:2263"


def test_web_mercator_area_is_reprojected_at_latitude_fifty() -> None:
    square_utm = Polygon(
        [(500_000, 5_538_630), (501_000, 5_538_630),
         (501_000, 5_539_630), (500_000, 5_539_630),
         (500_000, 5_538_630)]
    )
    to_web_mercator = Transformer.from_crs("EPSG:32631", "EPSG:3857", always_xy=True)
    square_web_mercator = transform(to_web_mercator.transform, square_utm)

    result = measure_geometry(square_web_mercator, "EPSG:3857")

    assert result.area_m2 is not None
    assert result.geodesic_area_m2 is not None
    assert abs(result.area_m2 - result.geodesic_area_m2) / result.geodesic_area_m2 < 0.005
    assert result.projected_crs == "EPSG:32631"
    assert "Source CRS distorts area (Mercator); reprojected to EPSG:32631 for measurement" in result.warnings