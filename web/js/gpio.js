import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { shortTime } from "./link_schedules.js";
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
  return row.on ? "On" : "Off";
}

function macroOptions(selected) {
  const macros = store.state.macros ?? [];
  const options = macros.map((m) => {
    const label = `${m.pattern} · ${m.description || m.action}`;
    return `<option value="${escapeHtml(m.pattern)}"${m.pattern === selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
  });
  if (selected && !macros.some((m) => m.pattern === selected)) {
    options.push(`<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)} (not a saved macro)</option>`);
  }
  return `<option value="">No macro</option>${options.join("")}`;
}

function actionsRow(pin, settings) {
  const field = (state) => `
    <label>When ${state}, say
      <input type="text" name="${state}_say-${pin}" maxlength="200" value="${escapeHtml(settings[`${state}_say`] ?? "")}" />
    </label>
    <label>and run
      <select name="${state}_macro-${pin}">${macroOptions(settings[`${state}_macro`] ?? "")}</select>
    </label>`;
  return `<tr class="gpio-actions" data-actions="${pin}"${settings.mode === "input" ? "" : " hidden"}>
    <td></td>
    <td colspan="3">
      <label class="inline-check"><input type="checkbox" name="invert-${pin}"${settings.invert ? " checked" : ""} />On when the pin reads low</label>
      <div class="gpio-action-grid">${field("on")}${field("off")}</div>
    </td>
  </tr>`;
}

function renderRows() {
  const pins = store.state.config?.gpio_pins ?? {};
  const key = JSON.stringify([pins, (store.state.macros ?? []).map((m) => [m.pattern, m.description, m.action])]);
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
    </tr>${actionsRow(pin, settings)}`;
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
      const canSwitch = row.mode === "output" && latest.available && document.body.dataset.role !== "viewer";
      const tag = canSwitch ? "button" : "div";
      const attrs = tag === "button" ? ` type="button" data-switch="${row.pin}" title="Turn ${row.on ? "off" : "on"}"` : "";
      return `<${tag} class="indicator${row.on ? " on" : ""}"${attrs}>
        <span class="indicator-dot"></span>
        <span class="indicator-name">${escapeHtml(row.name || `GPIO${row.pin}`)}</span>
        <span class="indicator-value">${row.mode === "input" ? "input" : "output"} · ${stateText(row, latest.available)}${row.until ? ` until ${escapeHtml(shortTime(row.until))}` : ""}</span>
      </${tag}>`;
    })
    .join("");
  if (dashPins.innerHTML !== html) dashPins.innerHTML = html;
}

async function refresh() {
  latest = await api("/api/gpio");
  store.set("gpio", latest);
  renderState();
}

async function switchPin(pin) {
  const row = latest?.pins.find((p) => p.pin === pin);
  if (!row) return;
  try {
    latest = await api(`/api/gpio/${pin}`, { method: "PUT", json: { on: !row.on } });
    store.set("gpio", latest);
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
    if (!mode) continue;
    gpio_pins[pin] = { mode, name: form.elements[`name-${pin}`].value.trim() };
    if (mode === "input") {
      Object.assign(gpio_pins[pin], {
        invert: form.elements[`invert-${pin}`].checked,
        on_say: form.elements[`on_say-${pin}`].value.trim(),
        off_say: form.elements[`off_say-${pin}`].value.trim(),
        on_macro: form.elements[`on_macro-${pin}`].value,
        off_macro: form.elements[`off_macro-${pin}`].value,
      });
    }
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
  tbody.addEventListener("change", (event) => {
    const select = event.target.closest('select[name^="mode-"]');
    if (!select) return;
    const pin = select.name.slice("mode-".length);
    tbody.querySelector(`[data-actions="${pin}"]`).hidden = select.value !== "input";
  });
  dashPins.addEventListener("click", onClick);
  for (const key of ["config", "macros"]) {
    store.addEventListener(key, () => {
      renderRows();
      renderState();
    });
  }
  if (store.state.config) renderRows();
  router.addEventListener("change", ({ detail }) => sync(detail));
  sync(currentView);
}
