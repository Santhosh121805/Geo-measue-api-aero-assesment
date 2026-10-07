"""Upload, status, and measurement API tests."""

from io import BytesIO
import zipfile
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
from httpx import Response

from app import database
from app.config import settings
from app.models import UploadedFile


def _upload(client: TestClient, filename: str, contents: bytes) -> Response:
    return client.post("/api/files/", files={"file": (filename, contents)})


def test_upload_kml_returns_completed_feature_measurements(client: TestClient, kml_bytes: bytes) -> None:
    response = _upload(client, "sample.kml", kml_bytes)

    assert response.status_code == 202
    file_id = response.json()["id"]
    status = client.get(f"/api/files/{file_id}/")
    assert status.status_code == 200
    assert status.json()["status"] == "COMPLETED"
    assert status.json()["feature_count"] == 3
    assert status.json()["crs"] == "EPSG:4326"

    measurements = client.get(f"/api/files/{file_id}/measurements/")
    assert measurements.status_code == 200
    assert measurements.json()["summary"]["counts_per_geometry_type"] == {
        "Polygon": 1, "LineString": 1, "Point": 1
    }
    assert measurements.json()["features"][0]["measurements"]["area_m2"] > 0
    point = next(feature for feature in measurements.json()["features"] if feature["geometry_type"] == "Point")
    assert point["measurements"] is None


def test_kml_extended_data_and_nested_folder_properties_are_preserved(
    client: TestClient,
) -> None:
    namespace = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}kml")
    document = ET.SubElement(root, f"{{{namespace}}}Document")
    outer = ET.SubElement(document, f"{{{namespace}}}Folder")
    ET.SubElement(outer, f"{{{namespace}}}name").text = "Outer"
    inner = ET.SubElement(outer, f"{{{namespace}}}Folder")
    ET.SubElement(inner, f"{{{namespace}}}name").text = "Inner"
    for name, parcel_id, owner in [("First", "P-1", "Asha"), ("Second", "P-2", "Ravi")]:
        placemark = ET.SubElement(inner, f"{{{namespace}}}Placemark")
        ET.SubElement(placemark, f"{{{namespace}}}name").text = name
        extended_data = ET.SubElement(placemark, f"{{{namespace}}}ExtendedData")
        data = ET.SubElement(extended_data, f"{{{namespace}}}Data", {"name": "parcel_id"})
        ET.SubElement(data, f"{{{namespace}}}value").text = parcel_id
        schema_data = ET.SubElement(extended_data, f"{{{namespace}}}SchemaData", {"schemaUrl": "#parcel"})
        ET.SubElement(schema_data, f"{{{namespace}}}SimpleData", {"name": "owner"}).text = owner
        polygon = ET.SubElement(placemark, f"{{{namespace}}}Polygon")
        boundary = ET.SubElement(polygon, f"{{{namespace}}}outerBoundaryIs")
        ring = ET.SubElement(boundary, f"{{{namespace}}}LinearRing")
        ET.SubElement(ring, f"{{{namespace}}}coordinates").text = (
            "77.590,12.970 77.591,12.970 77.591,12.971 "
            "77.590,12.971 77.590,12.970"
        )
    contents = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    response = _upload(client, "extended-data.kml", contents)
    measurements = client.get(f"/api/files/{response.json()['id']}/measurements/").json()
    features = measurements["features"]

    assert [feature["properties"]["parcel_id"] for feature in features] == ["P-1", "P-2"]
    assert [feature["properties"]["owner"] for feature in features] == ["Asha", "Ravi"]
    assert [feature["properties"]["folder"] for feature in features] == ["Inner", "Inner"]


def test_kml_metadata_count_mismatch_warns_and_skips_merge(client: TestClient) -> None:
    namespace = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}kml")
    document = ET.SubElement(root, f"{{{namespace}}}Document")
    placemark = ET.SubElement(document, f"{{{namespace}}}Placemark")
    extended_data = ET.SubElement(placemark, f"{{{namespace}}}ExtendedData")
    data = ET.SubElement(extended_data, f"{{{namespace}}}Data", {"name": "should_not_merge"})
    ET.SubElement(data, f"{{{namespace}}}value").text = "value"
    polygon = ET.SubElement(placemark, f"{{{namespace}}}Polygon")
    boundary = ET.SubElement(polygon, f"{{{namespace}}}outerBoundaryIs")
    ring = ET.SubElement(boundary, f"{{{namespace}}}LinearRing")
    ET.SubElement(ring, f"{{{namespace}}}coordinates").text = (
        "77.590,12.970 77.591,12.970 77.591,12.971 "
        "77.590,12.971 77.590,12.970"
    )
    no_geometry = ET.SubElement(document, f"{{{namespace}}}Placemark")
    extra_data = ET.SubElement(no_geometry, f"{{{namespace}}}ExtendedData")
    extra = ET.SubElement(extra_data, f"{{{namespace}}}Data", {"name": "extra"})
    ET.SubElement(extra, f"{{{namespace}}}value").text = "not a feature"

    response = _upload(
        client, "mismatched.kml", ET.tostring(root, encoding="utf-8", xml_declaration=True)
    )
    detail = client.get(f"/api/files/{response.json()['id']}/measurements/").json()

    assert len(detail["features"]) == 1
    assert "should_not_merge" not in detail["features"][0]["properties"]
    assert any(
        "Placemark count does not match" in warning
        for warning in detail["features"][0]["warnings"]
    )


