import { api } from "./api.js";
import { dayPicker, pickedDays, shortTime } from "./link_schedules.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const POLL_MS = 15000;

const el = (id) => document.getElementById(id);
const checkinsBody = el("net-checkins");
const scheduleList = el("net-schedules-list");

let net = null;
let editing = null; // id of the check-in being edited
let polling = null;
let schedulesRenderedFor = null;

// Net times are unix seconds.
const clock = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const when = (at) => shortTime(at * 1000);

function minutes(seconds) {
  const total = Math.max(0, Math.round(seconds / 60));
  return total < 60 ? `${total} min` : `${Math.floor(total / 60)} h ${total % 60} min`;
}

function runningText(current) {
  const parts = [`Started ${clock(current.started_at)} by ${current.started_by}`, `ends by itself at ${clock(current.ends_at)}`];
  if (current.linked_node) parts.push(`linked to node ${current.linked_node} for the net`);
  return `${parts.join(", ")}.`;
}

function renderControl() {
  const current = net.current;
  el("net-title").textContent = current ? current.name : "No net running";
  el("net-state").innerHTML = current ? '<span class="tag tag-on">running</span>' : "";
  let detail = current ? runningText(current) : "";
  if (current?.link_error) detail += ` Links: ${current.link_error}`;
  if (!current && net.next_scheduled) detail = `Next scheduled net: ${shortTime(net.next_scheduled)}.`;
  el("net-detail").textContent = detail;
  el("net-start-form").hidden = Boolean(current);
  el("net-end-actions").hidden = !current;
  el("net-start-name").placeholder = store.state.config?.net_name || "Net";

  el("net-card").hidden = !current;
  if (current) {
    el("net-card-title").textContent = `Net running: ${current.name}`;
    const count = current.checkins.length;
    el("net-card-detail").textContent = `${runningText(current)} ${count} check-in${count === 1 ? "" : "s"} so far.`;
  }
}

function checkinRow(checkin, index) {
  const row = document.createElement("tr");
  row.dataset.id = checkin.id;
  if (checkin.id === editing) {
    row.innerHTML = `
      <td>${index + 1}</td><td>${clock(checkin.at)}</td>
      <td><input type="text" data-field="callsign" required pattern="[A-Za-z0-9\\/\\-]{1,15}" value="${escapeHtml(checkin.callsign)}" /></td>
      <td><input type="text" data-field="notes" maxlength="300" value="${escapeHtml(checkin.notes)}" /></td>
      <td class="row-actions"><button type="button" class="btn btn-primary btn-sm" data-save>Save</button><button type="button" class="btn btn-ghost btn-sm" data-cancel>Cancel</button></td>`;
  } else {
    row.innerHTML = `
      <td>${index + 1}</td><td>${clock(checkin.at)}</td>
      <td><strong>${escapeHtml(checkin.callsign)}</strong></td><td>${escapeHtml(checkin.notes)}</td>
      <td class="row-actions"><button type="button" class="btn btn-ghost btn-sm" data-edit data-requires-write>Edit</button><button type="button" class="btn btn-danger btn-sm" data-delete data-requires-write>Remove</button></td>`;
  }
  return row;
}

function renderCheckins() {
  const current = net.current;
  el("net-checkins-card").hidden = !current;
  if (!current) return;
  if (editing && !current.checkins.some((c) => c.id === editing)) editing = null;
  if (editing && checkinsBody.contains(document.activeElement)) return; // don't clobber an edit in progress
  checkinsBody.replaceChildren(...current.checkins.map(checkinRow));
  el("net-checkins-empty").hidden = current.checkins.length > 0;
  const count = current.checkins.length;
  el("net-checkin-count").textContent = count ? `${count} station${count === 1 ? "" : "s"}` : "";
}

function renderPast() {
  el("net-past").innerHTML = net.past
    .map(
      (past) => `<tr data-id="${escapeHtml(past.id)}">
        <td>${escapeHtml(past.name)}</td><td>${escapeHtml(when(past.started_at))}</td>
        <td>${minutes(past.ended_at - past.started_at)}</td><td>${past.checkin_count}</td>
        <td class="row-actions"><a class="btn btn-ghost btn-sm" href="/api/nets/${encodeURIComponent(past.id)}/checkins.csv" download>CSV</a><button type="button" class="btn btn-danger btn-sm" data-delete-net data-requires-write>Delete</button></td>
      </tr>`,
    )
    .join("");
  el("net-past-empty").hidden = net.past.length > 0;
}

function show(status) {
  net = status;
  renderControl();
  renderCheckins();
  renderPast();
}

async function refresh() {
  show(await api("/api/net"));
}

function startPolling() {
  if (polling) return;
  polling = setInterval(() => {
    const watching = currentView === "net" || (currentView === "dashboard" && net?.current);
    if (watching && !document.hidden) refresh().catch(() => {});
  }, POLL_MS);
}

