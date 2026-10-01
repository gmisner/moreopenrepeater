import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const CHANNEL_LABELS = { ntfy: "ntfy", telegram: "Telegram", email: "Email", webhook: "Webhook" };
const SEVERITY_TAGS = { info: "tag-on", warning: "tag-warn", critical: "tag-danger" };

const el = (id) => document.getElementById(id);
const form = el("alerts-form");
const secretInputs = [...form.querySelectorAll("[data-secret]")];

let info = null;
const cleared = new Set();

function bytes(value) {
  if (value == null) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1000 && unit < units.length - 1) {
    size /= 1000;
    unit += 1;
  }
  return `${size >= 100 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

const kv = (rows) => rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
const tag = (cls, text) => `<span class="tag ${cls}">${escapeHtml(text)}</span>`;

function renderSystem() {
  const { system, settings, last_unexpected_stop: stop } = info;
  const temperature = system.temperature_c;
  const hot = temperature != null && temperature >= settings.temperature_limit_c;
  const throttled = system.throttled;
  let power = '<span class="muted">not a Raspberry Pi</span>';
  let throttling = power;
  if (throttled) {
    power = throttled.under_voltage_now
      ? tag("tag-danger", "under-voltage now")
      : throttled.under_voltage_occurred
        ? tag("tag-warn", "under-voltage since boot")
        : tag("tag-on", "OK");
    throttling = throttled.throttled_now
      ? tag("tag-danger", "throttled now")
      : throttled.throttled_occurred
        ? tag("tag-warn", "throttled since boot")
        : tag("tag-on", "none");
  }
  el("health-system").innerHTML = kv([
    [
      "CPU temperature",
      temperature == null
        ? '<span class="muted">not available</span>'
        : `${hot ? tag("tag-danger", `${temperature.toFixed(0)} °C`) : `${temperature.toFixed(0)} °C`} <small class="muted">alert at ${settings.temperature_limit_c} °C</small>`,
    ],
    ["Power supply", power],
    ["Throttling", throttling],
    ["Running since", system.boot_time ? escapeHtml(formatTimestamp(system.boot_time * 1000)) : "--"],
  ]);

  const disk = system.disk;
  el("health-disk").innerHTML = disk
    ? kv([
        ["Device", disk.device ? `<code>${escapeHtml(disk.device)}</code>` : "--"],
        ["Written since boot", bytes(disk.written_since_boot)],
        ["Written over its life", bytes(disk.written_lifetime)],
        ["Free space", `${bytes(disk.free)} of ${bytes(disk.total)}`],
        ["SD card protection", system.sd_protection ? tag("tag-on", "on") : tag("", "off")],
      ])
    : kv([["Storage", '<span class="muted">not available</span>']]);
  el("health-sd-note").hidden = system.sd_protection || !disk?.device?.startsWith("mmcblk");

  const note = el("alerts-unexpected-stop");
  note.hidden = !stop;
  if (stop) {
    const cause = stop.power ? "lost power or restarted without shutting down" : "stopped unexpectedly";
    note.textContent =
      `When it last started, the repeater had ${cause}. It was last known to be running at ` +
      `${formatTimestamp(stop.last_seen * 1000)} and came back at ${formatTimestamp(stop.restarted_at * 1000)}.`;
  }
}

function renderForm() {
  const { settings, secrets_set: secretsSet, channels } = info;
  for (const input of form.elements) {
    if (!input.name || input.dataset.secret !== undefined) continue;
    if (input.type === "checkbox") input.checked = Boolean(settings[input.name]);
    else input.value = settings[input.name] ?? "";
  }
  cleared.clear();
  for (const input of secretInputs) {
    input.value = "";
    input.placeholder = secretsSet[input.name] ? "Saved — type to replace" : input.dataset.placeholder ?? "";
    let remove = input.parentElement.querySelector("[data-remove-secret]");
    if (!remove) {
      remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-ghost btn-sm";
      remove.dataset.removeSecret = input.name;
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        cleared.add(input.name);
        input.value = "";
        input.placeholder = "Removed when you save";
        remove.hidden = true;
        form.classList.add("dirty");
      });
      input.insertAdjacentElement("afterend", remove);
    }
    remove.hidden = !secretsSet[input.name];
  }
  form.classList.remove("dirty");
  el("alerts-channels").innerHTML = channels.length
    ? channels.map((c) => tag(settings.enabled ? "tag-on" : "", CHANNEL_LABELS[c])).join(" ")
    : '<span class="muted small">nothing set up yet</span>';
}

function renderRecent() {
  const tbody = el("alerts-tbody");
  tbody.innerHTML = "";
  for (const alert of info.recent) {
    const row = document.createElement("tr");
    const failed = Object.entries(alert.errors).map(([c, e]) => `${CHANNEL_LABELS[c]}: ${e}`);
    const sent = alert.sent.length
      ? alert.sent.map((c) => CHANNEL_LABELS[c]).join(", ")
      : `<span class="muted">${escapeHtml(alert.note || "not sent")}</span>`;
    row.innerHTML = `
      <td>${escapeHtml(formatTimestamp(alert.at))}</td>
      <td>${tag(SEVERITY_TAGS[alert.severity], alert.severity)} <strong>${escapeHtml(alert.title)}</strong>
        <div class="muted small">${escapeHtml(alert.message)}</div></td>
      <td>${sent}${failed.length ? `<div class="form-error">${escapeHtml(failed.join("; "))}</div>` : ""}</td>`;
    tbody.appendChild(row);
  }
  el("alerts-empty").hidden = info.recent.length > 0;
}

function render() {
  renderSystem();
  if (!form.classList.contains("dirty")) renderForm();
  renderRecent();
}

async function load() {
  if (session?.role !== "admin") return;
  info = await api("/api/alerts");
  render();
}

function collect() {
  const body = {};
  for (const input of form.elements) {
    if (!input.name) continue;
    if (input.dataset.secret !== undefined) {
      if (input.value) body[input.name] = input.value;
      else if (cleared.has(input.name)) body[input.name] = "";
    } else if (input.type === "checkbox") body[input.name] = input.checked;
    else if (input.type === "number") body[input.name] = Number(input.value);
    else body[input.name] = input.value.trim();
  }
  return body;
}

function renderTestResults(results) {
  const list = el("alerts-test-results");
  list.innerHTML = Object.entries(results)
    .map(([channel, error]) =>
      error
        ? `<li>${tag("tag-danger", "failed")} ${CHANNEL_LABELS[channel]}: ${escapeHtml(error)}</li>`
        : `<li>${tag("tag-on", "sent")} ${CHANNEL_LABELS[channel]}</li>`,
    )
    .join("");
  list.hidden = false;
}

export function initAlerts() {
  for (const input of secretInputs) input.dataset.placeholder = input.placeholder;
  form.addEventListener("input", () => form.classList.add("dirty"));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    await withBusy(button, async () => {
      try {
        info = await api("/api/alerts", { method: "PUT", json: collect() });
        form.classList.remove("dirty");
        render();
        toast("Alert settings saved");
      } catch (error) {
        toastError(error);
      }
    });
  });
  const testButton = el("alerts-test");
  testButton.addEventListener("click", () =>
    withBusy(testButton, async () => {
      if (form.classList.contains("dirty")) {
        toast("Save your changes first", "error");
        return;
      }
      try {
        const { results } = await api("/api/alerts/test", { method: "POST" });
        renderTestResults(results);
        const failures = Object.values(results).filter(Boolean).length;
        if (failures) toast(`${failures} of ${Object.keys(results).length} didn't go out`, "error");
        else toast("Test alert sent");
      } catch (error) {
        toastError(error);
      }
    }),
  );
  el("alerts-refresh").addEventListener("click", (event) => withBusy(event.currentTarget, () => load().catch(toastError)));
  router.addEventListener("change", ({ detail }) => {
    if (detail === "alerts") load().catch(toastError);
  });
  if (currentView === "alerts") load().catch(toastError);
}
