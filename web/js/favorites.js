import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const form = document.getElementById("favorites-form");
const tbody = document.getElementById("favorites-tbody");
const empty = document.getElementById("favorites-empty");
const addButton = document.getElementById("favorites-add");
const saveButton = document.getElementById("favorites-save");

let known = new Map();
let renderedFor = null;

function lookupText(node) {
  const info = known.get(node);
  if (!info) return "";
  return [info.callsign, info.description].filter(Boolean).join(" · ");
}

function row(favorite = { node: "", name: "", monitor: false }) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="text" data-field="node" inputmode="numeric" pattern="[0-9]{1,10}" required value="${escapeHtml(favorite.node)}" placeholder="2000" /></td>
    <td><input type="text" data-field="name" maxlength="40" value="${escapeHtml(favorite.name)}" placeholder="${escapeHtml(lookupText(favorite.node))}" /></td>
    <td><input type="checkbox" data-field="monitor" ${favorite.monitor ? "checked" : ""} /></td>
    <td class="row-actions"><button type="button" class="btn btn-danger btn-sm" data-remove>Remove</button></td>`;
  return tr;
}

function render() {
  const favorites = store.state.config?.link_favorites ?? [];
  const key = JSON.stringify(favorites);
  if (key === renderedFor) return;
  renderedFor = key;
  tbody.replaceChildren(...favorites.map(row));
  empty.hidden = favorites.length > 0;
}

async function lookup() {
  const status = await api("/api/links");
  known = new Map(status.favorites.map((f) => [f.node, f]));
  for (const tr of tbody.rows) {
    const node = tr.querySelector('[data-field="node"]').value;
    tr.querySelector('[data-field="name"]').placeholder = lookupText(node);
  }
}

async function save(event) {
  event.preventDefault();
  const link_favorites = [...tbody.rows].map((tr) => ({
    node: tr.querySelector('[data-field="node"]').value.trim(),
    name: tr.querySelector('[data-field="name"]').value.trim(),
    monitor: tr.querySelector('[data-field="monitor"]').checked,
  }));
  await withBusy(saveButton, async () => {
    try {
      store.set("config", await api("/api/config", { method: "PUT", json: { link_favorites } }));
      await lookup();
      toast("Favorites saved");
    } catch (error) {
      toastError(error);
    }
  });
}

export function initFavorites() {
  form.addEventListener("submit", save);
  addButton.addEventListener("click", () => {
    const tr = row();
    tbody.appendChild(tr);
    empty.hidden = true;
    tr.querySelector("input").focus();
  });
  tbody.addEventListener("click", (event) => {
    if (!event.target.closest("[data-remove]")) return;
    event.target.closest("tr").remove();
    empty.hidden = tbody.rows.length > 0;
  });
  store.addEventListener("config", render);
  if (store.state.config) render();
  const open = () => lookup().catch(() => {});
  router.addEventListener("change", ({ detail }) => detail === "allstar" && open());
  if (currentView === "allstar") open();
}
