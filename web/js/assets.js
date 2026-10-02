import { api } from "./api.js";
import { store } from "./store.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const KIND_LABELS = { courtesy_tone: "Courtesy tone", id: "Voice ID", timeout_tone: "Timeout tone", custom: "Custom" };
const ASSIGNMENT_FIELDS = [
  "courtesy_tone_asset_id",
  "courtesy_tone_link_asset_id",
  "courtesy_tone_patch_asset_id",
  "net_courtesy_tone_asset_id",
  "id_asset_id",
  "long_id_asset_id",
  "timeout_tone_asset_id",
];

const tbody = document.getElementById("assets-tbody");
const empty = document.getElementById("assets-empty");
const form = document.getElementById("asset-form");

function render(assets) {
  tbody.innerHTML = "";
  empty.hidden = assets.length > 0;
  const config = store.state.config ?? {};
  for (const asset of assets) {
    const inUse = ASSIGNMENT_FIELDS.some((field) => config[field] === asset.id);
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><span class="tag">${escapeHtml(KIND_LABELS[asset.kind] ?? asset.kind)}</span></td>
      <td>${escapeHtml(asset.filename)} ${inUse ? '<span class="tag tag-on">in use</span>' : ""}</td>
      <td class="muted small">${escapeHtml(formatTimestamp(asset.uploaded_at))}</td>
      <td><audio controls preload="none" src="/api/assets/${encodeURIComponent(asset.id)}/audio"></audio></td>
      <td class="row-actions"><button type="button" class="btn btn-danger btn-sm">Delete</button></td>
    `;
    row.querySelector("button").addEventListener("click", () => remove(asset, inUse));
    tbody.appendChild(row);
  }
}

async function remove(asset, inUse) {
  const warning = inUse ? "\n\nIt's currently assigned in your settings." : "";
  if (!confirm(`Delete "${asset.filename}"?${warning}`)) return;
  try {
    await api(`/api/assets/${encodeURIComponent(asset.id)}`, { method: "DELETE" });
    toast(`Deleted ${asset.filename}`);
    await loadAssets();
  } catch (error) {
    toastError(error);
  }
}

export async function loadAssets() {
  store.set("assets", await api("/api/assets"));
}

export function initAssets() {
  store.addEventListener("assets", ({ detail }) => render(detail));
  store.addEventListener("config", () => render(store.state.assets));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    withBusy(form.querySelector("button[type=submit]"), async () => {
      try {
        const asset = await api("/api/assets", { method: "POST", body: new FormData(form) });
        toast(`Uploaded ${asset.filename}`);
        form.reset();
        await loadAssets();
      } catch (error) {
        toastError(error);
      }
    });
  });
}
