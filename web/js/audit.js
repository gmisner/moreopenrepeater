import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { escapeHtml, formatTimestamp, toastError } from "./ui.js";

// [pattern on "METHOD /path", label]; the first match wins.
const ACTIONS = [
  [/^PUT \/api\/config$/, "Changed settings"],
  [/^POST \/api\/login$/, "Signed in"],
  [/^POST \/api\/logout$/, "Signed out"],
  [/^POST \/api\/macros$/, "Added a DTMF macro"],
  [/^DELETE \/api\/macros\/(.+)$/, "Deleted DTMF macro $1"],
  [/^POST \/api\/announcements$/, "Added an announcement"],
  [/^POST \/api\/announcements\/.+\/play$/, "Played an announcement"],
  [/^PUT \/api\/announcements\//, "Edited an announcement"],
  [/^DELETE \/api\/announcements\//, "Deleted an announcement"],
  [/^POST \/api\/weather\/check$/, "Checked weather alerts"],
  [/^POST \/api\/weather\/alerts\/.+\/play$/, "Played a weather alert"],
  [/^POST \/api\/assets$/, "Uploaded an audio clip"],
  [/^DELETE \/api\/assets\//, "Deleted an audio clip"],
  [/^DELETE \/api\/recordings\//, "Deleted a recording"],
  [/^POST \/api\/snapshot$/, "Restored a backup"],
  [/^POST \/api\/users$/, "Added a user"],
  [/^PUT \/api\/users\//, "Changed a user"],
  [/^DELETE \/api\/users\/(.+)$/, "Deleted user $1"],
  [/^POST \/api\/simulate\/(.+)$/, "Simulator: $1"],
  [/^POST \/api\/updates$/, "Started an update"],
  [/^DTMF (.+)$/, "Over the air: $1"],
];

const tbody = document.getElementById("audit-tbody");
const empty = document.getElementById("audit-empty");

function describe({ action, status }) {
  if (action === "POST /api/login" && status === 401) return "Sign-in failed";
  for (const [pattern, label] of ACTIONS) {
    if (pattern.test(action)) return action.replace(pattern, label);
  }
  return action;
}

function result(entry) {
  if (!entry.status || entry.status < 400) return "";
  if (entry.action === "POST /api/login") return "";
  const label = entry.status === 401 ? "not signed in" : entry.status === 403 ? "not allowed" : `failed (${entry.status})`;
  return `<span class="tag tag-danger">${label}</span>`;
}

async function load() {
  const entries = await api("/api/audit?limit=300");
  tbody.innerHTML = "";
  empty.hidden = entries.length > 0;
  for (const e of entries) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="muted small">${escapeHtml(formatTimestamp(e.at))}</td>
      <td>${escapeHtml(e.actor)}</td>
      <td title="${escapeHtml(e.action)}">${escapeHtml(decodeURIComponent(describe(e)))}</td>
      <td class="small">${escapeHtml(e.detail)}</td>
      <td>${result(e)}</td>`;
    tbody.appendChild(row);
  }
}

export function initAudit() {
  document.getElementById("audit-refresh").addEventListener("click", () => load().catch(toastError));
  router.addEventListener("change", ({ detail }) => {
    if (detail === "audit") load().catch(toastError);
  });
  if (currentView === "audit") load().catch(toastError);
}
