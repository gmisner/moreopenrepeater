import { api } from "./api.js";
import { refreshFolds } from "./folds.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const POLL_MS = 250;
// Mirrors dsp/tones.py CTCSS_TONES_HZ.
const CTCSS_TONES = [
  67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8,
  123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9,
  183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
];
const METER_FLOOR_DB = -80;
const PTT_LABELS = { cm108: " · CM108 PTT", gpio: " · GPIO PTT", serial: " · serial PTT" };

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
  for (const select of document.querySelectorAll("[data-audio-devices]")) {
    const saved = store.state.config?.[select.name] ?? "";
    const current = select.form.classList.contains("dirty") ? select.value : saved;
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

const GLITCH_LABELS = {
  dropped_input_blocks: "Receive audio dropped",
  starved_output_blocks: "Transmit gap",
  input_overflows: "Sound card receive overrun",
  output_underflows: "Sound card transmit underrun",
};
const GLITCH_SHORT = {
  dropped_input_blocks: "receive drops",
  starved_output_blocks: "transmit gaps",
  input_overflows: "receive overruns",
  output_underflows: "transmit underruns",
};

function glitchSummary(engine) {
  return Object.entries(GLITCH_SHORT)
    .filter(([counter]) => engine[counter])
    .map(([counter, label]) => `${engine[counter]} ${label}`)
    .join(" · ");
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

  const glitches = glitchSummary(engine);
  const rate = engine.device_sample_rate ?? engine.sample_rate;
  const rateText =
    rate === engine.sample_rate ? `${rate / 1000} kHz` : `device ${rate / 1000} kHz → ${engine.sample_rate / 1000} kHz`;
  health.textContent = running
    ? `${rateText}${PTT_LABELS[engine.hardware_ptt] ?? ""}${glitches ? ` · ${glitches}` : ""}`
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
  const ptt = form.elements.ptt_output.value;
  const show = (selector, visible) => {
    for (const label of form.querySelectorAll(selector)) label.hidden = !visible;
  };
  show("[data-cos-hardware]", ["cm108", "gpio", "serial"].includes(source));
  show("[data-cos-gpio]", source === "gpio");
  show("[data-cos-serial]", source === "serial");
  show("[data-ptt-gpio]", ptt === "gpio");
  show("[data-ptt-serial]", ptt === "serial");
  show("[data-ptt-hardware]", ptt === "gpio" || ptt === "serial");
}

async function loadSerialPorts() {
  let ports = [];
  try {
    ports = await api("/api/serial/ports");
  } catch {
    // Typing a path still works.
  }
  document.getElementById("serial-ports").replaceChildren(
    ...ports.map((port) => new Option(port.path === port.target ? "" : port.target, port.path)),
  );
}

async function loadDevices() {
  try {
    devices = await api("/api/audio/devices");
  } catch {
    devices = [];
  }
  renderDeviceSelects();
  refreshFolds();
  loadSerialPorts();
}

const levelsList = document.getElementById("sound-levels-list");
const levelsNotes = document.getElementById("sound-levels-notes");
const levelsActions = document.getElementById("sound-levels-actions");
const SIDE_LABELS = { input: "Receive", output: "Transmit" };
let levels = [];
let levelsDevices = "";

function soundLevelText(level) {
  if (level.db == null) return `${Math.round((100 * (level.value - level.min)) / (level.max - level.min || 1))}%`;
  return `${level.db > 0 ? "+" : ""}${level.db.toFixed(1)} dB`;
}

function renderLevels(body) {
  levels = body.levels;
  const viewer = document.body.dataset.role === "viewer";
  levelsList.replaceChildren(
    ...levels.map((level, index) => {
      const row = document.createElement("div");
      row.className = "sound-level";
      const id = `sound-level-${index}`;
      row.innerHTML = `
        <label for="${id}">${SIDE_LABELS[level.side]} <span class="muted small">${escapeHtml(level.control)}, card ${level.card}</span>
          ${level.saved ? "" : '<span class="tag tag-warn">not saved</span>'}</label>
        <input type="range" id="${id}" step="1" />
        <span class="sound-level-value small">${soundLevelText(level)}</span>`;
      const slider = row.querySelector("input");
      Object.assign(slider, { min: level.min, max: level.max, value: level.value, disabled: viewer });
      slider.addEventListener("change", () => setLevel(level, Number(slider.value)));
      return row;
    }),
  );
  levelsNotes.hidden = !body.notes.length;
  levelsNotes.textContent = body.notes.join(" ");
  levelsActions.hidden = viewer || levels.every((level) => level.saved);
}

async function setLevel(level, value) {
  try {
    renderLevels(await api("/api/audio/levels", { method: "PUT", json: { side: level.side, control: level.control, value } }));
  } catch (error) {
    toastError(error);
    loadLevels();
  }
}

async function loadLevels() {
  const config = store.state.config;
  levelsDevices = `${config?.audio_input_device}\n${config?.audio_output_device}`;
  try {
    renderLevels(await api("/api/audio/levels"));
  } catch (error) {
    renderLevels({ levels: [], notes: [error.message] });
  }
}

async function saveCurrentLevels(button) {
  await withBusy(button, async () => {
    try {
      for (const level of levels.filter((l) => !l.saved)) {
        await api("/api/audio/levels", { method: "PUT", json: { side: level.side, control: level.control, value: level.value } });
      }
      toast("Levels saved");
    } catch (error) {
      toastError(error);
    }
  });
  loadLevels();
}

const glitchesBody = document.getElementById("glitches-tbody");
const glitchesEmpty = document.getElementById("glitches-empty");

function renderGlitches(glitches) {
  glitchesEmpty.hidden = glitches.length > 0;
  glitchesBody.innerHTML = glitches
    .map(
      (g) => `<tr>
        <td>${new Date(g.at * 1000).toLocaleTimeString()}</td>
        <td>${GLITCH_LABELS[g.counter]}</td>
        <td>${g.count}</td>
        <td>${escapeHtml(g.state.replaceAll("_", " "))}${g.transmitting ? ", transmitting" : ""}</td>
      </tr>`,
    )
    .join("");
}

const openingsBody = document.getElementById("openings-tbody");
const openingsEmpty = document.getElementById("openings-empty");
const OPENINGS_POLL_MS = 3000;

function openingNotes(opening) {
  const notes = [];
  if (opening.dtmf_digits) notes.push(`DTMF ${opening.dtmf_digits}`);
  if (opening.after_tx === 0) notes.push("while transmitting");
  else if (opening.after_tx != null) notes.push(`${opening.after_tx.toFixed(1)} s after unkey`);
  return notes.join(" · ");
}

function renderOpenings(openings) {
  openingsEmpty.hidden = openings.length > 0;
  openingsBody.innerHTML = openings
    .map((o, i) => {
      const older = openings[i + 1];
      const gap = older ? `${(o.started_at - older.started_at).toFixed(1)} s` : "";
      const strongest = o.strongest_hz == null ? "–" : `${o.strongest_hz.toFixed(0)} Hz · ${Math.round(o.tone_share * 100)}%`;
      return `<tr>
        <td>${new Date(o.started_at * 1000).toLocaleTimeString()}</td>
        <td>${o.duration.toFixed(1)} s</td>
        <td>${gap}</td>
        <td>${o.open_db.toFixed(0)} / ${o.peak_db.toFixed(0)} dBFS</td>
        <td>${strongest}</td>
        <td>${o.ctcss_hz == null ? "–" : `${o.ctcss_hz} Hz`}</td>
        <td>${escapeHtml(openingNotes(o))}</td>
      </tr>`;
    })
    .join("");
}

async function pollOpenings() {
  if (currentView === "audio" && !document.hidden) {
    try {
      const [openings, glitches] = await Promise.all([api("/api/audio/openings"), api("/api/audio/glitches")]);
      renderOpenings(openings);
      renderGlitches(glitches);
    } catch {
      // The next poll will retry.
    }
  }
  setTimeout(pollOpenings, OPENINGS_POLL_MS);
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
    const config = store.state.config;
    if (currentView === "audio" && `${config?.audio_input_device}\n${config?.audio_output_device}` !== levelsDevices) {
      loadLevels();
    }
  });
  document.getElementById("sound-levels-save").addEventListener("click", (event) => saveCurrentLevels(event.currentTarget));
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
    if (detail === "audio") {
      loadDevices();
      loadLevels();
    }
  });
  if (currentView === "audio") {
    loadDevices();
    loadLevels();
  }
  poll();
  pollOpenings();
}
