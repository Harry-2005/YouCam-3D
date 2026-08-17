import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { PLYLoader } from "three/addons/loaders/PLYLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";
import { unzipSync } from "https://cdn.jsdelivr.net/npm/fflate@0.8.2/esm/browser.js";

const $ = (selector) => document.querySelector(selector);
const state = {
  references: [],
  pipeline: null,
  pollTimer: null,
  viewUrls: new Map(),
  renderedModelId: null,
  modelZip: null,
  styleJob: null,
  stylePollTimer: null,
  garmentFile: null,
  styleUrls: [],
  candidateUrls: [],
  selectedCandidateId: null,
};

const form = $("#pipelineForm");
const referenceInput = $("#referenceInput");
const referenceStrip = $("#referenceStrip");
const dropzone = $("#dropzone");
const promptInput = $("#promptInput");
const instagramUrl = $("#instagramUrl");
const garmentCategory = $("#garmentCategory");
const garmentInput = $("#garmentInput");
const garmentUploadButton = $("#garmentUploadButton");
const garmentUploadLabel = $("#garmentUploadLabel");
const stylePreviewButton = $("#stylePreviewButton");
const styleProgress = $("#styleProgress");
const styleProgressText = $("#styleProgressText");
const stylePreview = $("#stylePreview");
const garmentFrame = $("#garmentFrame");
const tryonResult = $("#tryonResult");
const garmentDescription = $("#garmentDescription");
const approveStyleButton = $("#approveStyleButton");
const advancedPanel = $(".advanced-panel");
const viewCountInput = $("#viewCountInput");
const viewCountValue = $("#viewCountValue");
const launchButton = $("#launchButton");
const formError = $("#formError");
const contactGrid = $("#contactGrid");
const coherenceValue = $("#coherenceValue");
const coherenceMeter = $("#coherenceMeter");
const coherenceNote = $("#coherenceNote");
const stageEyebrow = $("#stageEyebrow");
const stageTitle = $("#stageTitle");
const jobStamp = $("#jobStamp");
const angleReadout = $("#angleReadout");
const datasetButton = $("#datasetButton");
const downloadButton = $("#downloadButton");
const logButton = $("#logButton");
const historyList = $("#historyList");
const viewportEmpty = $("#viewportEmpty");
const viewportTitle = $("#viewportTitle");
const viewportCopy = $("#viewportCopy");
const pointCount = $("#pointCount");
const modelFormat = $("#modelFormat");
const lightRigButton = $("#lightRigButton");
const lightRigState = $("#lightRigState");
const lightRig = $("#lightRig");
const lightResetButton = $("#lightResetButton");
const lightCloseButton = $("#lightCloseButton");
const lightStatus = $("#lightStatus");
const lightDome = $("#lightDome");
const lightHandle = $("#lightHandle");
const lightAzimuth = $("#lightAzimuth");
const lightElevation = $("#lightElevation");
const lightIntensity = $("#lightIntensity");
const lightAzimuthValue = $("#lightAzimuthValue");
const lightElevationValue = $("#lightElevationValue");
const lightIntensityValue = $("#lightIntensityValue");
const logDialog = $("#logDialog");
const logContent = $("#logContent");
const toast = $("#toast");
const candidateGrid = $("#candidateGrid");
const confirmCandidateButton = $("#confirmCandidateButton");
const candidateChoiceTitle = $("#candidateChoiceTitle");
const candidateChoiceCopy = $("#candidateChoiceCopy");
const studioDock = $("#studioDock");
const dockToggle = $("#dockToggle");
const pageButtons = [...document.querySelectorAll("[data-page-target]")];
const pagePanels = [...document.querySelectorAll("[data-page-panel]")];
const resultTabButtons = [...document.querySelectorAll("[data-result-tab]")];

function notify(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove("show"), 3200);
}

function customerMessage(error, fallback = "We could not complete this request. Please try again.") {
  const message = String(error?.message || error || "").toLowerCase();
  if (message.includes("instagram") || message.includes("cookie") || message.includes("private post") || message.includes("media download")) {
    return "We could not access that Instagram post. Confirm that it is public or upload an outfit image.";
  }
  if (message.includes("failed to fetch") || message.includes("network") || message.includes("timeout")) {
    return "The service is temporarily unavailable. Please try again shortly.";
  }
  return fallback;
}

function unlockPage(page) {
  pageButtons.find((button) => button.dataset.pageTarget === page)?.removeAttribute("disabled");
}

function goToPage(page) {
  const target = pagePanels.find((panel) => panel.dataset.pagePanel === page);
  if (!target) return;
  document.body.dataset.page = page;
  pagePanels.forEach((panel) => panel.classList.toggle("active", panel === target));
  pageButtons.forEach((button) => button.classList.toggle("active", button.dataset.pageTarget === page));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setResultTab(tab) {
  const selected = tab === "model" ? "model" : "angles";
  document.body.dataset.resultTab = selected;
  resultTabButtons.forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.resultTab === selected));
  });
}

