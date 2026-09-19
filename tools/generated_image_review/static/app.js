let csrfToken = "";

const state = {
  item: null,
  busy: false,
  pointerId: null,
  startX: 0,
  currentX: 0,
};

const card = document.getElementById("card");
const reviewArea = document.getElementById("reviewArea");
const completeArea = document.getElementById("completeArea");
const image = document.getElementById("reviewImage");
const quoteText = document.getElementById("quoteText");
const metadata = document.getElementById("metadata");
const assessmentText = document.getElementById("assessmentText");
const progressText = document.getElementById("progressText");
const statusLine = document.getElementById("statusLine");
const badge = document.getElementById("swipeBadge");
const allowButton = document.getElementById("allowButton");
const rejectButton = document.getElementById("rejectButton");
const undoButton = document.getElementById("undoButton");
const exportButton = document.getElementById("exportButton");
const finalExportButton = document.getElementById("finalExportButton");

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    ...options,
  });
  csrfToken = response.headers.get("X-CSRF-Token") || csrfToken;
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function setBusy(value) {
  state.busy = value;
  allowButton.disabled = value;
  rejectButton.disabled = value;
  undoButton.disabled = value;
}

function renderProgress(progress) {
  progressText.textContent = `Reviewed ${progress.reviewed} / ${progress.total} · Allowed ${progress.allowed} · Rejected ${progress.rejected} · Remaining ${progress.remaining}`;
}

function renderItem(data) {
  renderProgress(data.progress);
  resetCard();
  if (data.complete) {
    state.item = null;
    reviewArea.classList.add("hidden");
    completeArea.classList.remove("hidden");
    statusLine.textContent = "All eligible images are reviewed.";
    return;
  }
  state.item = data.item;
  reviewArea.classList.remove("hidden");
  completeArea.classList.add("hidden");
  image.src = `${data.item.image_url}?v=${encodeURIComponent(data.item.quote_hash)}`;
  quoteText.textContent = data.item.quote_text;
  const flags = data.item.flags.length ? data.item.flags.join(", ") : "none";
  metadata.textContent = `Grade: ${data.item.grade} · Score: ${data.item.overall_score || "unknown"} · Flags: ${flags}`;
  assessmentText.textContent = data.item.assessment_text || "No assessment text available.";
  statusLine.textContent = "";
}

async function loadNext() {
  setBusy(true);
  try {
    renderItem(await api("/api/next"));
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

async function decide(decision) {
  if (state.busy || !state.item) return;
  setBusy(true);
  try {
    const data = await api("/api/decision", {
      method: "POST",
      body: JSON.stringify({ quote_hash: state.item.quote_hash, decision }),
    });
    renderItem(data.next);
  } catch (error) {
    statusLine.textContent = error.message;
    resetCard();
  } finally {
    setBusy(false);
  }
}

async function undo() {
  if (state.busy) return;
  setBusy(true);
  try {
    const data = await api("/api/undo", { method: "POST", body: "{}" });
    renderItem(data.next);
    statusLine.textContent = data.undone ? "Last decision undone." : "Nothing to undo.";
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

async function exportOverrides() {
  if (state.busy) return;
  setBusy(true);
  try {
    const data = await api("/api/export", {method: "POST"});
    statusLine.textContent = `Exported ${data.reviewed} decisions to ${data.export_file}`;
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

function resetCard() {
  card.classList.remove("dragging", "allowing", "rejecting");
  card.style.transform = "";
  badge.style.opacity = "0";
  badge.textContent = "";
}

function updateDrag(deltaX) {
  const width = card.clientWidth || 1;
  const ratio = Math.min(Math.abs(deltaX) / (width * 0.35), 1);
  const rotate = Math.max(Math.min(deltaX / 18, 10), -10);
  card.style.transform = `translateX(${deltaX}px) rotate(${rotate}deg)`;
  card.classList.toggle("allowing", deltaX > 0);
  card.classList.toggle("rejecting", deltaX < 0);
  badge.style.opacity = String(ratio);
  badge.textContent = deltaX >= 0 ? "ALLOW" : "REJECT";
  badge.style.color = deltaX >= 0 ? "var(--allow)" : "var(--reject)";
}

card.addEventListener("pointerdown", (event) => {
  if (state.busy || !state.item) return;
  state.pointerId = event.pointerId;
  state.startX = event.clientX;
  state.currentX = 0;
  card.setPointerCapture(event.pointerId);
  card.classList.add("dragging");
});

card.addEventListener("pointermove", (event) => {
  if (state.pointerId !== event.pointerId) return;
  state.currentX = event.clientX - state.startX;
  updateDrag(state.currentX);
});

card.addEventListener("pointerup", (event) => {
  if (state.pointerId !== event.pointerId) return;
  card.releasePointerCapture(event.pointerId);
  state.pointerId = null;
  card.classList.remove("dragging");
  const threshold = Math.max(95, card.clientWidth * 0.23);
  if (state.currentX > threshold) {
    decide("allow");
  } else if (state.currentX < -threshold) {
    decide("reject");
  } else {
    resetCard();
  }
});

card.addEventListener("pointercancel", resetCard);
allowButton.addEventListener("click", () => decide("allow"));
rejectButton.addEventListener("click", () => decide("reject"));
undoButton.addEventListener("click", undo);
exportButton.addEventListener("click", exportOverrides);
finalExportButton.addEventListener("click", exportOverrides);

document.addEventListener("keydown", (event) => {
  const target = event.target;
  if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) {
    return;
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    decide("allow");
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    decide("reject");
  } else if (event.key.toLowerCase() === "u") {
    event.preventDefault();
    undo();
  }
});

loadNext();

