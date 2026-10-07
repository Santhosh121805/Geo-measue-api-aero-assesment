# Geo Measure API

Upload a map file (**KML** or a zipped **Shapefile**) and get the **area** of every polygon and the **length** of every line, in metres, hectares and acres.

Built with **FastAPI, GeoPandas, Shapely, pyproj and SQLite**, with a simple web UI that shows the results on a map.

![Homepage](docs/homepage.png)
![Workspace](docs/workspace.png)

---

## Features

- Upload `.kml` or `.zip` (Shapefile) files
- Area for polygons, length for lines; points are listed without measurements
- **Correct CRS handling:** shapes are converted from GPS degrees to metres (UTM) before measuring
- **Accuracy check:** every result is compared with a curved-Earth (geodesic) calculation
- Broken shapes (e.g. self-crossing polygons) are repaired and flagged
- Unsupported or invalid files return a clear error, never a crash
- Web UI with satellite map, measurement labels and upload history
- 25 automated tests, plus Docker support

---

## Setup

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1    Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

| Open | What it is |
|---|---|
| http://127.0.0.1:8000/ | Web UI (upload a file here) |
| http://127.0.0.1:8000/docs | Swagger API docs |

**Run tests:** `pytest`

**Docker:**
```bash
docker build -t geo-measure-api .
docker run -p 8000:8000 geo-measure-api
```

Sample files to try are in `sample_data/`.

---

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/files/` | Upload a `.kml` or `.zip` file |
| `GET` | `/api/files/{id}/` | File info and processing status |
| `GET` | `/api/files/{id}/measurements/` | Measurements for every feature |
| `GET` | `/api/files/` | List uploaded files |

### 1. Upload
```bash
curl -F "file=@sample_data/sample.kml" http://127.0.0.1:8000/api/files/
```
```json
{ "id": "9f368b08-bcf9-4182-98b5-cd64e9c9ad95", "status": "PENDING", "duplicate": false }
```
The file is processed in the background. Uploading the same file again returns the saved result (`"duplicate": true`).

### 2. File info
```bash
curl http://127.0.0.1:8000/api/files/{id}/
```
```json
{
  "id": "9f368b08-bcf9-4182-98b5-cd64e9c9ad95",
  "filename": "sample.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "projected_crs_used": "EPSG:32643",
  "status": "COMPLETED"
}
```
Status goes `PENDING → PROCESSING → COMPLETED` (or `FAILED` with an `error_message`).

### 3. Measurements
```bash
curl http://127.0.0.1:8000/api/files/{id}/measurements/
```
```json
{
  "summary": {
    "total_area_m2": 973365.15,
    "total_area_hectares": 97.3365,
    "total_length_m": 108.56,
    "counts_per_geometry_type": { "Polygon": 1, "LineString": 1, "Point": 1 }
  },
  "features": [
    {
      "feature_id": 0,
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "properties": { "Name": "Bangalore parcel" },
      "geometry": { "type": "Polygon", "coordinates": [[[77.59, 12.97], [77.599, 12.97], "..."]] },
      "measurements": {
        "area_m2": 973365.15,
        "area_hectares": 97.3365,
        "area_acres": 240.5238,
        "geodesic_area_m2": 972236.68,
        "difference_percent": 0.1161
      },
      "warnings": []
    }
  ]
}
```
(Shortened. Lines return `length_m`, and points return `measurements: null`.)

### Errors
All errors return JSON: `{ "detail": "message" }`

| Case | Response |
|---|---|
| Wrong file type / empty file / unsafe zip | `400` |
| File too large (over 50 MB) | `413` |
| Unknown file id | `404` |
| Measurements requested while still processing | `409` |
| File could not be read (e.g. zip missing `.dbf`) | Status `FAILED` with a reason |

---

## Architecture

![Architecture](docs/architecture.png)

### Project structure
```
app/
├── main.py              # FastAPI app, serves the web UI
├── routes/files.py      # API endpoints
├── services/
│   ├── file_reader.py   # Safely reads KML / unzips Shapefiles
│   ├── crs.py           # Picks the right metric CRS for each shape
│   ├── measurements.py  # Area, length, geodesic check, shape repair
│   └── processor.py     # Runs the full pipeline in the background
├── models.py            # Database tables (files, features)
├── schemas.py           # API response formats
└── static/              # Web UI (HTML, CSS, JS)
tests/                   # pytest tests
sample_data/             # Example KML and Shapefile
```

### CRS handling
Map files usually store coordinates in **EPSG:4326 (latitude/longitude in degrees)**. Degrees are not a fixed distance: 1° of longitude is about 111 km at the equator and 0 km at the poles. So **measuring directly in degrees gives wrong results.**

My approach:
- **Each shape is converted to the UTM zone at its centre** (EPSG:326xx north, 327xx south). UTM uses metres and is very accurate within its zone. Doing this *per shape* means a file covering several regions is still measured correctly.
- **Very large shapes** (wider than one UTM zone) use a local equal-area projection instead.
- **Files already in metres** (e.g. UTM) are measured directly.
- **Web Mercator (EPSG:3857)** is reprojected first, because it inflates areas.
- **Files with no CRS** are assumed to be EPSG:4326 only if the coordinates look like valid lat/long. A warning is added.

**Accuracy check:** every result is also calculated on the WGS84 ellipsoid (`pyproj.Geod`). The difference is usually under 0.2%.

---

## Design Decisions

| Decision | Why | Alternative considered |
|---|---|---|
| **FastAPI** | Lightweight, fast, built-in validation and Swagger docs | Django: more setup than this API needs |
| **SQLite** | Zero setup, easy to run and test | PostGIS: better for spatial queries at scale |
| **Background tasks** | Upload returns instantly; big files don't block | Celery + Redis: more reliable but heavier |
| **UTM per feature + geodesic check** | Accurate metric results, verified a second way | Single CRS for whole file: inaccurate across regions |
| **Repair invalid shapes** | Real survey data is messy; still give a useful result | Rejecting the file: less helpful |
| **File hash deduplication** | Same file isn't processed twice | Always reprocess: wasted work |

---

## Testing

`pytest` runs 25 tests with generated sample data. They cover:
- A 1 km × 1 km square measures ≈ 1,000,000 m²
- Lines of known length
- Multiple UTM zones and the southern hemisphere
- Web Mercator input
- Self-crossing shape repair
- Missing CRS
- Invalid, empty and unsafe files
- All API status codes

---

## Learnings

- Why measuring in lat/long degrees is wrong, and how map projections fix it
- Real files are messy: invalid shapes, missing `.prj` files and 3D coordinates need handling
- Zip uploads must be checked for safety (zip-slip, zip bombs) *before* extracting
- A background task needs its own database session

## Future Scope

- **Change detection:** compare two surveys of the same site to find area gained or lost (e.g. encroachment)
- **PostGIS** for spatial queries ("features within 1 km")
- **Celery + Redis** workers for large files
- **Volume calculation** from elevation data (DEM), useful for mining stockpiles
- Support for GeoJSON and GeoPackage
- Authentication and rate limiting