resultTabButtons.forEach((button) => button.addEventListener("click", () => setResultTab(button.dataset.resultTab)));
pageButtons.forEach((button) => button.addEventListener("click", () => {
  if (!button.disabled) goToPage(button.dataset.pageTarget);
}));
const dockIsCollapsed = localStorage.getItem("parallax_dock_collapsed") === "true";
studioDock.classList.toggle("collapsed", dockIsCollapsed);
document.body.classList.toggle("dock-collapsed", dockIsCollapsed);
dockToggle.setAttribute("aria-expanded", String(!dockIsCollapsed));
dockToggle.addEventListener("click", () => {
  const collapsed = studioDock.classList.toggle("collapsed");
  document.body.classList.toggle("dock-collapsed", collapsed);
  dockToggle.setAttribute("aria-expanded", String(!collapsed));
  localStorage.setItem("parallax_dock_collapsed", String(collapsed));
});

async function apiFetch(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {}
    throw new Error(message);
  }
  return response;
}

function addFiles(fileList) {
  const supported = new Set(["image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"]);
  for (const file of fileList) {
    if (!supported.has(file.type) || state.references.length >= 8) continue;
    if (!state.references.some((entry) => entry.file.name === file.name && entry.file.size === file.size)) {
      state.references.push({ file, url: URL.createObjectURL(file) });
    }
  }
  renderReferences();
}

