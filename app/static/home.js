// Homepage: upload a file, wait until it is measured, then open it in the workspace (/app?file=<id>).

const API = "/api/files/";
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function getJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

// Same wording as the workspace, so errors read the same everywhere.
function friendlyError(message) {
  const m = message.toLowerCase();
  if (m.includes("only .kml and .zip")) return "This file type isn't supported. Upload a .kml file, or a .zip that contains a Shapefile (.shp, .shx, .dbf, .prj).";
  if (m.includes("empty")) return "The file is empty. Export it again from your mapping tool and retry.";
  if (m.includes("missing required component")) return `${message} A Shapefile is several files; zip all of them together.`;
  if (m.includes("does not contain a .shp")) return "The zip has no Shapefile inside. Zip the .shp, .shx, .dbf and .prj files together.";
  return message;
}

function setProgress(step, text) {
  const order = ["upload", "process", "done"];
  $("home-progress").hidden = step == null;
  if (step == null) return;
  document.querySelectorAll("#home-progress .progress-steps span").forEach((el) => {
    const i = order.indexOf(el.dataset.step);
    const current = order.indexOf(step);
    el.classList.toggle("complete", i < current);
    el.classList.toggle("active", i === current);
  });
  $("home-progress-text").textContent = text || "";
}

function showError(title, message) {
  const box = $("home-error");
  box.hidden = !title;
  box.innerHTML = "";
  if (!title) return;
  const b = document.createElement("b");
  b.textContent = title;
  box.append(b, document.createTextNode(friendlyError(message)));
}

async function uploadAndOpen(file) {
  if (!file) return;
  showError(null);
  setProgress("upload", `Uploading ${file.name}…`);

  try {
    const form = new FormData();
    form.append("file", file);
    const upload = await getJson(API, { method: "POST", body: form });
    setProgress("process", upload.duplicate ? "You've uploaded this file before. Loading the saved result…" : "Reading shapes and measuring them…");

    // Wait for the background task to finish.
    let info;
    for (let attempt = 0; attempt < 60; attempt++) {
      info = await getJson(`${API}${upload.id}/`);
      if (info.status === "COMPLETED" || info.status === "FAILED") break;
      await sleep(1000);
    }

    if (info.status === "FAILED") {
      setProgress(null);
      showError(`Couldn't measure ${file.name}`, info.error_message || "Processing failed.");
      return;
    }

    setProgress("done", "Opening the workspace…");
    window.location.href = `/app?file=${encodeURIComponent(upload.id)}`;
  } catch (error) {
    setProgress(null);
    showError(`Couldn't upload ${file.name}`, error.message);
  }
}

const dropzone = $("home-dropzone");
$("home-file").addEventListener("change", (e) => {
  uploadAndOpen(e.target.files[0]);
  e.target.value = "";
});
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragging");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragging");
  uploadAndOpen(e.dataTransfer.files[0]);
});
