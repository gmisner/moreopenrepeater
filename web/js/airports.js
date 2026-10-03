import { api } from "./api.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const form = document.getElementById("airports-form");
const tbody = document.getElementById("airports-tbody");
const empty = document.getElementById("airports-empty");
const preview = document.getElementById("airports-preview");
const addButton = document.getElementById("airports-add");
const saveButton = document.getElementById("airports-save");

let renderedFor = null;

function row(airport = { icao: "", name: "" }) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="text" data-field="icao" pattern="[A-Za-z0-9]{4}" maxlength="4" required value="${escapeHtml(airport.icao)}" placeholder="KAMA" autocapitalize="characters" /></td>
    <td><input type="text" data-field="name" maxlength="40" value="${escapeHtml(airport.name)}" placeholder="Blank spells out the code" /></td>
    <td class="row-actions">
      <button type="button" class="btn btn-ghost btn-sm" data-preview>Preview</button>
      <button type="button" class="btn btn-danger btn-sm" data-remove>Remove</button>
    </td>`;
  return tr;
}

function render() {
  const airports = store.state.config?.metar_airports ?? [];
  const key = JSON.stringify(airports);
  if (key === renderedFor) return;
  renderedFor = key;
  tbody.replaceChildren(...airports.map(row));
  empty.hidden = airports.length > 0;
}

async function save(event) {
  event.preventDefault();
  const metar_airports = [...tbody.rows].map((tr) => ({
    icao: tr.querySelector('[data-field="icao"]').value.trim().toUpperCase(),
    name: tr.querySelector('[data-field="name"]').value.trim(),
  }));
  await withBusy(saveButton, async () => {
    try {
      store.set("config", await api("/api/config", { method: "PUT", json: { metar_airports } }));
      toast("Airports saved");
    } catch (error) {
      toastError(error);
    }
  });
}

async function showPreview(button) {
  const icao = button.closest("tr").querySelector('[data-field="icao"]').value.trim();
  if (!/^[A-Za-z0-9]{4}$/.test(icao)) {
    toastError(new Error("Enter a 4-character ICAO code first"));
    return;
  }
  await withBusy(button, async () => {
    try {
      const { text } = await api(`/api/metar/${encodeURIComponent(icao)}`);
      preview.textContent = text;
      preview.hidden = false;
    } catch (error) {
      toastError(error);
    }
  });
}

export function initAirports() {
  form.addEventListener("submit", save);
  addButton.addEventListener("click", () => {
    const tr = row();
    tbody.appendChild(tr);
    empty.hidden = true;
    tr.querySelector("input").focus();
  });
  tbody.addEventListener("click", (event) => {
    const previewButton = event.target.closest("[data-preview]");
    if (previewButton) {
      showPreview(previewButton);
      return;
    }
    if (!event.target.closest("[data-remove]")) return;
    event.target.closest("tr").remove();
    empty.hidden = tbody.rows.length > 0;
  });
  store.addEventListener("config", render);
  if (store.state.config) render();
}
