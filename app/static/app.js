// Geo Measure web UI.
// Flow: upload file -> poll status until COMPLETED/FAILED -> show shapes on the map + measurements.

const API = "/api/files/";
const FOOTBALL_PITCH_M2 = 7140; // standard 105 m x 68 m pitch, used to make areas easy to picture
const WALK_M_PER_MIN = 83;      // about 5 km/h, used to make lengths easy to picture

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const shapes = (n) => `${n} ${n === 1 ? "shape" : "shapes"}`;

let currentFileId = null;
let selectedId = null;
const layersById = {};

// ---------- formatting ----------
function num(value, digits = 2) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

// Small areas read better in m², large ones in hectares.
function formatArea(m2) {
  if (m2 == null) return "–";
  return m2 < 10000 ? `${num(m2, 0)} m²` : `${num(m2 / 10000, 2)} ha`;
}

function formatLength(m) {
  if (m == null) return "–";
  return m < 1000 ? `${num(m, 0)} m` : `${num(m / 1000, 2)} km`;
}

function areaHint(m2) {
  const pitches = m2 / FOOTBALL_PITCH_M2;
  const acres = `${num(m2 / 4046.8564224, 2)} acres`;
  if (pitches < 0.5) return `${acres} · smaller than a football pitch`;
  return `${acres} · about ${num(pitches, pitches < 10 ? 1 : 0)} football pitches`;
}

function lengthHint(m) {
  const minutes = m / WALK_M_PER_MIN;
  if (minutes < 1) return "less than a minute's walk";
  if (minutes < 120) return `about a ${num(minutes, 0)}-minute walk`;
  return `about ${num(minutes / 60, 1)} hours on foot`;
}

function shapeKind(type) {
  if (type.includes("Polygon")) return "polygon";
  if (type.includes("LineString")) return "line";
  if (type.includes("Point")) return "point";
  return "other";
}

const KIND_LABEL = {
  polygon: "Area (polygon)",
  line: "Line",
  point: "Location marker",
  other: "Unsupported shape",
};

function featureName(feature) {
  const p = feature.properties || {};
  return p.Name || p.name || p.NAME || `Shape ${feature.feature_id + 1}`;
}

// Extra attributes worth showing (skip empty values and the name we already show).
function extraProps(feature) {
  const skip = new Set(["Name", "name", "NAME", "Description", "description"]);
  return Object.entries(feature.properties || {})
    .filter(([key, value]) => !skip.has(key) && value !== null && value !== "")
    .slice(0, 4);
}

// ---------- API ----------
async function getJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

// Turn API error text into something a non-technical user can act on.
function friendlyError(message) {
  const m = message.toLowerCase();
  if (m.includes("only .kml and .zip")) return "This file type isn't supported. Upload a .kml file, or a .zip that contains a Shapefile (.shp, .shx, .dbf, .prj).";
  if (m.includes("empty")) return "The file is empty. Export it again from your mapping tool and retry.";
  if (m.includes("missing required component")) return `${message} A Shapefile is several files; zip all of them together.`;
  if (m.includes("does not contain a .shp")) return "The zip has no Shapefile inside. Zip the .shp, .shx, .dbf and .prj files together.";
  return message;
}

// ---------- map ----------
const map = L.map("map", { zoomControl: false }).setView([20.5, 78.9], 5);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.control.scale({ position: "bottomleft", imperial: false }).addTo(map);

const basemaps = {
  satellite: L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Imagery © Esri" }
  ),
  streets: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "© OpenStreetMap",
  }),
};
basemaps.satellite.addTo(map);

document.querySelectorAll("[data-basemap]").forEach((button) => {
  button.addEventListener("click", () => {
    Object.values(basemaps).forEach((layer) => map.removeLayer(layer));
    basemaps[button.dataset.basemap].addTo(map);
    document.querySelectorAll("[data-basemap]").forEach((b) => b.classList.toggle("active", b === button));
  });
});

const shapesLayer = L.featureGroup().addTo(map);

