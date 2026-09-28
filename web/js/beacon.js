import { store } from "./store.js";

// APRS101: position comments longer than this get cut off on many radios.
const MAX_COMMENT = 43;

function number(form, name) {
  const value = form.elements[name].value.trim();
  return value === "" ? null : Number(value);
}

// Same format as the server's format_frequency_comment (aprs.org freq spec).
export function frequencyComment(frequency, tone, offset) {
  const parts = [frequency < 1000 ? `${frequency.toFixed(3).padStart(7, "0")}MHz` : `${frequency.toFixed(2)}MHz`];
  if (tone !== null) parts.push(`T${String(Math.trunc(tone)).padStart(3, "0")}`);
  if (offset !== null) {
    if (tone === null) parts.push("Toff");
    parts.push(`${offset > 0 ? "+" : "-"}${String(Math.round(Math.abs(offset) * 100)).padStart(3, "0")}`);
  }
  return parts.join(" ");
}

function render(form, preview) {
  const required = store.state.config?.require_ctcss_hz ?? null;
  const toneField = form.elements.aprs_tone_hz;
  toneField.placeholder = required ? `${required} (required CTCSS)` : "none";

  let comment = form.elements.aprs_comment.value.trim();
  const frequency = number(form, "aprs_frequency_mhz");
  if (frequency !== null) {
    const tone = number(form, "aprs_tone_hz") ?? required;
    comment = `${frequencyComment(frequency, tone, number(form, "aprs_offset_mhz"))} ${comment}`.trim();
  }
  preview.replaceChildren();
  if (!comment) return;
  const code = document.createElement("code");
  code.textContent = comment;
  preview.append("Sends: ", code);
  if (comment.length > MAX_COMMENT) {
    preview.append(` — ${comment.length} characters; radios may only show the first ${MAX_COMMENT}.`);
  }
}

export function initBeaconPreview(form, preview) {
  const update = () => render(form, preview);
  form.addEventListener("input", update);
  form.querySelector("[data-reset]")?.addEventListener("click", () => queueMicrotask(update));
  store.addEventListener("config", () => queueMicrotask(update));
}
