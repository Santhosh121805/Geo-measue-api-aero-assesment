"""Programmatic geospatial fixtures and isolated API test setup."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from io import BytesIO
from pathlib import Path
import tempfile
import zipfile
import xml.etree.ElementTree as ET

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from app import database
from app.config import settings
from app.main import app


def make_kml_bytes() -> bytes:
    """Create KML with a polygon, line, and point around Bangalore."""
    namespace = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}kml")
    document = ET.SubElement(root, f"{{{namespace}}}Document")
    geometries = [
        ("Parcel", "Polygon", "77.590,12.970,0 77.599,12.970,0 77.599,12.979,0 77.590,12.979,0 77.590,12.970,0"),
        ("Road", "LineString", "77.580,12.970,0 77.581,12.970,0"),
        ("Marker", "Point", "77.590,12.970,0"),
    ]
    for name, geometry_type, coordinates in geometries:
        placemark = ET.SubElement(document, f"{{{namespace}}}Placemark")
        ET.SubElement(placemark, f"{{{namespace}}}name").text = name
        geometry = ET.SubElement(placemark, f"{{{namespace}}}{geometry_type}")
        if geometry_type == "Polygon":
            boundary = ET.SubElement(geometry, f"{{{namespace}}}outerBoundaryIs")
            ring = ET.SubElement(boundary, f"{{{namespace}}}LinearRing")
            ET.SubElement(ring, f"{{{namespace}}}coordinates").text = coordinates
        else:
            ET.SubElement(geometry, f"{{{namespace}}}coordinates").text = coordinates
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def make_shapefile_zip_bytes() -> bytes:
    """Create an in-memory ZIP containing one EPSG:4326 polygon Shapefile."""
    polygon = Polygon(
        [(77.590, 12.970), (77.599, 12.970), (77.599, 12.979),
         (77.590, 12.979), (77.590, 12.970)]
    )
    frame = gpd.GeoDataFrame({"name": ["Bangalore parcel"]}, geometry=[polygon], crs="EPSG:4326")
    with tempfile.TemporaryDirectory() as directory:
        shape_path = Path(directory) / "parcel.shp"
        frame.to_file(shape_path, driver="ESRI Shapefile", engine="pyogrio")
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for component in Path(directory).iterdir():
                archive.write(component, component.name)
    return output.getvalue()


@pytest.fixture(autouse=True)
def isolated_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Give each test its own SQLite database and upload directory."""
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    settings.upload_dir.mkdir(parents=True)
    database.configure_database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.Base.metadata.create_all(bind=database.engine)
    yield
    database.engine.dispose()
    database.configure_database(settings.database_url)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Provide a TestClient whose background tasks complete before requests return."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def kml_bytes() -> bytes:
    """KML containing one polygon, one line, and one point."""
    return make_kml_bytes()


@pytest.fixture
def shapefile_zip_bytes() -> bytes:
    """A generated polygon Shapefile ZIP."""
    return make_shapefile_zip_bytes()