import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const REFRESH_MS = 1000;
export const SPARE_PINS = [1, 2, 4, 5, 6, 7, 8];

const form = document.getElementById("gpio-form");
const tbody = document.getElementById("gpio-tbody");
const stateTag = document.getElementById("gpio-state");
const errorLine = document.getElementById("gpio-error");
const saveButton = document.getElementById("gpio-save");
const dashCard = document.getElementById("gpio-dash-card");
const dashPins = document.getElementById("gpio-dash-pins");
const dashNote = document.getElementById("gpio-dash-note");

let timer = null;
let latest = null;
let rowsFor = null;

export function pinLabel(pin, name) {
  return name ? `${name} (GPIO${pin})` : `GPIO${pin}`;
}

function stateText(row, available) {
  if (!row.mode || !available || row.on === null) return "–";
  if (row.mode === "input") return row.on ? "High" : "Low";
  return row.on ? "On" : "Off";
}

function renderRows() {
  const pins = store.state.config?.gpio_pins ?? {};
  const key = JSON.stringify(pins);
  if (key === rowsFor) return;
  rowsFor = key;
  tbody.innerHTML = SPARE_PINS.map((pin) => {
    const settings = pins[pin] ?? {};
    const mode = settings.mode ?? "";
    return `<tr data-pin="${pin}">
      <td><code>GPIO${pin}</code></td>
      <td><select name="mode-${pin}">
        <option value=""${mode === "" ? " selected" : ""}>Unused</option>
        <option value="output"${mode === "output" ? " selected" : ""}>Output</option>
        <option value="input"${mode === "input" ? " selected" : ""}>Input</option>
      </select></td>
      <td><input type="text" name="name-${pin}" maxlength="30" value="${escapeHtml(settings.name ?? "")}" /></td>
      <td data-state></td>
    </tr>`;
  }).join("");
  renderState();
}

function renderState() {
  if (!latest) return;
  const saved = store.state.config?.gpio_pins ?? {};
  stateTag.hidden = false;
  stateTag.textContent = latest.available ? "CM108 found" : "no CM108";
  stateTag.className = `tag ${latest.available ? "tag-on" : ""}`;
  errorLine.hidden = !latest.error;
  errorLine.textContent = latest.error ?? "";
  for (const row of latest.pins) {
    const cell = tbody.querySelector(`tr[data-pin="${row.pin}"] [data-state]`);
    if (!cell) continue;
    const text = stateText(row, latest.available);
    const switchable = latest.available && row.mode === "output" && saved[row.pin]?.mode === "output";
    const html = switchable
      ? `<button type="button" class="btn btn-sm ${row.on ? "btn-primary" : "btn-ghost"}" data-switch="${row.pin}">${text}</button>`
      : `<span class="tag ${row.on ? "tag-on" : ""}">${text}</span>`;
    if (cell.innerHTML !== html) cell.innerHTML = html;
  }
  renderDashboard();
}

function renderDashboard() {
  const used = latest.pins.filter((row) => row.mode);
  dashCard.hidden = used.length === 0;
  dashNote.hidden = latest.available && !latest.error;
  dashNote.textContent = latest.error ?? "No CM108 interface is connected, so the pins can't be read or switched.";
  const html = used
    .map((row) => {
      const tag = row.mode === "output" && latest.available ? "button" : "div";
      const attrs = tag === "button" ? ` type="button" data-switch="${row.pin}" title="Turn ${row.on ? "off" : "on"}"` : "";
      return `<${tag} class="indicator${row.on ? " on" : ""}"${attrs}>
        <span class="indicator-dot"></span>
        <span class="indicator-name">${escapeHtml(row.name || `GPIO${row.pin}`)}</span>
        <span class="indicator-value">${row.mode === "input" ? "input" : "output"} · ${stateText(row, latest.available)}</span>
      </${tag}>`;
    })
    .join("");
  if (dashPins.innerHTML !== html) dashPins.innerHTML = html;
}

async function refresh() {
  latest = await api("/api/gpio");
  renderState();
}

async function switchPin(pin) {
  const row = latest?.pins.find((p) => p.pin === pin);
  if (!row) return;
  try {
    latest = await api(`/api/gpio/${pin}`, { method: "PUT", json: { on: !row.on } });
    renderState();
  } catch (error) {
    toastError(error);
  }
}

async function save(event) {
  event.preventDefault();
  const gpio_pins = {};
  for (const pin of SPARE_PINS) {
    const mode = form.elements[`mode-${pin}`].value;
    if (mode) gpio_pins[pin] = { mode, name: form.elements[`name-${pin}`].value.trim() };
  }
  await withBusy(saveButton, async () => {
    try {
      store.set("config", await api("/api/config", { method: "PUT", json: { gpio_pins } }));
      await refresh();
      toast("GPIO pins saved");
    } catch (error) {
      toastError(error);
    }
  });
}

function onClick(event) {
  const button = event.target.closest("[data-switch]");
  if (button) switchPin(Number(button.dataset.switch));
}

function sync(view) {
  const wanted = view === "dashboard" || view === "audio";
  if (wanted && !timer) {
    refresh().catch(() => {});
    timer = setInterval(() => {
      if (!document.hidden) refresh().catch(() => {});
    }, REFRESH_MS);
  } else if (!wanted && timer) {
    clearInterval(timer);
    timer = null;
  }
}

export function initGpio() {
  form.addEventListener("submit", save);
  tbody.addEventListener("click", onClick);
  dashPins.addEventListener("click", onClick);
  store.addEventListener("config", () => {
    renderRows();
    renderState();
  });
  if (store.state.config) renderRows();
  router.addEventListener("change", ({ detail }) => sync(detail));
  sync(currentView);
}
