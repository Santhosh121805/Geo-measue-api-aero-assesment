// Simple frontend for the Geo Measure API.
// Flow: upload file -> poll status until COMPLETED/FAILED -> show measurements.

const API = "/api/files/";

// ---------- small helpers ----------
const $ = (id) => document.getElementById(id);

function fmt(value, digits = 2) {
  return value == null ? "–" : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function showMessage(text, isError = false) {
  const box = $("message");
  box.textContent = text;
  box.className = "message" + (isError ? " error" : "");
  box.hidden = !text;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // Our API always sends errors as {"detail": "..."}
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return body;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------- map ----------
const map = L.map("map").setView([20.5, 78.9], 4); // India
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap",
}).addTo(map);
let shapesLayer = L.featureGroup().addTo(map);
const layersById = {};

// Geometries are returned in the file's own CRS. Leaflet needs lon/lat,
// so we only draw them when the coordinates look like lon/lat.
function looksLikeLonLat(geometry) {
  const text = JSON.stringify(geometry.coordinates);
  const numbers = text.match(/-?\d+(\.\d+)?/g) || [];
  return numbers.every((n) => Math.abs(Number(n)) <= 180);
}

function drawFeatures(features) {
  // The map was created while its section was hidden, so tell Leaflet
  // to re-measure its size now that it is visible.
  map.invalidateSize();
  shapesLayer.clearLayers();
  let skipped = 0;

  for (const feature of features) {
    if (!feature.geometry || !looksLikeLonLat(feature.geometry)) {
      skipped++;
      continue;
    }
    const layer = L.geoJSON(feature.geometry, {
      style: { color: "#1f7a5a", weight: 2, fillOpacity: 0.25 },
      pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 6, color: "#1f7a5a" }),
    });
    layer.bindPopup(popupHtml(feature));
    layer.on("click", () => highlightRow(feature.feature_id));
    layer.addTo(shapesLayer);
    layersById[feature.feature_id] = layer;
  }

  if (shapesLayer.getLayers().length) {
    map.fitBounds(shapesLayer.getBounds(), { padding: [30, 30], maxZoom: 17 });
  }
  $("map-note").hidden = skipped === 0;
  $("map-note").textContent =
    `${skipped} feature(s) not drawn: their coordinates are not in longitude/latitude (projected CRS).`;
}

function popupHtml(feature) {
  const m = feature.measurements || {};
  const lines = [`<b>${featureName(feature)}</b>`, feature.geometry_type];
  if (m.area_m2 != null) lines.push(`Area: ${fmt(m.area_m2)} m² (${fmt(m.area_hectares, 4)} ha)`);
  if (m.length_m != null) lines.push(`Length: ${fmt(m.length_m)} m`);
  return lines.join("<br>");
}

// ---------- results ----------
function featureName(feature) {
  const p = feature.properties || {};
  return p.Name || p.name || p.NAME || `Feature ${feature.feature_id}`;
}

function showFileInfo(info) {
  $("result").hidden = false;
  $("info-filename").textContent = info.filename;
  $("info-status").textContent = info.status;
  $("info-status").className = "badge " + info.status;
  $("info-crs").textContent = info.crs || "–";
  $("info-projected").textContent = info.projected_crs_used || "–";
}

