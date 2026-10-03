import { unsavedConfig } from "./config.js";
import { playClip } from "./player.js";
import { store } from "./store.js";
import { toastError, withBusy } from "./ui.js";

// Mirrors playout.tones.COURTESY_TONE_STYLES.
export const TONE_STYLES = [
  ["beep", "Beep"],
  ["high_low", "High–low"],
  ["low_high", "Low–high"],
  ["bee_boo", "Bee-boo"],
  ["triple", "Triple beep"],
  ["chirp", "Chirp"],
  ["bumblebee", "Bumblebee"],
  ["up_run", "Three tones up"],
  ["down_run", "Three tones down"],
  ["bonk", "Bonk"],
  ["cw_k", "CW “K”"],
  ["cw_r", "CW “R”"],
  ["cw_t", "CW “T”"],
  ["custom", "Custom"],
];
const SEGMENTS = 4;

const hidden = document.querySelector('input[name="courtesy_tone_custom"]');
const rows = document.getElementById("tone-segments");
const previewButton = document.getElementById("tone-custom-preview");

function parse(text) {
  return (text ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.split(":").map(Number));
}

export function customToneSummary(text) {
  const parts = parse(text);
  if (!parts.length) return "Not set";
  const ms = parts.reduce((sum, [, length]) => sum + length, 0);
  const tones = parts.filter(([hz]) => hz).map(([hz]) => `${hz}`);
  return `${tones.join(", ")} Hz, ${ms} ms`;
}

function renderRows() {
  const parts = parse(hidden.value);
  rows.innerHTML = Array.from({ length: SEGMENTS }, (_, i) => {
    const [hz, ms] = parts[i] ?? ["", ""];
    return `<div class="tone-segment">
      <span class="muted small">Part ${i + 1}</span>
      <span class="input-unit"><input type="number" min="0" max="3000" step="10" data-hz value="${hz}" aria-label="Part ${i + 1} pitch" /><span>Hz</span></span>
      <span class="input-unit"><input type="number" min="20" max="1000" step="10" data-ms value="${ms}" aria-label="Part ${i + 1} length" /><span>ms</span></span>
    </div>`;
  }).join("");
}

function writeHidden() {
  const parts = [...rows.querySelectorAll(".tone-segment")]
    .map((row) => [row.querySelector("[data-hz]").value.trim(), row.querySelector("[data-ms]").value.trim()])
    .filter(([hz, ms]) => hz !== "" && ms !== "")
    .map(([hz, ms]) => `${Number(hz)}:${Number(ms)}`);
  hidden.value = parts.join(" ");
  hidden.dispatchEvent(new Event("input", { bubbles: true }));
}

function preview() {
  return withBusy(previewButton, async () => {
    try {
      const config = { ...unsavedConfig(), courtesy_tone_style: "custom", clear_courtesy_tone_asset_id: true };
      await playClip("courtesy_tone", config);
    } catch (error) {
      toastError(error);
    }
  });
}

export function initTones() {
  const options = TONE_STYLES.map(([value, label]) => new Option(label, value));
  for (const select of document.querySelectorAll("select[data-tone-styles]")) {
    select.append(...options.map((option) => option.cloneNode(true)));
  }
  rows.addEventListener("input", (event) => {
    event.stopPropagation();
    writeHidden();
  });
  previewButton.addEventListener("click", preview);
  // After config.js has filled the form in (its listener was added first).
  store.addEventListener("config", () => {
    if (!hidden.form.classList.contains("dirty")) renderRows();
  });
  hidden.form.querySelector("[data-reset]").addEventListener("click", () => setTimeout(renderRows));
  renderRows();
}
