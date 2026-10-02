import { api } from "./api.js";
import { refreshFolds } from "./folds.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";

const POLL_MS = 250;
// Mirrors dsp/tones.py CTCSS_TONES_HZ.
const CTCSS_TONES = [
  67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8,
  123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9,
  183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
];
const METER_FLOOR_DB = -80;
const PTT_LABELS = { cm108: " · CM108 PTT", gpio: " · GPIO PTT" };

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
  const rate = engine.device_sample_rate ?? engine.sample_rate;
  const rateText =
    rate === engine.sample_rate ? `${rate / 1000} kHz` : `device ${rate / 1000} kHz → ${engine.sample_rate / 1000} kHz`;
  health.textContent = running
    ? `${rateText}${PTT_LABELS[engine.hardware_ptt] ?? ""}${glitches ? ` · ${glitches} audio glitches` : ""}`
    : "";
}

function renderThreshold() {
  const source = form.elements.cos_source.value;
  const threshold = Number(form.elements.vox_threshold_db.value);
  levelThreshold.hidden = source !== "vox" || Number.isNaN(threshold);
  levelThreshold.style.left = `${meterPercent(threshold)}%`;
}

function renderHardwareFields() {
  const source = form.elements.cos_source.value;
  const gpioPtt = form.elements.ptt_output.value === "gpio";
  const show = (selector, visible) => {
    for (const label of form.querySelectorAll(selector)) label.hidden = !visible;
  };
  show("[data-cos-hardware]", source === "cm108" || source === "gpio");
  show("[data-cos-gpio]", source === "gpio");
  show("[data-ptt-gpio]", gpioPtt);
}

async function loadDevices() {
  try {
    devices = await api("/api/audio/devices");
  } catch {
    devices = [];
  }
  renderDeviceSelects();
  refreshFolds();
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
  document.getElementById("ctcss-tones").replaceChildren(...CTCSS_TONES.map((hz) => new Option(hz.toFixed(1))));
  store.addEventListener("config", () => {
    renderDeviceSelects();
    renderThreshold();
    renderHardwareFields();
  });
  form.addEventListener("input", () => {
    renderThreshold();
    renderHardwareFields();
  });
  form.querySelector("[data-reset]").addEventListener("click", () =>
    setTimeout(() => {
      renderThreshold();
      renderHardwareFields();
    }),
  );
  router.addEventListener("change", ({ detail }) => {
    if (detail === "audio") loadDevices();
  });
  if (currentView === "audio") loadDevices();
  poll();
}
