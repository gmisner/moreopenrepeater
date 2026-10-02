import { api } from "./api.js";
import { currentView } from "./router.js";
import { store } from "./store.js";

const POLL_MS = 250;
const METER_FLOOR_DB = -80;

const form = document.getElementById("monitor-form");
const stateTag = document.getElementById("monitor-state");
const levelFill = document.getElementById("monitor-level-fill");
const levelThreshold = document.getElementById("monitor-level-threshold");
const levelText = document.getElementById("monitor-level-text");
const squelchTag = document.getElementById("monitor-squelch");
const health = document.getElementById("monitor-health");
const errorLine = document.getElementById("monitor-error");

function meterPercent(db) {
  return Math.max(0, Math.min(100, ((db - METER_FLOOR_DB) / -METER_FLOOR_DB) * 100));
}

function renderFields() {
  const squelch = form.elements.monitor_squelch.value;
  for (const label of form.querySelectorAll("[data-monitor-squelch]")) label.hidden = label.dataset.monitorSquelch !== squelch;
  const threshold = Number(form.elements.monitor_vox_threshold_db.value);
  levelThreshold.hidden = squelch !== "vox" || Number.isNaN(threshold);
  levelThreshold.style.left = `${meterPercent(threshold)}%`;
}

function render(status) {
  const { running } = status;
  stateTag.className = `tag ${running ? "tag-on" : status.error ? "tag-danger" : ""}`;
  stateTag.textContent = running ? "running" : status.error ? "error" : status.enabled ? "stopped" : "off";
  errorLine.hidden = !status.error;
  errorLine.textContent = status.error ?? "";
  levelFill.style.width = `${running ? meterPercent(status.level_db) : 0}%`;
  levelFill.classList.toggle("open", running && status.squelch_open);
  levelText.textContent = running ? `${status.level_db.toFixed(0)} dBFS` : "–";
  squelchTag.className = `tag ${running && status.squelch_open ? "tag-on" : ""}`;
  const rate = status.device_sample_rate;
  const parts = [];
  if (running && rate) parts.push(rate === status.sample_rate ? `${rate / 1000} kHz` : `device ${rate / 1000} kHz → ${status.sample_rate / 1000} kHz`);
  if (running && status.listeners) parts.push(`${status.listeners} listening`);
  if (running && status.dropped_input_blocks) parts.push(`${status.dropped_input_blocks} audio glitches`);
  health.textContent = parts.join(" · ");
}

async function poll() {
  if (currentView === "audio" && !document.hidden) {
    try {
      render(await api("/api/monitor-receiver"));
    } catch {
      // The next poll will retry.
    }
  }
  setTimeout(poll, POLL_MS);
}

export function initMonitorReceiver() {
  store.addEventListener("config", renderFields);
  form.addEventListener("input", renderFields);
  form.querySelector("[data-reset]").addEventListener("click", () => setTimeout(renderFields));
  poll();
}
