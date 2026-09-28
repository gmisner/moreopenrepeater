import { api } from "./api.js";
import { initLocationInputs } from "./location.js";
import { playClip } from "./player.js";
import { store } from "./store.js";
import { escapeHtml, formatDuration, toast, toastError, withBusy } from "./ui.js";

// Optional fields the API only resets to None through an explicit
// `clear_<field>: true`, since an omitted field means "leave unchanged".
const CLEARABLE_FIELDS = new Set([
  "require_ctcss_hz",
  "courtesy_tone_asset_id",
  "id_asset_id",
  "timeout_tone_asset_id",
  "aprs_lat",
  "aprs_lon",
  "wx_lat",
  "wx_lon",
  "tx_ctcss_hz",
]);

const configForms = [...document.querySelectorAll("[data-config-form]")];

function namedFields(form) {
  return [...form.elements].filter((el) => el.name);
}

function collect(form) {
  const body = {};
  for (const el of namedFields(form)) {
    if (el.type === "checkbox") {
      body[el.name] = el.checked;
      continue;
    }
    const value = el.value.trim();
    if (value === "") {
      if (CLEARABLE_FIELDS.has(el.name)) body[`clear_${el.name}`] = true;
      else if (el.type !== "number") body[el.name] = "";
      continue;
    }
    body[el.name] = el.type === "number" ? Number(value) : value;
  }
  return body;
}

function populate(form, config) {
  for (const el of namedFields(form)) {
    if (!(el.name in config)) continue;
    if (el.type === "checkbox") el.checked = Boolean(config[el.name]);
    else el.value = config[el.name] ?? "";
  }
  form.classList.remove("dirty");
}

function populateAll(config) {
  for (const form of configForms) {
    if (!form.classList.contains("dirty")) populate(form, config);
  }
}

function renderAssetSelects(assets) {
  for (const select of document.querySelectorAll("[data-asset-select]")) {
    const current = store.state.config?.[select.name] ?? select.value;
    select.innerHTML = '<option value="">(built-in)</option>';
    for (const asset of assets.filter((a) => a.kind === select.dataset.assetSelect)) {
      const option = document.createElement("option");
      option.value = asset.id;
      option.textContent = asset.filename;
      select.appendChild(option);
    }
    select.value = current ?? "";
  }
}

function kv(entries) {
  return entries.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${v}</dd>`).join("");
}

function badge(on, onText = "enabled", offText = "disabled") {
  return `<span class="tag ${on ? "tag-on" : ""}">${on ? onText : offText}</span>`;
}

function renderSummaries(config) {
  document.getElementById("station-callsign").textContent = config.callsign || "(no callsign)";
  document.getElementById("station-summary").innerHTML = kv([
    ["Callsign", escapeHtml(config.callsign || "not set")],
    ["ID mode", escapeHtml(config.id_mode.toUpperCase())],
    ["ID every", formatDuration(config.id_interval)],
    ["Timeout", formatDuration(config.tot_duration)],
    ["Hang time", formatDuration(config.hang_time)],
  ]);
  const { macros, assets } = store.state;
  document.getElementById("feature-summary").innerHTML = kv([
    ["CTCSS access", config.require_ctcss_hz ? `${config.require_ctcss_hz} Hz` : badge(false, "", "carrier")],
    ["APRS", badge(config.aprs_enabled)],
    ["Weather alerts", badge(config.wx_alerts_enabled)],
    ["DTMF macros", String(macros.length)],
    ["Announcements", String(store.state.announcements?.length ?? 0)],
    ["Audio clips", String(assets.length)],
  ]);
}

async function save(form) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const config = await api("/api/config", { method: "PUT", json: collect(form) });
    form.classList.remove("dirty");
    store.set("config", config);
    toast("Settings saved");
  } catch (error) {
    toastError(error);
  } finally {
    button.disabled = false;
  }
}

// Every settings form's current (possibly unsaved) values, so a preview
// sounds like what would go out on air after pressing Save.
export function unsavedConfig() {
  return Object.assign({}, ...configForms.map(collect));
}

function initPreviews() {
  for (const button of document.querySelectorAll("[data-preview-clip]")) {
    button.addEventListener("click", () =>
      withBusy(button, async () => {
        try {
          await playClip(button.dataset.previewClip, unsavedConfig());
        } catch (error) {
          toastError(error);
        }
      }),
    );
  }
}

async function loadTTSInfo() {
  try {
    const { engine } = await api("/api/audio/tts");
    document.getElementById("tts-engine").textContent = engine ? `text-to-speech (${engine})` : "CW — no text-to-speech engine installed";
  } catch {
    // Informational only.
  }
}

export async function loadConfig() {
  store.set("config", await api("/api/config"));
}

export function initConfig() {
  for (const form of configForms) {
    form.addEventListener("input", () => form.classList.add("dirty"));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      save(form);
    });
    form.querySelector("[data-reset]")?.addEventListener("click", () => {
      if (store.state.config) populate(form, store.state.config);
    });
  }
  store.addEventListener("config", ({ detail }) => {
    populateAll(detail);
    renderSummaries(detail);
  });
  store.addEventListener("assets", ({ detail }) => {
    renderAssetSelects(detail);
    if (store.state.config) renderSummaries(store.state.config);
  });
  for (const key of ["macros", "announcements"]) {
    store.addEventListener(key, () => {
      if (store.state.config) renderSummaries(store.state.config);
    });
  }
  window.addEventListener("beforeunload", (event) => {
    if (configForms.some((f) => f.classList.contains("dirty"))) event.preventDefault();
  });
  initPreviews();
  initLocationInputs(document.getElementById("aprs-locate").form, "aprs_lat", "aprs_lon");
  initLocationInputs(document.getElementById("wx-locate").form, "wx_lat", "wx_lon");
  loadTTSInfo();
}