function renderReferences() {
  referenceStrip.innerHTML = "";
  state.references.forEach((entry, index) => {
    const frame = document.createElement("div");
    frame.className = "reference-thumb";
    const image = document.createElement("img");
    image.src = entry.url;
    image.alt = `Reference ${index + 1}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(entry.url);
      state.references.splice(index, 1);
      renderReferences();
    });
    frame.append(image, remove);
    referenceStrip.append(frame);
  });
  $("#fileCount").textContent = `${state.references.length} / 8`;
  dropzone.classList.toggle("completed", state.references.length > 0);
  updatePrimaryAction();
}

dropzone.addEventListener("click", () => referenceInput.click());
dropzone.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) referenceInput.click(); });
referenceInput.addEventListener("change", () => addFiles(referenceInput.files));
["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); }));
["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); }));
dropzone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
viewCountInput.addEventListener("input", () => { viewCountValue.textContent = viewCountInput.value; renderContactSheet(); });

garmentUploadButton.addEventListener("click", () => garmentInput.click());
garmentInput.addEventListener("change", () => {
  state.garmentFile = garmentInput.files?.[0] || null;
  garmentUploadLabel.textContent = state.garmentFile ? state.garmentFile.name : "Upload an outfit image";
  garmentUploadButton.classList.toggle("loaded", Boolean(state.garmentFile));
  updatePrimaryAction();
});
instagramUrl.addEventListener("input", updatePrimaryAction);

function updatePrimaryAction() {
  const activeStatuses = new Set(["queued", "downloading_media", "selecting_garment", "uploading_assets", "generating_tryon"]);
  const ready = state.references.length > 0 && Boolean(instagramUrl.value.trim() || state.garmentFile);
  const busy = state.styleJob && activeStatuses.has(state.styleJob.status);
  stylePreviewButton.disabled = !ready || Boolean(busy);
}

function styleHumanStatus(status) {
  return {
    queued: "Outfit review scheduled",
    downloading_media: "Reviewing the Instagram post",
    selecting_garment: "Organizing distinct outfit options",
    awaiting_garment_selection: "Select your preferred outfit",
    uploading_assets: "Preparing the fitting images",
    generating_tryon: "Creating your YouCam fitting",
    complete: "Your YouCam fitting is complete",
    approved: "Your fitting is approved for 3D",
    failed: "The fitting could not be completed",
  }[status] || status;
}

function clearStyleUrls() {
  state.styleUrls.forEach((url) => URL.revokeObjectURL(url));
  state.styleUrls = [];
}

async function setAuthenticatedImage(image, path) {
  const response = await apiFetch(path);
  const url = URL.createObjectURL(await response.blob());
  state.styleUrls.push(url);
  image.src = url;
}

function clearCandidateUrls() {
  state.candidateUrls.forEach((url) => URL.revokeObjectURL(url));
  state.candidateUrls = [];
}

async function renderCandidateOptions(job) {
  clearCandidateUrls();
  state.selectedCandidateId = null;
  candidateGrid.innerHTML = "";
  confirmCandidateButton.disabled = true;
  candidateChoiceTitle.textContent = "Select an outfit";
  candidateChoiceCopy.textContent = "YouCam will apply only the option you confirm.";
  const categoryLabels = { upper_body: "Top or jacket", lower_body: "Bottom", full_body: "Full look" };
  for (const option of job.candidate_options || []) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "candidate-card";
    const image = document.createElement("img");
    image.alt = option.label;
    const check = document.createElement("span");
    check.textContent = "✓";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = option.label;
    const category = document.createElement("small");
    category.textContent = categoryLabels[option.garment_category] || "Detected outfit";
    copy.append(title, category);
    card.append(image, check, copy);
    card.addEventListener("click", () => {
      state.selectedCandidateId = option.id;
      candidateGrid.querySelectorAll(".candidate-card").forEach((item) => item.classList.toggle("selected", item === card));
      confirmCandidateButton.disabled = false;
      candidateChoiceTitle.textContent = option.label;
      candidateChoiceCopy.textContent = option.description || "This is the outfit YouCam will fit to your photo.";
    });
    candidateGrid.append(card);
    try {
      const response = await apiFetch(option.image_url);
      const url = URL.createObjectURL(await response.blob());
      state.candidateUrls.push(url);
      image.src = url;
    } catch {
      image.alt = `${option.label} preview unavailable`;
    }
  }
}

async function renderStyleJob(job) {
  state.styleJob = job;
  styleProgress.classList.remove("hidden", "failed", "complete");
  styleProgress.classList.toggle("failed", job.status === "failed");
  styleProgress.classList.toggle("complete", ["complete", "approved"].includes(job.status));
  styleProgressText.textContent = job.status === "failed" ? "The fitting could not be completed. Review the images and try again." : styleHumanStatus(job.status);
  if (job.status === "failed") {
    clearInterval(state.stylePollTimer);
    stylePreviewButton.querySelector("span").textContent = "Review outfit options";
    goToPage("create");
    updatePrimaryAction();
    return;
  }
  if (job.status === "awaiting_garment_selection") {
    clearInterval(state.stylePollTimer);
    await renderCandidateOptions(job);
    unlockPage("select");
    goToPage("select");
    stylePreviewButton.querySelector("span").textContent = "Review another outfit";
    updatePrimaryAction();
    return;
  }
  if (!["complete", "approved"].includes(job.status)) return;
  clearInterval(state.stylePollTimer);
  clearStyleUrls();
  await Promise.all([
    setAuthenticatedImage(garmentFrame, `/v1/style-jobs/${job.id}/garment`),
    setAuthenticatedImage(tryonResult, `/v1/style-jobs/${job.id}/result`),
  ]);
  garmentDescription.textContent = job.garment_description;
  stylePreview.classList.remove("hidden");
  advancedPanel.open = false;
  unlockPage("preview");
  goToPage("preview");
  stylePreviewButton.querySelector("span").textContent = "Review another outfit";
  updatePrimaryAction();
}

async function pollStyleJob() {
  if (!state.styleJob) return;
  try {
    const response = await apiFetch(`/v1/style-jobs/${state.styleJob.id}`);
    await renderStyleJob(await response.json());
  } catch (error) {
    styleProgressText.textContent = customerMessage(error, "We could not refresh the fitting status. Please try again.");
  }
}

async function loadLatestStyleJob() {
  try {
    const response = await apiFetch("/v1/style-jobs");
    const jobs = await response.json();
    const latest = jobs[0];
    if (!latest || latest.status === "approved") return;
    await renderStyleJob(latest);
    if (!["complete", "failed", "awaiting_garment_selection"].includes(latest.status)) {
      clearInterval(state.stylePollTimer);
      state.stylePollTimer = setInterval(pollStyleJob, 3000);
    }
  } catch {}
}

stylePreviewButton.addEventListener("click", async () => {
  formError.textContent = "";
  if (!state.references.length) return (formError.textContent = "Add a clear reference photo to continue.");
  if (!instagramUrl.value.trim() && !state.garmentFile) return (formError.textContent = "Add a public Instagram link or upload an outfit image to continue.");
  const payload = new FormData();
  payload.append("identity_image", state.references[0].file);
  payload.append("instagram_url", instagramUrl.value.trim());
  payload.append("garment_category", garmentCategory.value);
  if (state.garmentFile) payload.append("garment_image", state.garmentFile);
  stylePreviewButton.disabled = true;
  stylePreviewButton.querySelector("span").textContent = "Reviewing the outfit...";
  stylePreview.classList.add("hidden");
  advancedPanel.open = false;
  styleProgress.classList.remove("hidden", "failed", "complete");
  styleProgressText.textContent = "Preparing your fitting...";
  try {
    const response = await apiFetch("/v1/style-jobs", { method: "POST", body: payload });
    state.styleJob = await response.json();
    await renderStyleJob(state.styleJob);
    clearInterval(state.stylePollTimer);
    state.stylePollTimer = setInterval(pollStyleJob, 3000);
    notify("Outfit review started");
  } catch (error) {
    styleProgress.classList.add("failed");
    styleProgressText.textContent = customerMessage(error, "We could not create this fitting. Review the images and try again.");
    stylePreviewButton.querySelector("span").textContent = "Review outfit options";
    updatePrimaryAction();
  }
});

confirmCandidateButton.addEventListener("click", async () => {
  if (!state.styleJob || !state.selectedCandidateId) return;
  const payload = new FormData();
  payload.append("candidate_id", state.selectedCandidateId);
  confirmCandidateButton.disabled = true;
  confirmCandidateButton.querySelector("span").textContent = "Creating your fitting...";
  candidateChoiceCopy.textContent = "YouCam is applying the selected outfit to your photo.";
  try {
    const response = await apiFetch(`/v1/style-jobs/${state.styleJob.id}/garment-selection`, { method: "POST", body: payload });
    state.styleJob = await response.json();
    clearInterval(state.stylePollTimer);
    state.stylePollTimer = setInterval(pollStyleJob, 3000);
    notify("Outfit confirmed. Your YouCam fitting is in progress.");
  } catch (error) {
    candidateChoiceCopy.textContent = customerMessage(error, "We could not confirm this outfit. Please make your selection again.");
    confirmCandidateButton.disabled = false;
  } finally {
    confirmCandidateButton.querySelector("span").textContent = "Confirm outfit";
  }
});

approveStyleButton.addEventListener("click", async () => {
  if (!state.styleJob || !["complete", "approved"].includes(state.styleJob.status)) return;
  const payload = new FormData();
  payload.append("prompt", promptInput.value.trim());
  payload.append("method", $("#methodInput").value);
  payload.append("iterations", $("#iterationsInput").value);
  approveStyleButton.disabled = true;
  approveStyleButton.querySelector("span").textContent = "Starting 3D creation...";
  try {
    const response = await apiFetch(`/v1/style-jobs/${state.styleJob.id}/approve`, { method: "POST", body: payload });
    const pipeline = await response.json();
    promptInput.value = pipeline.prompt;
    stylePreview.classList.add("hidden");
    selectPipeline(pipeline);
    notify("Fitting approved. Creating twelve consistent views.");
    await loadHistory();
  } catch (error) {
    formError.textContent = customerMessage(error, "We could not start 3D creation. Please try again.");
  } finally {
    approveStyleButton.disabled = false;
    approveStyleButton.querySelector("span").textContent = "Approve and create 3D";
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  if (!state.references.length) return (formError.textContent = "Add a clear reference photo to continue.");
  const payload = new FormData();
  payload.append("prompt", promptInput.value.trim());
  state.references.forEach(({ file }) => payload.append("references", file));
  payload.append("view_count", viewCountInput.value);
  payload.append("method", $("#methodInput").value);
  payload.append("iterations", $("#iterationsInput").value);
  launchButton.disabled = true;
  launchButton.querySelector("span").textContent = "Preparing 3D creation…";
  try {
    const response = await apiFetch("/v1/pipelines", { method: "POST", body: payload });
    const pipeline = await response.json();
    promptInput.value = pipeline.prompt;
    selectPipeline(pipeline);
    notify("Your 3D look has been scheduled for creation.");
    await loadHistory();
  } catch (error) {
    formError.textContent = customerMessage(error, "We could not start 3D creation. Please try again.");
  } finally {
    launchButton.disabled = false;
    launchButton.querySelector("span").textContent = "Create my 3D look";
  }
});

function humanStatus(status) {
  return {
    queued: "Creation scheduled",
    generating_views: "Creating angle views",
    stabilizing_views: "Refining visual consistency",
    verifying_views: "Reviewing angle views",
    preparing_dataset: "Preparing the 3D composition",
    training: "Creating the 3D model",
    complete: "3D look complete",
    failed: "Creation could not be completed",
  }[status] || status;
}

function phaseFor(status) {
  return { queued: 0, generating_views: 1, stabilizing_views: 2, verifying_views: 2, preparing_dataset: 2, training: 3, complete: 4, failed: -1 }[status] ?? 0;
}

function updatePhases(pipeline) {
  const active = phaseFor(pipeline.status);
  const phases = [...document.querySelectorAll(".phase")];
  const lines = [...document.querySelectorAll(".track-line")];
  phases.forEach((phase, index) => {
    phase.classList.toggle("done", active > index || pipeline.status === "complete");
    phase.classList.toggle("active", active === index && pipeline.status !== "failed");
  });
  lines.forEach((line, index) => line.classList.toggle("done", active > index));
}

function renderContactSheet() {
  const pipeline = state.pipeline;
  const total = pipeline?.view_count || Number(viewCountInput.value);
  const generated = pipeline?.generated_views || 0;
  contactGrid.innerHTML = "";
  for (let index = 0; index < total; index += 1) {
    const frame = document.createElement("div");
    frame.className = `contact-frame ${index >= generated ? "pending" : ""}`;
    if (pipeline?.status === "generating_views" && index === generated) frame.classList.add("generating");
    const orbit = document.createElement("i");
    const angle = pipeline?.angles?.[index] ?? Math.round(index * 360 / total);
    const angleLabel = document.createElement("span"); angleLabel.className = "angle"; angleLabel.textContent = `${String(Math.round(angle)).padStart(3, "0")}°`;
    const frameNo = document.createElement("span"); frameNo.className = "frame-no"; frameNo.textContent = `F${String(index + 1).padStart(2, "0")}`;
    frame.append(orbit, angleLabel, frameNo);
    contactGrid.append(frame);
    if (pipeline && index < generated) loadViewImage(pipeline.id, index, frame);
  }
}

async function loadViewImage(id, index, frame) {
  const key = `${id}:${index}`;
  try {
    if (!state.viewUrls.has(key)) {
      const response = await apiFetch(`/v1/pipelines/${id}/views/${index}`);
      state.viewUrls.set(key, URL.createObjectURL(await response.blob()));
    }
    if (state.pipeline?.id !== id || frame.querySelector("img")) return;
    const image = document.createElement("img");
    image.src = state.viewUrls.get(key);
    image.alt = `Generated orbit view ${index + 1}`;
    frame.prepend(image);
  } catch {}
}

function selectPipeline(pipeline) {
  stylePreview.classList.add("hidden");
  state.pipeline = pipeline;
  state.modelZip = null;
  state.renderedModelId = null;
  setResultTab(pipeline.status === "complete" ? "model" : "angles");
  clearInterval(state.pollTimer);
  unlockPage("build");
  goToPage("build");
  renderPipeline(pipeline);
  state.pollTimer = setInterval(pollPipeline, 4000);
}

async function pollPipeline() {
  if (!state.pipeline) return;
  try {
    const response = await apiFetch(`/v1/pipelines/${state.pipeline.id}`);
    const pipeline = await response.json();
    if ((pipeline.stabilized_views || 0) !== (state.pipeline.stabilized_views || 0)) {
      for (const [key, url] of state.viewUrls) {
        if (key.startsWith(`${pipeline.id}:`)) {
          URL.revokeObjectURL(url);
          state.viewUrls.delete(key);
        }
      }
    }
    state.pipeline = pipeline;
    renderPipeline(pipeline);
    if (["complete", "failed"].includes(pipeline.status)) {
      clearInterval(state.pollTimer);
      await loadHistory();
    }
  } catch (error) {
    notify(customerMessage(error, "We could not refresh this look. Please try again."));
  }
}

function renderPipeline(pipeline) {
  stageEyebrow.textContent = humanStatus(pipeline.status);
  stageTitle.innerHTML = pipeline.status === "complete" ? "Your look,<br><em>now in 3D.</em>" : "Creating your<br><em>3D look.</em>";
  jobStamp.querySelector("strong").textContent = pipeline.id.slice(0, 8).toUpperCase();
  jobStamp.querySelector("small").textContent = humanStatus(pipeline.status).toUpperCase();
  angleReadout.textContent = pipeline.current_angle == null ? "AZ —° / EL 10°" : `AZ ${Math.round(pipeline.current_angle)}° / EL 10°`;
  updatePhases(pipeline);
  renderContactSheet();
  const score = pipeline.coherence_score;
  coherenceValue.textContent = score == null ? (["stabilizing_views", "verifying_views"].includes(pipeline.status) ? "In review" : "Assessment pending") : `${Math.round(score * 100)} / 100`;
  coherenceMeter.style.width = `${(score || 0) * 100}%`;
  coherenceNote.textContent = pipeline.verification_notes?.[0] || "Identity, outfit, pose, and color are evaluated across every angle.";
  datasetButton.disabled = !["training", "complete"].includes(pipeline.status);
  logButton.disabled = false;
  downloadButton.disabled = pipeline.status !== "complete";
  viewportTitle.textContent = pipeline.status === "failed" ? "Creation could not be completed" : pipeline.status === "training" ? "Creating your 3D model" : pipeline.status === "complete" ? "Preparing the interactive preview" : "Your 3D look will appear here";
  viewportCopy.textContent = pipeline.status === "failed" ? "Please try again with clear, consistent reference images." : pipeline.status === "training" ? "The model and its full-color materials are being prepared." : "Consistent angle views will be assembled into the final 3D result.";
  [...historyList.querySelectorAll(".history-item")].forEach((item) => item.classList.toggle("selected", item.dataset.id === pipeline.id));
  if (pipeline.status === "complete" && state.renderedModelId !== pipeline.id) {
    setResultTab("model");
    loadModel(pipeline.id);
  }
}

async function loadHistory() {
  try {
    const response = await apiFetch("/v1/pipelines");
    const pipelines = await response.json();
    historyList.innerHTML = "";
    if (!pipelines.length) {
      historyList.innerHTML = '<div class="library-empty"><img loading="lazy" src="./assets/clay-look-library-v1.webp" alt="Clay fashion archive cabinet filled with different saved-look objects" /><div><span>YOUR LOOK ARCHIVE</span><strong>Your collection is ready.</strong><p>Completed 3D fittings will appear here for convenient access.</p></div></div>';
      return;
    }
    pipelines.forEach((pipeline) => {
      const button = document.createElement("button");
      button.type = "button"; button.className = "history-item"; button.dataset.id = pipeline.id;
      const title = document.createElement("strong"); title.textContent = pipeline.prompt;
      const meta = document.createElement("small"); meta.textContent = `${pipeline.view_count} views · ${humanStatus(pipeline.status)}`;
      const dot = document.createElement("span"); dot.className = pipeline.status === "complete" ? "complete" : pipeline.status === "failed" ? "failed" : "active";
      button.append(title, meta, dot);
      button.addEventListener("click", () => selectPipeline(pipeline));
      historyList.append(button);
    });
  } catch (error) { historyList.innerHTML = `<p class="muted">${customerMessage(error, "Your saved looks are temporarily unavailable. Please refresh to try again.")}</p>`; }
}

$("#refreshHistory").addEventListener("click", loadHistory);

async function downloadFile(path, filename) {
  const response = await apiFetch(path);
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob); link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 2000);
}

datasetButton.addEventListener("click", () => state.pipeline && downloadFile(`/v1/pipelines/${state.pipeline.id}/dataset`, `${state.pipeline.id}-dataset.zip`));
downloadButton.addEventListener("click", () => state.pipeline && downloadFile(`/v1/pipelines/${state.pipeline.id}/model`, `${state.pipeline.id}-model.zip`));
logButton.addEventListener("click", async () => {
  if (!state.pipeline) return;
  logDialog.showModal(); logContent.textContent = "Preparing the creation report...";
  try { logContent.textContent = await (await apiFetch(`/v1/pipelines/${state.pipeline.id}/log`)).text(); }
  catch (error) { logContent.textContent = customerMessage(error, "The creation report is temporarily unavailable."); }
});
$("#closeLog").addEventListener("click", () => logDialog.close());

// Textured-model instrument
const canvas = $("#modelCanvas");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0xfaf7f9, 0.032);
const camera = new THREE.PerspectiveCamera(23, 1, 0.01, 1000);
camera.position.set(2.8, 2.1, 2.5);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true; controls.dampingFactor = 0.06; controls.autoRotate = true; controls.autoRotateSpeed = 0.5;
const grid = new THREE.GridHelper(8, 32, 0xf2b6cc, 0xeadfe5);
scene.add(grid);
scene.add(new THREE.HemisphereLight(0xffffff, 0xf3e7ed, 2.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.8);
keyLight.position.set(3, 4, 5);
scene.add(keyLight);
scene.add(keyLight.target);
const spark = new SparkRenderer({ renderer });
scene.add(spark);
let modelObject = null;
let modelBlobUrl = null;

const defaultLighting = { azimuth: 32, elevation: 35, intensity: 2.8 };
let lighting = { ...defaultLighting };
let lightingAvailable = false;
try {
  lighting = { ...lighting, ...JSON.parse(localStorage.getItem("parallax_lighting") || "{}") };
} catch {}

function updateDirectionalLight(persist = true) {
  const azimuth = THREE.MathUtils.degToRad(lighting.azimuth);
  const elevation = THREE.MathUtils.degToRad(lighting.elevation);
  const horizontal = Math.cos(elevation);
  keyLight.position.set(
    Math.sin(azimuth) * horizontal * 8,
    Math.sin(elevation) * 8,
    Math.cos(azimuth) * horizontal * 8,
  );
  keyLight.intensity = lighting.intensity;
  keyLight.target.position.set(0, 0, 0);
  lightAzimuth.value = String(Math.round(lighting.azimuth));
  lightElevation.value = String(Math.round(lighting.elevation));
  lightIntensity.value = String(lighting.intensity);
  lightAzimuthValue.textContent = `${Math.round(lighting.azimuth)}°`;
  lightElevationValue.textContent = `${Math.round(lighting.elevation)}°`;
  lightIntensityValue.textContent = lighting.intensity.toFixed(1);
  lightHandle.style.left = `${lighting.azimuth / 360 * 100}%`;
  lightHandle.style.top = `${(90 - lighting.elevation) / 90 * 100}%`;
  if (persist) localStorage.setItem("parallax_lighting", JSON.stringify(lighting));
}

function setLightingAvailable(available) {
  lightingAvailable = available;
  lightRig.classList.toggle("unavailable", !available);
  lightRigButton.classList.toggle("ready", available);
  lightRigState.textContent = available ? "Available" : "Available after creation";
  lightStatus.textContent = available
    ? "Drag the control to adjust light direction on the completed model."
    : "Directional lighting becomes available when the 3D model is complete.";
}

function setDomeLighting(event) {
  const bounds = lightDome.getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
  const y = Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height));
  lighting.azimuth = x * 360;
  lighting.elevation = (1 - y) * 90;
  updateDirectionalLight();
}

lightRigButton.addEventListener("click", () => {
  const opening = lightRig.classList.contains("hidden");
  lightRig.classList.toggle("hidden", !opening);
  lightRigButton.setAttribute("aria-expanded", String(opening));
  if (opening && !lightingAvailable) notify("Lighting controls become available when the 3D model is complete.");
});
lightCloseButton.addEventListener("click", () => {
  lightRig.classList.add("hidden");
  lightRigButton.setAttribute("aria-expanded", "false");
  lightRigButton.focus();
});
lightResetButton.addEventListener("click", () => {
  lighting = { ...defaultLighting };
  updateDirectionalLight();
  notify("Lighting settings restored");
});
lightAzimuth.addEventListener("input", () => { lighting.azimuth = Number(lightAzimuth.value); updateDirectionalLight(); });
lightElevation.addEventListener("input", () => { lighting.elevation = Number(lightElevation.value); updateDirectionalLight(); });
lightIntensity.addEventListener("input", () => { lighting.intensity = Number(lightIntensity.value); updateDirectionalLight(); });
lightDome.addEventListener("pointerdown", (event) => {
  lightDome.setPointerCapture(event.pointerId);
  setDomeLighting(event);
});
lightDome.addEventListener("pointermove", (event) => {
  if (lightDome.hasPointerCapture(event.pointerId)) setDomeLighting(event);
});
updateDirectionalLight(false);

function disposeModel(object) {
  if (!object) return;
  scene.remove(object);
  object.dispose?.();
  object.traverse((child) => {
    child.geometry?.dispose();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    materials.filter(Boolean).forEach((material) => {
      material.map?.dispose();
      material.dispose();
    });
  });
  if (modelBlobUrl) {
    URL.revokeObjectURL(modelBlobUrl);
    modelBlobUrl = null;
  }
}

function frameModel(object) {
  const box = new THREE.Box3().setFromObject(object);
  const center = box.getCenter(new THREE.Vector3());
  object.position.sub(center);
  const sphere = new THREE.Box3().setFromObject(object).getBoundingSphere(new THREE.Sphere());
  const radius = Math.max(sphere.radius, 0.01);
  const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.12;
  camera.near = Math.max(radius / 500, .001);
  camera.far = distance * 20;
  camera.position.set(0, distance * .2, distance);
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.update();
  return radius;
}

async function loadTexture(bytes) {
  const url = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
  try {
    const texture = await new THREE.TextureLoader().loadAsync(url);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
    texture.flipY = false;
    return texture;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function resizeRenderer() {
  const box = canvas.getBoundingClientRect();
  if (canvas.width !== Math.floor(box.width * renderer.getPixelRatio()) || canvas.height !== Math.floor(box.height * renderer.getPixelRatio())) {
    renderer.setSize(box.width, box.height, false); camera.aspect = box.width / box.height; camera.updateProjectionMatrix();
  }
}
function animate() { requestAnimationFrame(animate); resizeRenderer(); controls.update(); renderer.render(scene, camera); }
animate();

async function loadModel(id) {
  state.renderedModelId = id;
  viewportTitle.textContent = "Opening your 3D look";
  try {
    const response = await apiFetch(`/v1/pipelines/${id}/model`);
    const zipBytes = new Uint8Array(await response.arrayBuffer());
    const files = unzipSync(zipBytes);
    const glbName = Object.keys(files).find((name) => name.toLowerCase().endsWith(".glb"));
    const splatName = Object.keys(files).find((name) => name.toLowerCase().endsWith("splat.ply"));
    const objName = Object.keys(files).find((name) => name.toLowerCase().endsWith("mesh.obj"));
    const textureName = Object.keys(files).find((name) => name.toLowerCase().endsWith("material_0.png"));
    disposeModel(modelObject);
    camera.up.set(0, 1, 0);
    controls.autoRotate = true;
    grid.visible = true;
    setLightingAvailable(false);
    grid.position.y = 0;
    if (glbName) {
      viewportTitle.textContent = "Applying color and materials";
      const bytes = files[glbName];
      const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
      const gltf = await new GLTFLoader().parseAsync(buffer, "");
      let triangles = 0;
      gltf.scene.traverse((child) => {
        if (!child.isMesh) return;
        triangles += child.geometry.index ? child.geometry.index.count / 3 : child.geometry.getAttribute("position").count / 3;
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        materials.filter(Boolean).forEach((material) => {
          if (material.map) material.map.colorSpace = THREE.SRGBColorSpace;
          material.needsUpdate = true;
        });
      });
      modelObject = gltf.scene;
      scene.add(modelObject);
      camera.fov = 35;
      const radius = frameModel(modelObject);
      grid.position.y = -radius * 1.02;
      modelFormat.textContent = "FULL-COLOR 3D LOOK";
      pointCount.textContent = `${Math.round(triangles).toLocaleString()} SURFACE ELEMENTS`;
      viewportEmpty.classList.add("hidden");
      setLightingAvailable(true);
      notify("Your full-color 3D look is complete.");
      return;
    }
    if (splatName) {
      modelObject = new SplatMesh({ fileBytes: files[splatName], fileName: "splat.ply" });
      modelObject.quaternion.set(1, 0, 0, 0);
      scene.add(modelObject);
      viewportTitle.textContent = "Preparing the interactive view";
      await modelObject.initialized;
      camera.fov = 23;
      grid.visible = false;
      camera.up.set(0, 0, -1);
      camera.near = .01; camera.far = 100; camera.position.set(1, 0, 0); camera.updateProjectionMatrix();
      controls.autoRotate = false;
      controls.target.set(0, 0, 0); controls.update();
      modelFormat.textContent = "FULL-COLOR 3D VIEW";
      pointCount.textContent = `${Number(modelObject.numSplats || 0).toLocaleString()} COLOR ELEMENTS`;
      viewportEmpty.classList.add("hidden");
      setLightingAvailable(false);
      notify("Your full-color 3D view is complete.");
      return;
    }
    if (objName && textureName) {
      const object = new OBJLoader().parse(new TextDecoder().decode(files[objName]));
      const texture = await loadTexture(files[textureName]);
      let triangles = 0;
      object.traverse((child) => {
        if (!child.isMesh) return;
        triangles += child.geometry.index ? child.geometry.index.count / 3 : child.geometry.getAttribute("position").count / 3;
        child.material = new THREE.MeshStandardMaterial({ map: texture, roughness: .78, metalness: .04, side: THREE.DoubleSide });
      });
      modelObject = object;
      scene.add(modelObject);
      camera.fov = 42;
      frameModel(modelObject);
      modelFormat.textContent = "FULL-COLOR 3D LOOK";
      pointCount.textContent = `${Math.round(triangles).toLocaleString()} SURFACE ELEMENTS`;
      viewportEmpty.classList.add("hidden");
      setLightingAvailable(true);
      notify("Your textured 3D look is complete.");
      return;
    }
    const plyName = Object.keys(files).find((name) => name.toLowerCase().endsWith(".ply"));
    if (!plyName) throw new Error("The model archive did not contain a PLY point cloud.");
    const bytes = files[plyName];
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const geometry = new PLYLoader().parse(buffer);
    geometry.computeBoundingBox(); geometry.center(); geometry.computeBoundingSphere();
    const radius = geometry.boundingSphere?.radius || 1;
    const material = new THREE.PointsMaterial({ size: Math.max(radius / 380, 0.0025), vertexColors: Boolean(geometry.getAttribute("color")), color: geometry.getAttribute("color") ? 0xffffff : 0xf43f7a, sizeAttenuation: true, transparent: true, opacity: .96 });
    modelObject = new THREE.Points(geometry, material); scene.add(modelObject);
    camera.fov = 42;
    frameModel(modelObject);
    viewportEmpty.classList.add("hidden");
    setLightingAvailable(false);
    modelFormat.textContent = geometry.getAttribute("color") ? "COLOR 3D VIEW" : "3D VIEW";
    pointCount.textContent = `${geometry.getAttribute("position").count.toLocaleString()} MODEL ELEMENTS`;
    notify("Your 3D look is complete.");
  } catch (error) {
    viewportTitle.textContent = "The 3D preview is unavailable"; viewportCopy.textContent = customerMessage(error, "The model could not be opened. Please download it or try again."); viewportEmpty.classList.remove("hidden");
  }
}

renderContactSheet();
loadHistory();
loadLatestStyleJob();