def test_upload_shapefile_zip_completes(client: TestClient, shapefile_zip_bytes: bytes) -> None:
    response = _upload(client, "sample.zip", shapefile_zip_bytes)

    assert response.status_code == 202
    status = client.get(f"/api/files/{response.json()['id']}/")
    assert status.json()["status"] == "COMPLETED"
    assert status.json()["feature_count"] == 1


def test_missing_shapefile_projection_assumes_wgs84_with_warning(
    client: TestClient, shapefile_zip_bytes: bytes
) -> None:
    source = BytesIO(shapefile_zip_bytes)
    output = BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(output, "w") as modified:
        for member in original.infolist():
            if not member.filename.lower().endswith(".prj"):
                modified.writestr(member.filename, original.read(member.filename))

    response = _upload(client, "no-projection.zip", output.getvalue())
    detail = client.get(f"/api/files/{response.json()['id']}/measurements/").json()

    assert detail["crs"] == "EPSG:4326"
    assert "CRS missing, assumed EPSG:4326" in detail["features"][0]["warnings"]


def test_duplicate_upload_returns_existing_id(client: TestClient, kml_bytes: bytes) -> None:
    original = _upload(client, "first.kml", kml_bytes)
    duplicate = _upload(client, "renamed.kml", kml_bytes)

    assert original.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == original.json()["id"]
    assert duplicate.json()["duplicate"] is True


def test_rejects_wrong_extension_empty_and_corrupt_zip(client: TestClient) -> None:
    wrong_type = _upload(client, "notes.txt", b"text")
    empty = _upload(client, "empty.kml", b"")
    corrupt = _upload(client, "broken.zip", b"not a zip")

    assert wrong_type.status_code == 400
    assert isinstance(wrong_type.json()["detail"], str)
    assert empty.status_code == 400
    assert corrupt.status_code == 400


def test_upload_size_limit_is_configurable(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_file_size_bytes", 4)

    response = _upload(client, "large.kml", b"12345")

    assert response.status_code == 413
    assert response.json() == {"detail": "File exceeds the upload limit of 4 bytes."}


def test_zip_without_shapefile_is_marked_failed(client: TestClient) -> None:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("readme.txt", "no shape here")

    response = _upload(client, "missing-shape.zip", archive.getvalue())
    status = client.get(f"/api/files/{response.json()['id']}/")

    assert response.status_code == 202
    assert status.json()["status"] == "FAILED"
    assert ".shp" in status.json()["error_message"]
    measurements = client.get(f"/api/files/{response.json()['id']}/measurements/")
    assert measurements.status_code == 422


def test_zip_slip_is_rejected(client: TestClient) -> None:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../evil.shp", b"unsafe")

    response = _upload(client, "unsafe.zip", archive.getvalue())

    assert response.status_code == 400
    assert "unsafe" in response.json()["detail"].lower()


def test_unknown_file_is_404_and_pending_measurements_are_409(client: TestClient) -> None:
    assert client.get("/api/files/unknown/").status_code == 404
    with database.SessionLocal() as session:
        record = UploadedFile(filename="pending.kml", file_type="kml", file_hash="0" * 64)
        session.add(record)
        session.commit()
        file_id = record.id

    response = client.get(f"/api/files/{file_id}/measurements/")

    assert response.status_code == 409
    assert response.json() == {"detail": "File processing is PENDING."}


def test_files_endpoint_paginates_and_health_is_available(client: TestClient, kml_bytes: bytes) -> None:
    upload = _upload(client, "one.kml", kml_bytes)

    assert client.get("/health").json() == {"status": "ok"}
    listing = client.get("/api/files/?limit=1&offset=0")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    filtered = client.get(f"/api/files/{upload.json()['id']}/measurements/?geometry_type=Polygon")
    assert len(filtered.json()["features"]) == 1
    invalid_limit = client.get("/api/files/?limit=0")
    assert invalid_limit.status_code == 422
    assert isinstance(invalid_limit.json()["detail"], str)