"""CRS, measurement, and geometry-quality tests."""

from pyproj import Transformer
from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.ops import transform

from app.services.measurements import measure_geometry, serialize_measurements


def _wgs84(geometry: Polygon | LineString) -> Polygon | LineString:
    converter = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    return transform(converter.transform, geometry)


def test_one_square_kilometer_polygon_matches_geodesic() -> None:
    square = Polygon([(760_000, 1_435_000), (761_000, 1_435_000),
                      (761_000, 1_436_000), (760_000, 1_436_000),
                      (760_000, 1_435_000)])

    result = measure_geometry(_wgs84(square), "EPSG:4326")

    assert result.area_m2 is not None
    assert abs(result.area_m2 - 1_000_000) / 1_000_000 < 0.005
    assert result.difference_percent is not None
    assert result.difference_percent < 0.5


def test_known_line_length_matches_geodesic() -> None:
    line = LineString([(760_000, 1_435_000), (761_000, 1_435_000)])

    result = measure_geometry(_wgs84(line), "EPSG:4326")

    assert result.length_m is not None
    assert abs(result.length_m - 1_000) / 1_000 < 0.005
    assert result.difference_percent is not None
    assert result.difference_percent < 0.5


def test_point_has_no_measurement_and_is_supported() -> None:
    result = measure_geometry(Point(77.59, 12.97), "EPSG:4326")

    assert result.is_supported
    assert serialize_measurements(result) is None


def test_invalid_bow_tie_is_repaired_with_reason() -> None:
    bow_tie = Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)])

    result = measure_geometry(bow_tie, "EPSG:4326")

    assert result.was_repaired
    assert result.is_supported
    assert any("Self-intersection" in warning for warning in result.warnings)


def test_geometry_collection_is_unsupported_without_crashing() -> None:
    result = measure_geometry(GeometryCollection([Point(0, 0)]), "EPSG:4326")

    assert not result.is_supported
    assert result.warnings


def test_projected_input_uses_its_crs_and_metric_units() -> None:
    square = Polygon([(500_000, 1_400_000), (501_000, 1_400_000),
                      (501_000, 1_401_000), (500_000, 1_401_000),
                      (500_000, 1_400_000)])

    result = measure_geometry(square, "EPSG:32643")

    assert result.projected_crs == "EPSG:32643"
    assert result.area_m2 == 1_000_000


def test_two_features_choose_their_own_utm_zones() -> None:
    bangalore = Polygon([(77, 12), (77.1, 12), (77.1, 12.1), (77, 12.1), (77, 12)])
    california = Polygon([(-122, 37), (-121.9, 37), (-121.9, 37.1), (-122, 37.1), (-122, 37)])

    india_result = measure_geometry(bangalore, "EPSG:4326")
    california_result = measure_geometry(california, "EPSG:4326")

    assert india_result.projected_crs == "EPSG:32643"
    assert california_result.projected_crs == "EPSG:32610"


def test_southern_hemisphere_uses_southern_utm_epsg() -> None:
    australia = Polygon([(144, -38), (144.1, -38), (144.1, -37.9), (144, -37.9), (144, -38)])

    result = measure_geometry(australia, "EPSG:4326")

    assert result.projected_crs == "EPSG:32755"