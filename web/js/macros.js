import { api } from "./api.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const tbody = document.getElementById("macros-tbody");
const empty = document.getElementById("macros-empty");
const form = document.getElementById("macro-form");
const title = document.getElementById("macro-form-title");
const submitButton = document.getElementById("macro-submit");
const cancelButton = document.getElementById("macro-cancel");

let editingPattern = null;

function render(macros) {
  tbody.innerHTML = "";
  empty.hidden = macros.length > 0;
  for (const macro of [...macros].sort((a, b) => a.pattern.localeCompare(b.pattern))) {
    const row = document.createElement("tr");
    row.classList.toggle("editing", macro.pattern === editingPattern);
    row.innerHTML = `
      <td><code>${escapeHtml(macro.pattern)}</code></td>
      <td>${escapeHtml(macro.description) || '<span class="muted">—</span>'}</td>
      <td><code>${escapeHtml(macro.command)}</code></td>
      <td>${escapeHtml(macro.node_id) || '<span class="muted">—</span>'}</td>
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

function startEdit(macro) {
  editingPattern = macro.pattern;
  for (const field of ["pattern", "description", "command", "node_id"]) form.elements[field].value = macro[field];
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
  title.textContent = "Add macro";
  submitButton.textContent = "Add macro";
  cancelButton.hidden = true;
  render(store.state.macros);
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
  const body = Object.fromEntries(new FormData(form));
  body.pattern = body.pattern.trim().toUpperCase();
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
  cancelButton.addEventListener("click", stopEdit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    withBusy(submitButton, submit);
  });
}
