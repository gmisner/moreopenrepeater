import { api } from "./api.js";
import { currentView } from "./router.js";
import { store } from "./store.js";

const POLL_MS = 250;
const METER_FLOOR_DB = -80;

const form = document.getElementById("link-radio-form");
const stateTag = document.getElementById("link-radio-state");
const levelFill = document.getElementById("link-radio-level-fill");
const levelThreshold = document.getElementById("link-radio-level-threshold");
const levelText = document.getElementById("link-radio-level-text");
const rxTag = document.getElementById("link-radio-rx");
const txTag = document.getElementById("link-radio-tx");
const health = document.getElementById("link-radio-health");
const errorLine = document.getElementById("link-radio-error");

function meterPercent(db) {
  return Math.max(0, Math.min(100, ((db - METER_FLOOR_DB) / -METER_FLOOR_DB) * 100));
}

function renderFields() {
  const cos = form.elements.link_radio_cos.value;
  const ptt = form.elements.link_radio_ptt.value;
  for (const label of form.querySelectorAll("[data-link-cos]")) label.hidden = !label.dataset.linkCos.split(" ").includes(cos);
  for (const label of form.querySelectorAll("[data-link-ptt]")) label.hidden = !label.dataset.linkPtt.split(" ").includes(ptt);
  const threshold = Number(form.elements.link_radio_vox_threshold_db.value);
  levelThreshold.hidden = cos !== "vox" || Number.isNaN(threshold);
  levelThreshold.style.left = `${meterPercent(threshold)}%`;
}

function render(status) {
  const { running } = status;
  stateTag.className = `tag ${running ? "tag-on" : status.error ? "tag-danger" : ""}`;
  stateTag.textContent = running ? "connected" : status.error ? "error" : status.enabled ? "stopped" : "off";
  errorLine.hidden = !status.error;
  errorLine.textContent = status.error ?? "";
  levelFill.style.width = `${running ? meterPercent(status.rx_level_db) : 0}%`;
  levelFill.classList.toggle("open", running && status.receiving);
  levelText.textContent = running ? `${status.rx_level_db.toFixed(0)} dBFS` : "–";
  rxTag.className = `tag ${running && status.receiving ? "tag-on" : ""}`;
  txTag.className = `tag ${running && status.transmitting ? "tag-danger" : ""}`;
  const parts = [];
  if (running && status.timed_out) parts.push("timed out until the user unkeys");
  if (running && status.id_owed) parts.push("ID due");
  if (running && status.device_sample_rate) parts.push(`${status.device_sample_rate / 1000} kHz`);
  const glitches = status.dropped_input_blocks + status.starved_output_blocks;
  if (running && glitches) parts.push(`${glitches} audio glitches`);
  health.textContent = parts.join(" · ");
}

async function poll() {
  if (currentView === "audio" && !document.hidden) {
    try {
      render(await api("/api/link-radio"));
    } catch {
      // The next poll will retry.
    }
  }
  setTimeout(poll, POLL_MS);
}

export function initLinkRadio() {
  store.addEventListener("config", renderFields);
  form.addEventListener("input", renderFields);
  form.querySelector("[data-reset]").addEventListener("click", () => setTimeout(renderFields));
  poll();
}
