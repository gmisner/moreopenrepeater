import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";

const POLL_MS = 250;
const METER_FLOOR_DB = -80;

const form = document.getElementById("audio-engine-form");
const stateTag = document.getElementById("audio-engine-state");
const levelFill = document.getElementById("audio-level-fill");
const levelThreshold = document.getElementById("audio-level-threshold");
const levelText = document.getElementById("audio-level-text");
const cosTag = document.getElementById("audio-cos");
const ctcssTag = document.getElementById("audio-ctcss");
const txTag = document.getElementById("audio-tx");
const health = document.getElementById("audio-health");
const errorLine = document.getElementById("audio-engine-error");
const silenceHint = document.getElementById("audio-silence-hint");
const SILENCE_DB = -120;
const SILENT_POLLS_BEFORE_HINT = 20;

let devices = [];
let silentPolls = 0;

function meterPercent(db) {
  return Math.max(0, Math.min(100, ((db - METER_FLOOR_DB) / -METER_FLOOR_DB) * 100));
}

// Device dropdowns list what PortAudio reports now, plus the saved device
// even if it's unplugged, so saving another setting doesn't silently drop it.
function renderDeviceSelects() {
  for (const select of form.querySelectorAll("[data-audio-devices]")) {
    const saved = store.state.config?.[select.name] ?? "";
    const current = form.classList.contains("dirty") ? select.value : saved;
    const names = devices.filter((d) => d[select.dataset.audioDevices] > 0).map((d) => d.name);
    select.innerHTML = '<option value="">System default</option>';
    for (const name of names) select.add(new Option(name, name));
    if (current && !names.includes(current)) select.add(new Option(`${current} (not connected)`, current));
    select.value = current;
  }
}

function setTag(tag, on, text, onClass = "tag-on") {
  tag.className = `tag ${on ? onClass : ""}`;
  tag.textContent = text;
}

function renderEngine(engine) {
  if (engine.running) setTag(stateTag, true, "running");
  else if (engine.error) setTag(stateTag, true, "error", "tag-danger");
  else setTag(stateTag, false, engine.enabled ? "stopped" : "off");

  errorLine.hidden = !engine.error;
  errorLine.textContent = engine.error ?? "";

  const running = engine.running;
  silentPolls = running && engine.rx_level_db <= SILENCE_DB ? silentPolls + 1 : 0;
  silenceHint.hidden = silentPolls < SILENT_POLLS_BEFORE_HINT;
  levelFill.style.width = `${running ? meterPercent(engine.rx_level_db) : 0}%`;
  levelFill.classList.toggle("open", running && engine.cos_open);
  levelText.textContent = running ? `${engine.rx_level_db.toFixed(0)} dBFS` : "–";
  setTag(cosTag, running && engine.cos_open, "carrier");
  setTag(ctcssTag, running && engine.ctcss_hz != null, engine.ctcss_hz != null ? `${engine.ctcss_hz} Hz` : "no tone");
  setTag(txTag, running && engine.transmitting, "TX", "tag-danger");

  const glitches = engine.dropped_input_blocks + engine.starved_output_blocks;
  health.textContent = running
    ? `${engine.sample_rate / 1000} kHz${engine.hardware_ptt ? " · CM108 PTT" : ""}${glitches ? ` · ${glitches} audio glitches` : ""}`
    : "";
}

function renderThreshold() {
  const source = form.elements.cos_source.value;
  const threshold = Number(form.elements.vox_threshold_db.value);
  levelThreshold.hidden = source !== "vox" || Number.isNaN(threshold);
  levelThreshold.style.left = `${meterPercent(threshold)}%`;
}

async function loadDevices() {
  try {
    devices = await api("/api/audio/devices");
  } catch {
    devices = [];
  }
  renderDeviceSelects();
}

async function poll() {
  if (currentView === "audio" && !document.hidden) {
    try {
      renderEngine(await api("/api/audio/engine"));
    } catch {
      // The next poll will retry; the session check handles sign-out.
    }
  }
  setTimeout(poll, POLL_MS);
}

export function initAudio() {
  store.addEventListener("config", () => {
    renderDeviceSelects();
    renderThreshold();
  });
  form.addEventListener("input", renderThreshold);
  router.addEventListener("change", ({ detail }) => {
    if (detail === "audio") loadDevices();
  });
  if (currentView === "audio") loadDevices();
  poll();
}
