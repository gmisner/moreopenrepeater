import { api } from "./api.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const tbody = document.getElementById("macros-tbody");
const empty = document.getElementById("macros-empty");
const form = document.getElementById("macro-form");
const title = document.getElementById("macro-form-title");
const submitButton = document.getElementById("macro-submit");
const cancelButton = document.getElementById("macro-cancel");

const ACTION_LABELS = {
  link: "Link command",
  time: "Talking clock",
  weather: "Weather alerts",
  id: "Station ID",
  announcement: "Announcement",
  say: "Say text",
  parrot: "Parrot",
  tx_disable: "Transmitter off",
  tx_enable: "Transmitter on",
  aprs: "APRS stations nearby",
};

let editingPattern = null;

function details(macro) {
  const muted = '<span class="muted">—</span>';
  if (macro.action === "link") {
    const node = macro.node_id ? ` <span class="muted">→ node ${escapeHtml(macro.node_id)}</span>` : "";
    return `<code>${escapeHtml(macro.command)}</code>${node}`;
  }
  if (macro.action === "announcement") {
    const announcement = store.state.announcements?.find((a) => a.id === macro.command);
    return announcement ? escapeHtml(announcement.name) : '<span class="tag tag-warn">missing announcement</span>';
  }
  if (macro.action === "say") return `&ldquo;${escapeHtml(macro.command)}&rdquo;`;
  return muted;
}

function render(macros) {
  tbody.innerHTML = "";
  empty.hidden = macros.length > 0;
  for (const macro of [...macros].sort((a, b) => a.pattern.localeCompare(b.pattern))) {
    const row = document.createElement("tr");
    row.classList.toggle("editing", macro.pattern === editingPattern);
    row.innerHTML = `
      <td><code>${escapeHtml(macro.pattern)}</code></td>
      <td>${escapeHtml(macro.description) || '<span class="muted">—</span>'}</td>
      <td>${escapeHtml(ACTION_LABELS[macro.action] ?? macro.action)}</td>
      <td>${details(macro)}</td>
      <td class="row-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-action="edit">Edit</button>
        <button type="button" class="btn btn-danger btn-sm" data-action="delete">Delete</button>
      </td>
    `;
    row.querySelector("[data-action=edit]").addEventListener("click", () => startEdit(macro));
    row.querySelector("[data-action=delete]").addEventListener("click", () => remove(macro.pattern));
    tbody.appendChild(row);
  }
}

function renderAnnouncementOptions() {
  const select = form.elements.announcement_id;
  const current = select.value;
  select.replaceChildren(...(store.state.announcements ?? []).map((a) => new Option(a.name, a.id)));
  if (current) select.value = current;
}

function showArgumentFields() {
  const action = form.elements.action.value;
  for (const field of form.querySelectorAll("[data-macro-arg]")) {
    const shown = field.dataset.macroArg === action;
    field.hidden = !shown;
    for (const input of field.querySelectorAll("input, select")) input.disabled = !shown;
  }
  form.elements.command.required = action === "link";
  form.elements.say_text.required = action === "say";
  form.elements.announcement_id.required = action === "announcement";
}

function startEdit(macro) {
  editingPattern = macro.pattern;
  form.reset();
  for (const field of ["pattern", "description", "action", "node_id"]) form.elements[field].value = macro[field];
  if (macro.action === "link") form.elements.command.value = macro.command;
  if (macro.action === "say") form.elements.say_text.value = macro.command;
  if (macro.action === "announcement") form.elements.announcement_id.value = macro.command;
  showArgumentFields();
  title.textContent = `Edit macro ${macro.pattern}`;
  submitButton.textContent = "Save changes";
  cancelButton.hidden = false;
  render(store.state.macros);
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
  form.elements.pattern.focus();
}

function stopEdit() {
  editingPattern = null;
  form.reset();
  showArgumentFields();
  title.textContent = "Add macro";
  submitButton.textContent = "Add macro";
  cancelButton.hidden = true;
  render(store.state.macros);
}

function collect() {
  const els = form.elements;
  const action = els.action.value;
  const command = { link: els.command.value, say: els.say_text.value, announcement: els.announcement_id.value }[action] ?? "";
  return {
    pattern: els.pattern.value.trim().toUpperCase(),
    description: els.description.value.trim(),
    action,
    command: command.trim(),
    node_id: action === "link" ? els.node_id.value.trim() : "",
  };
}

async function remove(pattern) {
  if (!confirm(`Delete macro ${pattern}?`)) return;
  try {
    store.set("macros", await api(`/api/macros/${encodeURIComponent(pattern)}`, { method: "DELETE" }));
    if (editingPattern === pattern) stopEdit();
    toast(`Deleted macro ${pattern}`);
  } catch (error) {
    toastError(error);
  }
}

async function submit() {
  const body = collect();
  const existing = store.state.macros.find((m) => m.pattern === body.pattern);
  if (existing && body.pattern !== editingPattern && !confirm(`Macro ${body.pattern} already exists. Replace it?`)) return;
  try {
    let macros = await api("/api/macros", { method: "POST", json: body });
    // Renaming a macro's pattern: the API keys macros by pattern, so the old
    // entry has to be removed separately.
    if (editingPattern && editingPattern !== body.pattern) {
      macros = await api(`/api/macros/${encodeURIComponent(editingPattern)}`, { method: "DELETE" });
    }
    store.set("macros", macros);
    toast(editingPattern ? `Updated macro ${body.pattern}` : `Added macro ${body.pattern}`);
    stopEdit();
  } catch (error) {
    toastError(error);
  }
}

export async function loadMacros() {
  store.set("macros", await api("/api/macros"));
}

export function initMacros() {
  store.addEventListener("macros", ({ detail }) => render(detail));
  store.addEventListener("announcements", () => {
    renderAnnouncementOptions();
    if (store.state.macros) render(store.state.macros);
  });
  form.elements.action.addEventListener("change", showArgumentFields);
  showArgumentFields();
  cancelButton.addEventListener("click", stopEdit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    withBusy(submitButton, submit);
  });
}