const STYLE = {
  polygon: { color: "#2f80c9", weight: 2, fillColor: "#2f80c9", fillOpacity: 0.25 },
  line: { color: "#f2b31d", weight: 4 },
  selected: { color: "#f26b1d", weight: 4, fillColor: "#f26b1d", fillOpacity: 0.3 },
};

// The API returns geometry in the file's own CRS. Leaflet needs lon/lat,
// so shapes from projected files (e.g. metres) are listed but not drawn.
function looksLikeLonLat(geometry) {
  const numbers = JSON.stringify(geometry.coordinates).match(/-?\d+(\.\d+)?(e-?\d+)?/gi) || [];
  return numbers.every((n) => Math.abs(Number(n)) <= 180);
}

function labelFor(feature) {
  const m = feature.measurements;
  if (!m) return null;
  if (m.area_m2 != null) return formatArea(m.area_m2);
  if (m.length_m != null) return formatLength(m.length_m);
  return null;
}

function drawFeatures(features) {
  map.invalidateSize(); // panel sizes may have changed
  shapesLayer.clearLayers();
  Object.keys(layersById).forEach((k) => delete layersById[k]);
  let skipped = 0;

  for (const feature of features) {
    if (!feature.geometry || !looksLikeLonLat(feature.geometry)) {
      if (feature.geometry) skipped++;
      continue;
    }
    const kind = shapeKind(feature.geometry_type);
    const layer = L.geoJSON(feature.geometry, {
      // Points get their own style in pointToLayer, so don't override it here.
      style: kind === "point" ? undefined : STYLE[kind] || STYLE.polygon,
      pointToLayer: (_, latlng) =>
        L.circleMarker(latlng, { radius: 7, color: "#fff", weight: 3, fillColor: "#f26b1d", fillOpacity: 1 }),
    });
    const label = labelFor(feature);
    if (label) {
      layer.bindTooltip(label, { permanent: true, direction: "center", className: "measure-label" });
    } else {
      layer.bindTooltip(featureName(feature), { direction: "top", className: "measure-label" });
    }
    layer.on("click", () => selectFeature(feature.feature_id, { pan: false }));
    layer.addTo(shapesLayer);
    layersById[feature.feature_id] = { layer, kind };
  }

  if (shapesLayer.getLayers().length) {
    map.fitBounds(shapesLayer.getBounds(), { padding: [50, 50], maxZoom: 17 });
  }
  const note = $("map-note");
  note.hidden = skipped === 0;
  note.textContent = `${skipped} shape(s) aren't shown on the map because this file uses a projected coordinate system. Their measurements are still listed on the right.`;
}

function selectFeature(featureId, { pan = true } = {}) {
  // reset previous selection
  if (selectedId != null && layersById[selectedId]) {
    const prev = layersById[selectedId];
    if (prev.kind !== "point") prev.layer.setStyle(STYLE[prev.kind]);
    prev.layer.eachLayer((l) => l.getTooltip()?.getElement()?.classList.remove("selected"));
  }
  document.querySelectorAll(".feature.selected").forEach((el) => el.classList.remove("selected"));

  selectedId = featureId;
  const card = document.querySelector(`.feature[data-id="${featureId}"]`);
  card?.classList.add("selected");
  card?.scrollIntoView({ block: "nearest", behavior: "smooth" });

  const entry = layersById[featureId];
  if (!entry) return;
  if (entry.kind !== "point") entry.layer.setStyle(STYLE.selected);
  entry.layer.eachLayer((l) => l.getTooltip()?.getElement()?.classList.add("selected"));
  if (pan) map.fitBounds(entry.layer.getBounds(), { padding: [80, 80], maxZoom: 17 });
}

