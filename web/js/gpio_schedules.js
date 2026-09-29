import { api } from "./api.js";
import { pinLabel } from "./gpio.js";
import { dayPicker, pickedDays, scheduleText } from "./link_schedules.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const form = document.getElementById("gpio-schedules-form");
const list = document.getElementById("gpio-schedules-list");
const empty = document.getElementById("gpio-schedules-empty");
const addButton = document.getElementById("gpio-schedules-add");
const saveButton = document.getElementById("gpio-schedules-save");

let renderedFor = null;

function outputs() {
  const pins = store.state.config?.gpio_pins ?? {};
  return Object.entries(pins)
    .filter(([, settings]) => settings.mode === "output")
    .map(([pin, settings]) => ({ pin: Number(pin), name: settings.name }));
}

function pinOptions(selected) {
  const options = outputs().map(
    ({ pin, name }) => `<option value="${pin}"${pin === selected ? " selected" : ""}>${escapeHtml(pinLabel(pin, name))}</option>`
  );
  if (selected && !outputs().some(({ pin }) => pin === selected)) {
    options.push(`<option value="${selected}" selected>GPIO${selected} (not an output)</option>`);
  }
  return options.join("");
}

function item(schedule = { pin: outputs()[0]?.pin, days: [], time: "", minutes: 60, enabled: true }) {
  const div = document.createElement("div");
  div.className = "schedule-item";
  div.innerHTML = `
    <div class="form-grid">
      <label>Output
        <select data-field="pin" required>${pinOptions(schedule.pin)}</select>
      </label>
      <label>On at
        <input type="time" data-field="time" required value="${escapeHtml(schedule.time)}" />
      </label>
      <label>Minutes on
        <input type="number" data-field="minutes" min="0" max="1440" step="1" required value="${schedule.minutes}" />
      </label>
      ${dayPicker(schedule.days)}
    </div>
    <div class="schedule-foot">
      <label class="inline-check"><input type="checkbox" data-field="enabled" ${schedule.enabled ? "checked" : ""} />On</label>
      <span class="schedule-next muted small"></span>
      <span class="row-actions"><button type="button" class="btn btn-danger btn-sm" data-remove>Remove</button></span>
    </div>`;
  return div;
}

function render() {
  const config = store.state.config;
  const schedules = config?.gpio_schedules ?? [];
  const key = JSON.stringify([schedules, config?.gpio_pins]);
  if (key === renderedFor) return;
  renderedFor = key;
  list.replaceChildren(...schedules.map(item));
  syncEmpty();
}

function syncEmpty() {
  empty.hidden = list.children.length > 0;
  addButton.disabled = outputs().length === 0;
}

function showStatus(status) {
  if (!status) return;
  const saved = store.state.config?.gpio_schedules ?? [];
  if (list.children.length !== saved.length) return;
  [...list.children].forEach((div, index) => {
    const text = scheduleText(status.schedules?.[index], "On");
    const cell = div.querySelector(".schedule-next");
    if (cell.textContent !== text) cell.textContent = text;
  });
}

async function save(event) {
  event.preventDefault();
  const gpio_schedules = [...list.children].map((div) => {
    const value = (field) => div.querySelector(`[data-field="${field}"]`);
    return {
      pin: Number(value("pin").value),
      time: value("time").value,
      minutes: Number(value("minutes").value),
      days: pickedDays(div),
      enabled: value("enabled").checked,
    };
  });
  if (gpio_schedules.some((schedule) => schedule.days.length === 0)) {
    toastError(new Error("Pick at least one day for each schedule."));
    return;
  }
  await withBusy(saveButton, async () => {
    try {
      store.set("config", await api("/api/config", { method: "PUT", json: { gpio_schedules } }));
      store.set("gpio", await api("/api/gpio"));
      toast("Output schedules saved");
    } catch (error) {
      toastError(error);
    }
  });
}

export function initGpioSchedules() {
  form.addEventListener("submit", save);
  addButton.addEventListener("click", () => {
    const div = item();
    list.appendChild(div);
    syncEmpty();
    div.querySelector("input").focus();
  });
  list.addEventListener("click", (event) => {
    if (!event.target.closest("[data-remove]")) return;
    event.target.closest(".schedule-item").remove();
    syncEmpty();
  });
  store.addEventListener("config", render);
  store.addEventListener("gpio", ({ detail }) => showStatus(detail));
  if (store.state.config) render();
}
