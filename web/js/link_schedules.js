import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const form = document.getElementById("schedules-form");
const list = document.getElementById("schedules-list");
const empty = document.getElementById("schedules-empty");
const addButton = document.getElementById("schedules-add");
const saveButton = document.getElementById("schedules-save");

let renderedFor = null;

// Times are the station's local wall clock (no timezone).
export function shortTime(value) {
  const when = new Date(value);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return time;
  const farOff = when - Date.now() > 6 * 24 * 3600 * 1000;
  const day = when.toLocaleDateString([], farOff ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "short" });
  return `${day} ${time}`;
}

function item(schedule = { node: "", name: "", days: [], time: "", minutes: 60, monitor: false, enabled: true }) {
  const div = document.createElement("div");
  div.className = "schedule-item";
  const days = DAY_NAMES.map(
    (day, index) =>
      `<label><input type="checkbox" data-day="${index}" ${schedule.days.includes(index) ? "checked" : ""} />${day}</label>`
  ).join("");
  div.innerHTML = `
    <div class="form-grid">
      <label>Node
        <input type="text" data-field="node" inputmode="numeric" pattern="[0-9]{1,10}" required value="${escapeHtml(schedule.node)}" placeholder="2000" />
      </label>
      <label>Name
        <input type="text" data-field="name" maxlength="40" value="${escapeHtml(schedule.name)}" placeholder="Tuesday net" />
      </label>
      <label>Start
        <input type="time" data-field="time" required value="${escapeHtml(schedule.time)}" />
      </label>
      <label>Minutes linked
        <input type="number" data-field="minutes" min="0" max="720" step="1" required value="${schedule.minutes}" />
      </label>
      <fieldset class="span-2 day-picker"><legend>Days</legend>${days}</fieldset>
    </div>
    <div class="schedule-foot">
      <label class="inline-check"><input type="checkbox" data-field="monitor" ${schedule.monitor ? "checked" : ""} />Monitor only</label>
      <label class="inline-check"><input type="checkbox" data-field="enabled" ${schedule.enabled ? "checked" : ""} />On</label>
      <span class="schedule-next muted small"></span>
      <span class="row-actions"><button type="button" class="btn btn-danger btn-sm" data-remove>Remove</button></span>
    </div>`;
  return div;
}

function render() {
  const schedules = store.state.config?.link_schedules ?? [];
  const key = JSON.stringify(schedules);
  if (key === renderedFor) return;
  renderedFor = key;
  list.replaceChildren(...schedules.map(item));
  empty.hidden = schedules.length > 0;
}

function showStatus(status) {
  if (renderedFor !== JSON.stringify(store.state.config?.link_schedules ?? [])) return;
  [...list.children].forEach((div, index) => {
    const row = status.schedules[index];
    const text = !row ? "" : row.until ? `Linked until ${shortTime(row.until)}` : row.next_start ? `Next: ${shortTime(row.next_start)}` : "";
    div.querySelector(".schedule-next").textContent = text;
  });
}

async function refresh() {
  showStatus(await api("/api/links"));
}

function collect() {
  return [...list.children].map((div) => {
    const value = (field) => div.querySelector(`[data-field="${field}"]`);
    return {
      node: value("node").value.trim(),
      name: value("name").value.trim(),
      time: value("time").value,
      minutes: Number(value("minutes").value),
      days: [...div.querySelectorAll("[data-day]:checked")].map((box) => Number(box.dataset.day)),
      monitor: value("monitor").checked,
      enabled: value("enabled").checked,
    };
  });
}

async function save(event) {
  event.preventDefault();
  const link_schedules = collect();
  if (link_schedules.some((schedule) => schedule.days.length === 0)) {
    toastError(new Error("Pick at least one day for each schedule."));
    return;
  }
  await withBusy(saveButton, async () => {
    try {
      store.set("config", await api("/api/config", { method: "PUT", json: { link_schedules } }));
      await refresh();
      toast("Schedules saved");
    } catch (error) {
      toastError(error);
    }
  });
}

export function initLinkSchedules() {
  form.addEventListener("submit", save);
  addButton.addEventListener("click", () => {
    const div = item();
    list.appendChild(div);
    empty.hidden = true;
    div.querySelector("input").focus();
  });
  list.addEventListener("click", (event) => {
    if (!event.target.closest("[data-remove]")) return;
    event.target.closest(".schedule-item").remove();
    empty.hidden = list.children.length > 0;
  });
  store.addEventListener("config", () => {
    render();
    refresh().catch(() => {});
  });
  if (store.state.config) render();
  const open = () => refresh().catch(() => {});
  router.addEventListener("change", ({ detail }) => detail === "allstar" && open());
  if (currentView === "allstar") open();
}