// ---------- right panel ----------
function showDetails(info, data) {
  document.querySelector(".app").classList.remove("no-details");
  $("details").hidden = false;
  $("welcome").hidden = true;

  $("d-filename").textContent = info.filename;
  $("d-meta").textContent = `${shapes(info.feature_count)} · ${info.file_type === "kml" ? "KML file" : "Shapefile"}`;
  $("d-status").textContent = info.status === "COMPLETED" ? "Measured" : info.status;
  $("d-status").className = `status ${info.status}`;

  const s = data.summary;
  $("t-area").textContent = s.total_area_m2 ? formatArea(s.total_area_m2) : "–";
  $("t-area-sub").textContent = s.total_area_m2 ? `${num(s.total_area_m2, 0)} m² · ${num(s.total_area_acres, 2)} acres` : "No areas in this file";
  $("t-length").textContent = s.total_length_m ? formatLength(s.total_length_m) : "–";
  $("t-length-sub").textContent = s.total_length_m ? `${num(s.total_length_m, 0)} m` : "No lines in this file";

  const chips = Object.entries(s.counts_per_geometry_type).map(
    ([type, count]) => `<span class="chip">${count} ${type}</span>`
  );
  if (s.repaired_feature_count) chips.push(`<span class="chip warn">${s.repaired_feature_count} repaired</span>`);
  if (s.unsupported_feature_count) chips.push(`<span class="chip warn">${s.unsupported_feature_count} not measurable</span>`);
  $("d-counts").innerHTML = chips.join("");

  $("crs-source").textContent = info.crs === "EPSG:4326" ? "GPS latitude/longitude (EPSG:4326)" : info.crs || "an unknown system";
  $("crs-projected").textContent = info.projected_crs_used || "a local metric grid";

  const list = $("feature-list");
  list.innerHTML = "";
  for (const feature of data.features) list.appendChild(featureCard(feature));

  drawFeatures(data.features);
}

function featureCard(feature) {
  const kind = feature.is_supported ? shapeKind(feature.geometry_type) : "other";
  const m = feature.measurements;
  const li = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "feature";
  button.dataset.id = feature.feature_id;

  let value = "";
  let hint = "";
  if (m?.area_m2 != null) {
    value = formatArea(m.area_m2);
    hint = areaHint(m.area_m2);
  } else if (m?.length_m != null) {
    value = formatLength(m.length_m);
    hint = lengthHint(m.length_m);
  } else if (kind === "point") {
    hint = "A single location. Points have no area or length.";
  } else {
    hint = "This shape type can't be measured.";
  }

  let accuracy = "";
  if (m?.difference_percent != null) {
    const ok = m.difference_percent <= 0.5;
    accuracy = `<div class="f-accuracy ${ok ? "" : "warn"}">${ok ? "✓ Verified" : "Check"}: ${num(m.difference_percent, 2)}% difference from the curved-Earth calculation</div>`;
  }

  button.innerHTML = `
    <span class="swatch ${kind}"></span>
    <div class="f-body">
      <div class="f-name"></div>
      <div class="f-type">${KIND_LABEL[kind]}</div>
      ${value ? `<div class="f-value">${value}</div>` : ""}
      <div class="f-hint">${hint}</div>
      ${accuracy}
      <div class="f-props"></div>
      <ul class="f-warnings"></ul>
    </div>`;

  // Values from the uploaded file are inserted as text, never as HTML.
  button.querySelector(".f-name").textContent = featureName(feature);
  button.querySelector(".f-props").textContent = extraProps(feature)
    .map(([key, value]) => `${key}: ${value}`)
    .join("  ·  ");
  for (const warning of feature.warnings) {
    const item = document.createElement("li");
    item.textContent = `⚠ ${warning}`;
    button.querySelector(".f-warnings").appendChild(item);
  }

  button.addEventListener("click", () => selectFeature(feature.feature_id));
  li.appendChild(button);
  return li;
}

// ---------- upload progress + errors ----------
function setProgress(step, text) {
  const order = ["upload", "process", "done"];
  $("progress").hidden = step == null;
  if (step == null) return;
  document.querySelectorAll(".progress-steps span").forEach((el) => {
    const i = order.indexOf(el.dataset.step);
    const current = order.indexOf(step);
    el.classList.toggle("complete", i < current || step === "done");
    el.classList.toggle("active", i === current && step !== "done");
  });
  $("progress-text").textContent = text || "";
}