function showMeasurements(data) {
  const s = data.summary;
  $("stat-features").textContent = data.features.length;
  $("stat-area").textContent = `${fmt(s.total_area_hectares, 2)} ha`;
  $("stat-area-sub").textContent = `${fmt(s.total_area_m2)} m² · ${fmt(s.total_area_acres, 2)} acres`;
  $("stat-length").textContent = `${fmt(s.total_length_km, 3)} km`;
  $("stat-length-sub").textContent = `${fmt(s.total_length_m)} m`;
  $("stat-issues").textContent = `${s.repaired_feature_count} / ${s.unsupported_feature_count}`;

  $("feature-rows").innerHTML = "";
  for (const feature of data.features) {
    const m = feature.measurements || {};
    const row = document.createElement("tr");
    row.id = `row-${feature.feature_id}`;
    row.innerHTML = `
      <td>${feature.feature_id}</td>
      <td></td>
      <td>${feature.geometry_type}</td>
      <td>${m.area_m2 != null ? `${fmt(m.area_m2)} m²<br><small>${fmt(m.area_hectares, 4)} ha</small>` : "–"}</td>
      <td>${m.length_m != null ? `${fmt(m.length_m)} m<br><small>${fmt(m.length_km, 3)} km</small>` : "–"}</td>
      <td>${m.difference_percent != null ? `${fmt(m.difference_percent, 3)}%` : "–"}</td>
      <td class="warn"></td>`;
    // textContent (not innerHTML) for values that come from the uploaded file
    row.children[1].textContent = featureName(feature);
    row.children[6].textContent = feature.warnings.join(" · ");
    row.addEventListener("click", () => zoomTo(feature.feature_id));
    $("feature-rows").appendChild(row);
  }

  drawFeatures(data.features);
}

function highlightRow(featureId) {
  document.querySelectorAll("tr.selected").forEach((r) => r.classList.remove("selected"));
  $(`row-${featureId}`)?.classList.add("selected");
}

function zoomTo(featureId) {
  highlightRow(featureId);
  const layer = layersById[featureId];
  if (!layer) return;
  map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 17 });
  layer.openPopup();
}

// ---------- main flow ----------
async function loadFile(fileId) {
  // Poll until processing finishes (background task on the server).
  let info;
  for (let attempt = 0; attempt < 60; attempt++) {
    info = await getJson(`${API}${fileId}/`);
    showFileInfo(info);
    if (info.status === "COMPLETED" || info.status === "FAILED") break;
    await sleep(1000);
  }

  if (info.status === "FAILED") {
    showMessage(`Processing failed: ${info.error_message}`, true);
    clearResults();
    return;
  }
  if (info.status !== "COMPLETED") {
    showMessage("Still processing… try again in a moment.", true);
    return;
  }

  showMessage("");
  showMeasurements(await getJson(`${API}${fileId}/measurements/`));
  loadHistory();
}

function clearResults() {
  $("feature-rows").innerHTML = "";
  shapesLayer.clearLayers();
  ["stat-features", "stat-area", "stat-length", "stat-issues"].forEach((id) => ($(id).textContent = "–"));
  ["stat-area-sub", "stat-length-sub"].forEach((id) => ($(id).textContent = ""));
}

async function uploadFile(file) {
  if (!file) return;
  showMessage(`Uploading ${file.name}…`);
  const form = new FormData();
  form.append("file", file);
  try {
    const upload = await getJson(API, { method: "POST", body: form });
    if (upload.duplicate) showMessage("This file was uploaded before — showing the saved result.");
    else showMessage("Processing…");
    await loadFile(upload.id);
  } catch (error) {
    showMessage(error.message, true);
  }
}

async function loadHistory() {
  try {
    const files = await getJson(`${API}?limit=10`);
    const list = $("history");
    list.innerHTML = files.length ? "" : "<li>No uploads yet.</li>";
    for (const f of files) {
      const item = document.createElement("li");
      item.innerHTML = `<span></span><span class="badge ${f.status}">${f.status}</span>`;
      item.firstChild.textContent = `${f.filename} · ${f.feature_count} features`;
      item.addEventListener("click", () => loadFile(f.id).catch((e) => showMessage(e.message, true)));
      list.appendChild(item);
    }
  } catch {
    $("history").innerHTML = "<li>Could not load history.</li>";
  }
}

// ---------- upload box events ----------
const dropzone = $("dropzone");
$("file-input").addEventListener("change", (e) => {
  uploadFile(e.target.files[0]);
  e.target.value = ""; // allow uploading the same file again
});
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragging");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragging");
  uploadFile(e.dataTransfer.files[0]);
});

loadHistory();
