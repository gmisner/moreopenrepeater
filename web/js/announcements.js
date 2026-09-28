import { api } from "./api.js";
import { playClip } from "./player.js";
import { router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MAX_SAYS_LENGTH = 70;

const tbody = document.getElementById("announcements-tbody");
const empty = document.getElementById("announcements-empty");
const form = document.getElementById("announcement-form");
const title = document.getElementById("announcement-form-title");
const submitButton = document.getElementById("announcement-submit");
const cancelButton = document.getElementById("announcement-cancel");
const previewButton = document.getElementById("announcement-preview");
const assetSelect = document.getElementById("announcement-asset");
const messageField = document.getElementById("announcement-message");

let editingId = null;

function describeSchedule(a) {
  if (a.kind === "interval") {
    return a.every_minutes % 60 === 0 ? `Every ${a.every_minutes / 60} h` : `Every ${a.every_minutes} min`;
  }
  const days = a.days.length === 7 ? "Daily" : a.days.map((d) => DAY_NAMES[d]).join(", ");
  return `${days} at ${a.times.join(", ")}`;
}

// `next_run` is the station's local wall-clock time (no timezone).
function describeNextRun(a) {
  if (!a.enabled) return '<span class="tag">paused</span>';
  if (!a.next_run) return '<span class="muted">—</span>';
  const when = new Date(a.next_run);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  if (when.toDateString() === today.toDateString()) return `Today ${time}`;
  return `${when.toLocaleDateString([], { weekday: "short" })} ${time}`;
}

function describeSays(a) {
  if (a.asset_id) {
    const asset = (store.state.assets ?? []).find((x) => x.id === a.asset_id);
    return `<span class="tag">clip</span> ${escapeHtml(asset?.filename ?? "(deleted clip)")}`;
  }
  const text = a.message.length > MAX_SAYS_LENGTH ? `${a.message.slice(0, MAX_SAYS_LENGTH)}…` : a.message;
  return escapeHtml(text);
}

function render(announcements) {
  tbody.innerHTML = "";
  empty.hidden = announcements.length > 0;
  for (const a of [...announcements].sort((x, y) => x.name.localeCompare(y.name))) {
    const row = document.createElement("tr");
    row.classList.toggle("editing", a.id === editingId);
    row.innerHTML = `
      <td>${escapeHtml(a.name)}</td>
      <td>${describeSays(a)}</td>
      <td>${escapeHtml(describeSchedule(a))}</td>
      <td>${describeNextRun(a)}</td>
      <td class="row-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-action="play" title="Transmit now (waits for a clear channel)">Play now</button>
        <button type="button" class="btn btn-ghost btn-sm" data-action="edit">Edit</button>
        <button type="button" class="btn btn-danger btn-sm" data-action="delete">Delete</button>
      </td>
    `;
    row.querySelector("[data-action=play]").addEventListener("click", (e) => withBusy(e.currentTarget, () => playNow(a)));
    row.querySelector("[data-action=edit]").addEventListener("click", () => startEdit(a));
    row.querySelector("[data-action=delete]").addEventListener("click", () => remove(a));
    tbody.appendChild(row);
  }
}

function renderAssetOptions(assets) {
  const current = assetSelect.value;
  assetSelect.innerHTML = '<option value="">Speak the message</option>';
  for (const asset of assets) {
    const option = document.createElement("option");
    option.value = asset.id;
    option.textContent = asset.filename;
    assetSelect.appendChild(option);
  }
  assetSelect.value = current;
}

function syncFormVisibility() {
  const kind = form.elements.kind.value;
  for (const el of form.querySelectorAll("[data-kind]")) el.hidden = el.dataset.kind !== kind;
  form.elements.times.required = kind === "weekly";
  const usesClip = Boolean(assetSelect.value);
  messageField.hidden = usesClip;
  form.elements.message.required = !usesClip;
}

function collect() {
  const elements = form.elements;
  return {
    name: elements.name.value.trim(),
    message: elements.message.value.trim(),
    asset_id: assetSelect.value || null,
    kind: elements.kind.value,
    every_minutes: Number(elements.every_minutes.value) || 60,
    times: elements.times.value.split(",").map((t) => t.trim()).filter(Boolean),
    days: [...form.querySelectorAll("input[name=days]:checked")].map((box) => Number(box.value)),
    enabled: elements.enabled.checked,
  };
}

function populate(a) {
  const elements = form.elements;
  elements.name.value = a.name;
  elements.message.value = a.message;
  assetSelect.value = a.asset_id ?? "";
  elements.kind.value = a.kind;
  elements.every_minutes.value = a.every_minutes;
  elements.times.value = a.times.join(", ");
  for (const box of form.querySelectorAll("input[name=days]")) box.checked = a.days.includes(Number(box.value));
  elements.enabled.checked = a.enabled;
  syncFormVisibility();
}

function startEdit(a) {
  editingId = a.id;
  populate(a);
  title.textContent = `Edit ${a.name}`;
  submitButton.textContent = "Save changes";
  cancelButton.hidden = false;
  render(store.state.announcements);
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
  form.elements.name.focus();
}

function stopEdit() {
  editingId = null;
  form.reset();
  syncFormVisibility();
  title.textContent = "Add announcement";
  submitButton.textContent = "Add announcement";
  cancelButton.hidden = true;
  render(store.state.announcements);
}

async function playNow(a) {
  try {
    await api(`/api/announcements/${encodeURIComponent(a.id)}/play`, { method: "POST" });
    toast(`Queued "${a.name}" — it plays as soon as the channel is clear`);
  } catch (error) {
    toastError(error);
  }
}

async function remove(a) {
  if (!confirm(`Delete announcement "${a.name}"?`)) return;
  try {
    await api(`/api/announcements/${encodeURIComponent(a.id)}`, { method: "DELETE" });
    if (editingId === a.id) stopEdit();
    toast(`Deleted "${a.name}"`);
    await loadAnnouncements();
  } catch (error) {
    toastError(error);
  }
}

async function submit() {
  const body = collect();
  if (body.kind === "weekly" && body.days.length === 0) {
    toastError("Pick at least one day");
    return;
  }
  try {
    const saved = editingId
      ? await api(`/api/announcements/${encodeURIComponent(editingId)}`, { method: "PUT", json: body })
      : await api("/api/announcements", { method: "POST", json: body });
    toast(editingId ? `Updated "${saved.name}"` : `Added "${saved.name}"`);
    stopEdit();
    await loadAnnouncements();
  } catch (error) {
    toastError(error);
  }
}

async function preview() {
  const { message, asset_id } = collect();
  if (!asset_id && !message) {
    toastError("Type a message to preview");
    return;
  }
  try {
    await playClip(asset_id ? `asset:${asset_id}` : `tts:${message}`);
  } catch (error) {
    toastError(error);
  }
}

export async function loadAnnouncements() {
  store.set("announcements", await api("/api/announcements"));
}

export function initAnnouncements() {
  store.addEventListener("announcements", ({ detail }) => render(detail));
  store.addEventListener("assets", ({ detail }) => {
    renderAssetOptions(detail);
    if (store.state.announcements) render(store.state.announcements);
  });
  // "Next" times move on as announcements fire; refresh when the view is opened.
  router.addEventListener("change", ({ detail }) => {
    if (detail === "announcements") loadAnnouncements().catch(toastError);
  });
  form.elements.kind.addEventListener("change", syncFormVisibility);
  assetSelect.addEventListener("change", syncFormVisibility);
  cancelButton.addEventListener("click", stopEdit);
  previewButton.addEventListener("click", () => withBusy(previewButton, preview));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    withBusy(submitButton, submit);
  });
  syncFormVisibility();
}