function showError(title, message) {
  const box = $("error");
  box.hidden = !title;
  box.innerHTML = "";
  if (!title) return;
  const b = document.createElement("b");
  b.textContent = title;
  box.append(b, document.createTextNode(friendlyError(message)));
}

// ---------- main flow ----------
async function loadFile(fileId) {
  currentFileId = fileId;
  selectedId = null;
  showError(null);
  highlightActiveFile();

  let info;
  for (let attempt = 0; attempt < 60; attempt++) {
    info = await getJson(`${API}${fileId}/`);
    if (info.status === "COMPLETED" || info.status === "FAILED") break;
    setProgress("process", "Reading shapes and measuring them…");
    await sleep(1000);
  }

  if (info.status === "FAILED") {
    setProgress(null);
    showError(`Couldn't measure ${info.filename}`, info.error_message || "Processing failed.");
    hideDetails();
    return;
  }
  if (info.status !== "COMPLETED") {
    showError("Still working", "This file is taking longer than usual. Click it in the list in a moment.");
    return;
  }

  const data = await getJson(`${API}${fileId}/measurements/`);
  showDetails(info, data);
}

function hideDetails() {
  $("details").hidden = true;
  document.querySelector(".app").classList.add("no-details");
  shapesLayer.clearLayers();
  $("map-note").hidden = true;
  map.invalidateSize();
}

async function uploadFile(file) {
  if (!file) return;
  showError(null);
  setProgress("upload", `Uploading ${file.name}…`);
  const form = new FormData();
  form.append("file", file);
  try {
    const upload = await getJson(API, { method: "POST", body: form });
    setProgress("process", upload.duplicate ? "You've uploaded this file before. Loading the saved result…" : "Reading shapes and measuring them…");
    await loadFile(upload.id);
    if (!$("details").hidden) setProgress("done", `${file.name} measured.`);
    await loadFiles();
  } catch (error) {
    setProgress(null);
    showError(`Couldn't upload ${file.name}`, error.message);
  }
}

async function loadFiles() {
  const list = $("file-list");
  try {
    const files = await getJson(`${API}?limit=20`);
    list.innerHTML = "";
    if (!files.length) {
      list.innerHTML = `<li class="file-empty">No files yet. Upload one above to get started.</li>`;
      return;
    }
    for (const f of files) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.id = f.id;
      button.innerHTML = `<span class="file-name"></span><span class="status ${f.status}"></span><span class="file-sub"></span>`;
      button.querySelector(".file-name").textContent = f.filename;
      button.querySelector(".status").textContent = f.status === "COMPLETED" ? "Measured" : f.status.toLowerCase();
      button.querySelector(".file-sub").textContent =
        `${shapes(f.feature_count)} · ${new Date(f.created_at + (f.created_at.endsWith("Z") ? "" : "Z")).toLocaleString()}`;
      button.addEventListener("click", () => {
        setProgress(null);
        loadFile(f.id).catch((e) => showError("Couldn't open file", e.message));
      });
      li.appendChild(button);
      list.appendChild(li);
    }
    highlightActiveFile();
  } catch {
    list.innerHTML = `<li class="file-empty">Couldn't load your files. Is the server running?</li>`;
  }
}

function highlightActiveFile() {
  document.querySelectorAll(".file-list button").forEach((b) => b.classList.toggle("active", b.dataset.id === currentFileId));
}

// ---------- events ----------
const dropzone = $("dropzone");
$("file-input").addEventListener("change", (e) => {
  uploadFile(e.target.files[0]);
  e.target.value = ""; // allow the same file to be chosen again
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
$("refresh-btn").addEventListener("click", loadFiles);
$("help-btn").addEventListener("click", () => {
  $("welcome").hidden = !$("welcome").hidden;
});

document.querySelector(".app").classList.add("no-details");
loadFiles();
