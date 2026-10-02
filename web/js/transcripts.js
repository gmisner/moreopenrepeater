import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toastError } from "./ui.js";

const form = document.getElementById("transcription-form");
const engineSelect = form.elements.transcription_engine;
const statusNote = document.getElementById("transcription-status");

function showFields() {
  for (const el of form.querySelectorAll("[data-transcription]")) {
    el.hidden = el.dataset.transcription !== engineSelect.value;
  }
}

function describe(status) {
  if (status.engine === "vosk") {
    if (!status.vosk_installed) {
      return "Vosk isn't installed. On the controller, run <code>sudo /opt/moreopenrepeater/.venv/bin/pip install vosk</code> and restart.";
    }
    if (!status.vosk_model) {
      return `No Vosk model yet. Unpack one (vosk-model-small-en-us-0.15 from alphacephei.com/vosk/models) into <code>${escapeHtml(status.vosk_model_dir)}</code>.`;
    }
  }
  if (status.engine === "openai" && !status.api_key_set && form.elements.transcription_url.value.includes("api.openai.com")) {
    return "OpenAI needs an API key: put it in <code>MOREOPENREPEATER_TRANSCRIPTION_API_KEY</code> in <code>/etc/moreopenrepeater/env</code> and restart.";
  }
  if (status.engine === "off") return "";
  const parts = [];
  if (status.last_error) parts.push(`Last try failed: ${escapeHtml(status.last_error)}.`);
  parts.push(status.pending ? `${status.pending} waiting to be transcribed.` : "All caught up.");
  if (status.api_key_set && status.engine === "openai") parts.push("An API key is set.");
  return parts.join(" ");
}

export async function loadTranscriptionStatus() {
  const status = await api("/api/transcription");
  statusNote.innerHTML = describe(status);
}

export function initTranscripts() {
  engineSelect.addEventListener("change", showFields);
  store.addEventListener("config", () => {
    showFields();
    if (currentView === "activity") loadTranscriptionStatus().catch(toastError);
  });
  router.addEventListener("change", ({ detail }) => {
    if (detail === "activity") loadTranscriptionStatus().catch(toastError);
  });
  showFields();
}
