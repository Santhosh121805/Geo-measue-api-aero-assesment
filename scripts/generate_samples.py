"""Generate the checked-in KML and zipped Shapefile examples."""

from __future__ import annotations

from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import geopandas as gpd
from shapely.geometry import Polygon


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "sample_data"


def write_kml(path: Path) -> None:
    """Write a KML sample containing polygon, line, and point features."""
    namespace = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}kml")
    document = ET.SubElement(root, f"{{{namespace}}}Document")
    sample_features = [
        ("Bangalore parcel", "Polygon", "77.590,12.970,0 77.599,12.970,0 77.599,12.979,0 77.590,12.979,0 77.590,12.970,0"),
        ("Bangalore road", "LineString", "77.580,12.970,0 77.581,12.970,0"),
        ("Bangalore marker", "Point", "77.590,12.970,0"),
    ]
    for name, geometry_type, coordinates in sample_features:
        placemark = ET.SubElement(document, f"{{{namespace}}}Placemark")
        ET.SubElement(placemark, f"{{{namespace}}}name").text = name
        geometry = ET.SubElement(placemark, f"{{{namespace}}}{geometry_type}")
        if geometry_type == "Polygon":
            boundary = ET.SubElement(geometry, f"{{{namespace}}}outerBoundaryIs")
            ring = ET.SubElement(boundary, f"{{{namespace}}}LinearRing")
            ET.SubElement(ring, f"{{{namespace}}}coordinates").text = coordinates
        else:
            ET.SubElement(geometry, f"{{{namespace}}}coordinates").text = coordinates
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_shapefile_zip(path: Path) -> None:
    """Write a ZIP containing a one-feature EPSG:4326 Shapefile."""
    parcel = Polygon(
        [(77.590, 12.970), (77.599, 12.970), (77.599, 12.979),
         (77.590, 12.979), (77.590, 12.970)]
    )
    frame = gpd.GeoDataFrame({"name": ["Bangalore parcel"]}, geometry=[parcel], crs="EPSG:4326")
    with tempfile.TemporaryDirectory() as directory:
        shape_path = Path(directory) / "sample_parcel.shp"
        frame.to_file(shape_path, driver="ESRI Shapefile", engine="pyogrio")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for component in Path(directory).iterdir():
                archive.write(component, component.name)


def main() -> None:
    """Generate both sample data files in the repository sample directory."""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    write_kml(SAMPLE_DIR / "sample.kml")
    write_shapefile_zip(SAMPLE_DIR / "sample_shapefile.zip")


if __name__ == "__main__":
    main()