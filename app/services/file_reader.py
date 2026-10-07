"""Safe upload validation and GeoDataFrame loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import stat
import tempfile
import zipfile
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import force_2d

from app.config import Settings, settings

KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
KML_NAMESPACES = {"k": KML_NAMESPACE}


@dataclass
class ReadResult:
    """Loaded source features and file-level warnings."""

    frame: gpd.GeoDataFrame
    warnings: list[str]


def _validated_zip_members(archive: zipfile.ZipFile, config: Settings) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > config.max_archive_entries:
        raise ValueError("ZIP archive contains too many entries.")

    total_size = 0
    seen_names: set[str] = set()
    for member in members:
        normalized = member.filename.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or ".." in path.parts
            or (path.parts and ":" in path.parts[0])
            or stat.S_ISLNK(member.external_attr >> 16)
        ):
            raise ValueError("ZIP archive contains an unsafe path.")
        normalized_name = path.as_posix().casefold()
        if normalized_name in seen_names:
            raise ValueError("ZIP archive contains duplicate file paths.")
        seen_names.add(normalized_name)
        total_size += member.file_size
        if total_size > config.max_archive_size_bytes:
            raise ValueError("ZIP archive expands beyond the allowed size.")
    return members


def validate_zip_safety(path: Path, config: Settings = settings) -> None:
    """Reject corrupt or unsafe archives before scheduling background work."""
    try:
        with zipfile.ZipFile(path) as archive:
            _validated_zip_members(archive, config)
    except (zipfile.BadZipFile, OSError) as error:
        raise ValueError("The uploaded ZIP file is corrupt or invalid.") from error


def _placemark_metadata(path: Path) -> list[dict[str, str]]:
    root = ET.parse(path).getroot()
    placemarks: list[dict[str, str]] = []
    folder_tag = f"{{{KML_NAMESPACE}}}Folder"
    placemark_tag = f"{{{KML_NAMESPACE}}}Placemark"

    def visit(element: ET.Element, containing_folder: str | None = None) -> None:
        if element.tag == folder_tag:
            containing_folder = element.findtext(f"{{{KML_NAMESPACE}}}name") or containing_folder
        if element.tag == placemark_tag:
            properties: dict[str, str] = {}
            for data in element.findall("k:ExtendedData/k:Data", KML_NAMESPACES):
                name = data.get("name")
                value = data.findtext("k:value", namespaces=KML_NAMESPACES)
                if name and value is not None:
                    properties[name] = value
            for simple_data in element.findall(
                "k:ExtendedData/k:SchemaData/k:SimpleData", KML_NAMESPACES
            ):
                name = simple_data.get("name")
                if name and simple_data.text is not None:
                    properties[name] = simple_data.text
            if containing_folder:
                properties["folder"] = containing_folder
            placemarks.append(properties)
            return
        for child in element:
            visit(child, containing_folder)

    visit(root)
    return placemarks


def _read_kml(path: Path) -> ReadResult:
    try:
        layers = pyogrio.list_layers(path)
        if len(layers) == 0:
            raise ValueError("KML file contains no readable layers.")
        frames = [
            gpd.read_file(path, layer=str(layer[0]), engine="pyogrio")
            for layer in layers
        ]
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("KML file could not be read.") from error

    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise ValueError("KML file contains no features.")
    geometry_name = frames[0].geometry.name
    source_crs = frames[0].crs
    aligned_frames = [
        frame.to_crs(source_crs) if source_crs and frame.crs and frame.crs != source_crs else frame
        for frame in frames
    ]
    combined = gpd.GeoDataFrame(
        pd.concat(aligned_frames, ignore_index=True), geometry=geometry_name, crs=source_crs
    )
    combined.geometry = force_2d(combined.geometry.array)
    warnings: list[str] = []
    try:
        placemarks = _placemark_metadata(path)
    except (ET.ParseError, OSError) as error:
        warnings.append(f"KML Placemark metadata could not be read; ExtendedData was skipped: {error}")
        return ReadResult(frame=combined, warnings=warnings)

    if len(placemarks) != len(combined):
        warnings.append(
            "KML Placemark count does not match GeoDataFrame feature count; "
            "ExtendedData was skipped."
        )
        return ReadResult(frame=combined, warnings=warnings)

    metadata_keys = {key for properties in placemarks for key in properties}
    for key in metadata_keys:
        combined[key] = [properties.get(key) for properties in placemarks]
    return ReadResult(frame=combined, warnings=warnings)


def _read_shapefile(path: Path, config: Settings) -> ReadResult:
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            members = _validated_zip_members(archive, config)
            shapefiles = [member for member in members if member.filename.lower().endswith(".shp")]
            if len(shapefiles) != 1:
                if not shapefiles:
                    raise ValueError("ZIP archive does not contain a .shp file.")
                raise ValueError("ZIP archive must contain exactly one .shp file.")

            shape_member = shapefiles[0]
            shape_path = PurePosixPath(shape_member.filename.replace("\\", "/"))
            available = {
                PurePosixPath(member.filename.replace("\\", "/")).as_posix().casefold()
                for member in members
                if not member.is_dir()
            }
            missing = [
                extension
                for extension in (".shx", ".dbf")
                if shape_path.with_suffix(extension).as_posix().casefold() not in available
            ]
            if missing:
                raise ValueError(
                    "Shapefile is missing required component(s): " + ", ".join(missing)
                )
            projection_path = shape_path.with_suffix(".prj").as_posix().casefold()
            if projection_path not in available:
                warnings.append("CRS missing; coordinates will be checked before assuming EPSG:4326.")

            with tempfile.TemporaryDirectory(prefix="geo-extract-", dir=config.upload_dir) as folder:
                archive.extractall(folder)
                extracted_path = Path(folder).joinpath(*shape_path.parts)
                frame = gpd.read_file(extracted_path, engine="pyogrio")
                frame.geometry = force_2d(frame.geometry.array)
    except ValueError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        raise ValueError("Shapefile ZIP is corrupt or could not be extracted.") from error
    except Exception as error:
        raise ValueError("Shapefile contents could not be read.") from error
    return ReadResult(frame=frame, warnings=warnings)


def read_uploaded_file(
    path: Path, file_type: str, config: Settings = settings
) -> ReadResult:
    """Read KML layers or one zipped Shapefile into a two-dimensional frame."""
    if file_type == "kml":
        return _read_kml(path)
    if file_type == "shapefile":
        return _read_shapefile(path, config)
    raise ValueError("Unsupported geospatial file type.")