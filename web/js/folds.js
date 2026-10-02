import { store } from "./store.js";
import { formatDuration } from "./ui.js";

// Folds are <details class="fold"> groups of fields. One with data-summary
// shows a line describing its current values, so it can stay closed.

const optionText = (select) => select.selectedOptions[0]?.textContent.trim() ?? select.value;
const signed = (value) => (Number(value) > 0 ? `+${Number(value)}` : `${Number(value)}`);
const tone = (value) => `${Number(value).toFixed(1)} Hz`;

const SUMMARIES = {
  "audio-devices": (f) => `${optionText(f.audio_input_device)} in, ${optionText(f.audio_output_device)} out`,
  "audio-cos": (f) => {
    const source = f.cos_source.value;
    if (source === "vox") return `Audio level, opens at ${f.vox_threshold_db.value} dBFS`;
    if (source === "ctcss") return "CTCSS tone present";
    const input = source === "gpio" ? `GPIO${f.cos_gpio_pin.value}` : "CM108 COS input";
    return `${input}, active ${f.cos_polarity.value}`;
  },
  "audio-ptt": (f) =>
    f.ptt_output.value === "gpio" ? `GPIO${f.ptt_gpio_pin.value}, active ${f.ptt_polarity.value}` : "CM108 interface",
  "audio-tx": (f) =>
    `Gain ${signed(f.tx_gain_db.value)} dB, ${f.tx_ctcss_hz.value ? `CTCSS ${tone(f.tx_ctcss_hz.value)}` : "no CTCSS tone"}`,

  "cw-id": (f) =>
    `${f.id_mode.value === "voice" ? "Not used with voice IDs · " : ""}${f.cw_wpm.value} WPM, ${f.cw_tone_hz.value} Hz`,
  "voice-id": (f) =>
    `${f.id_mode.value === "cw" ? "Not used with CW IDs · " : ""}“${f.voice_id_text.value.trim() || "{callsign} repeater"}”` +
    (f.id_phonetic.checked ? ", phonetic" : ""),

  "aprs-position": (f) =>
    f.aprs_lat.value && f.aprs_lon.value
      ? `${f.aprs_lat.value}, ${f.aprs_lon.value} · ${optionText(f.aprs_symbol)}`
      : "Not set: sends a status beacon",
  "aprs-frequency": (f) => {
    if (!f.aprs_frequency_mhz.value) return "Left out of the beacon";
    const parts = [`${Number(f.aprs_frequency_mhz.value).toFixed(3)} MHz`];
    if (f.aprs_offset_mhz.value) parts.push(`offset ${signed(f.aprs_offset_mhz.value)}`);
    if (f.aprs_tone_hz.value) parts.push(tone(f.aprs_tone_hz.value));
    return parts.join(", ");
  },
  "aprs-server": (f) => `${f.aprs_server.value}:${f.aprs_port.value}`,

  "patch-numbers": (f) => {
    const allowed = f.autopatch_allowed.value.trim();
    const blocked = f.autopatch_blocked.value.trim();
    return `${allowed ? `Allowed ${allowed}` : "Nothing allowed"}${blocked ? ` · blocked ${blocked}` : ""}`;
  },
  "patch-length": (f) =>
    `Calls up to ${formatDuration(Number(f.autopatch_max_call_seconds.value))}, ring ${f.autopatch_ring_seconds.value}s`,
  "patch-dialing": (f) => {
    const callerId = f.autopatch_caller_id.value.trim();
    return `${f.autopatch_dial_string.value.trim()}${callerId ? ` · caller ID ${callerId}` : ""}`;
  },
  "trunk-more": (f) => {
    const login = f.auth_username.value.trim();
    return `${f.transport.value.toUpperCase()}${f.port.value ? ` port ${f.port.value}` : ""}${login ? `, login ${login}` : ""}`;
  },
};

export function refreshFolds() {
  for (const fold of document.querySelectorAll("details.fold[data-summary]")) {
    const describe = SUMMARIES[fold.dataset.summary];
    const form = fold.closest("form");
    if (describe && form) fold.querySelector(".fold-hint").textContent = describe(form.elements);
  }
}

export function initFolds() {
  // A closed fold would hide the field the browser wants to point at.
  document.addEventListener("invalid", (event) => event.target.closest("details")?.setAttribute("open", ""), true);
  document.addEventListener("input", refreshFolds);
  document.addEventListener("change", refreshFolds);
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-reset]")) setTimeout(refreshFolds);
  });
  store.addEventListener("config", refreshFolds);
  refreshFolds();
}