async function call(path, options, message) {
  try {
    show(await api(path, options));
    if (message) toast(message);
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

function endNet(button) {
  if (!confirm(`End ${net?.current?.name ?? "the net"}?`)) return;
  withBusy(button, () => call("/api/net/end", { method: "POST" }, "Net ended"));
}

// -- schedules -------------------------------------------------------------

function scheduleItem(schedule = { days: [], time: "", minutes: 60, enabled: true }) {
  const div = document.createElement("div");
  div.className = "schedule-item";
  div.innerHTML = `
    <div class="form-grid">
      <label>Start
        <input type="time" data-field="time" required value="${escapeHtml(schedule.time)}" />
      </label>
      <label>Minutes
        <input type="number" data-field="minutes" min="0" max="720" step="1" required value="${schedule.minutes}" />
      </label>
      ${dayPicker(schedule.days)}
    </div>
    <div class="schedule-foot">
      <label class="inline-check"><input type="checkbox" data-field="enabled" ${schedule.enabled ? "checked" : ""} />On</label>
      <span class="row-actions"><button type="button" class="btn btn-danger btn-sm" data-remove>Remove</button></span>
    </div>`;
  return div;
}

function renderSchedules() {
  const schedules = store.state.config?.net_schedules ?? [];
  const key = JSON.stringify(schedules);
  if (key === schedulesRenderedFor) return;
  schedulesRenderedFor = key;
  scheduleList.replaceChildren(...schedules.map(scheduleItem));
  el("net-schedules-empty").hidden = schedules.length > 0;
}

async function saveSchedules(event) {
  event.preventDefault();
  const net_schedules = [...scheduleList.children].map((div) => {
    const value = (field) => div.querySelector(`[data-field="${field}"]`);
    return {
      time: value("time").value,
      minutes: Number(value("minutes").value),
      days: pickedDays(div),
      enabled: value("enabled").checked,
    };
  });
  if (net_schedules.some((schedule) => schedule.days.length === 0)) {
    toastError(new Error("Pick at least one day for each schedule."));
    return;
  }
  await withBusy(el("net-schedules-save"), async () => {
    try {
      store.set("config", await api("/api/config", { method: "PUT", json: { net_schedules } }));
      await refresh();
      toast("Schedules saved");
    } catch (error) {
      toastError(error);
    }
  });
}

export function initNet() {
  el("net-start-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.submitter ?? event.target.querySelector("button");
    withBusy(button, async () => {
      if (await call("/api/net/start", { method: "POST", json: { name: el("net-start-name").value } }, "Net started")) {
        event.target.reset();
        el("net-checkin-callsign").focus();
      }
    });
  });
  el("net-end").addEventListener("click", (event) => endNet(event.currentTarget));
  el("net-card-end").addEventListener("click", (event) => endNet(event.currentTarget));

  el("net-checkin-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const callsign = el("net-checkin-callsign");
    const notes = el("net-checkin-notes");
    withBusy(el("net-checkin-add"), async () => {
      const json = { callsign: callsign.value.trim(), notes: notes.value.trim() };
      if (await call("/api/net/checkins", { method: "POST", json })) {
        event.target.reset(); // also clears :user-invalid
        callsign.focus();
      }
    });
  });

  checkinsBody.addEventListener("click", async (event) => {
    const row = event.target.closest("tr[data-id]");
    if (!row) return;
    const id = row.dataset.id;
    if (event.target.closest("[data-edit]")) {
      editing = id;
      renderCheckins();
      checkinsBody.querySelector('[data-field="callsign"]')?.focus();
    } else if (event.target.closest("[data-cancel]")) {
      editing = null;
      renderCheckins();
    } else if (event.target.closest("[data-save]")) {
      const callsign = row.querySelector('[data-field="callsign"]');
      if (!callsign.reportValidity()) return;
      const json = { callsign: callsign.value.trim(), notes: row.querySelector('[data-field="notes"]').value.trim() };
      editing = null;
      await call(`/api/net/checkins/${encodeURIComponent(id)}`, { method: "PUT", json });
    } else if (event.target.closest("[data-delete]")) {
      const callsign = row.querySelector("strong")?.textContent ?? "this check-in";
      if (confirm(`Remove ${callsign}?`)) await call(`/api/net/checkins/${encodeURIComponent(id)}`, { method: "DELETE" });
    }
  });
  checkinsBody.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.matches("input")) {
      event.preventDefault();
      event.target.closest("tr").querySelector("[data-save]")?.click();
    } else if (event.key === "Escape" && editing) {
      editing = null;
      renderCheckins();
    }
  });

  el("net-past").addEventListener("click", async (event) => {
    if (!event.target.closest("[data-delete-net]")) return;
    const row = event.target.closest("tr[data-id]");
    if (confirm("Delete this net and its check-ins?")) {
      await call(`/api/nets/${encodeURIComponent(row.dataset.id)}`, { method: "DELETE" }, "Net deleted");
    }
  });

  el("net-schedules-form").addEventListener("submit", saveSchedules);
  el("net-schedules-add").addEventListener("click", () => {
    const div = scheduleItem();
    scheduleList.appendChild(div);
    el("net-schedules-empty").hidden = true;
    div.querySelector("input").focus();
  });
  scheduleList.addEventListener("click", (event) => {
    if (!event.target.closest("[data-remove]")) return;
    event.target.closest(".schedule-item").remove();
    el("net-schedules-empty").hidden = scheduleList.children.length > 0;
  });

  store.addEventListener("config", renderSchedules);
  if (store.state.config) renderSchedules();
  // A net started or ended over the air, by schedule, or in another browser.
  store.addEventListener("status", ({ detail }) => {
    if (net && Boolean(net.current) !== detail.net_active) refresh().catch(() => {});
  });
  router.addEventListener("change", ({ detail }) => {
    if (detail === "net" || detail === "dashboard") refresh().catch(toastError);
  });
  refresh().catch(() => {});
  startPolling();
}
