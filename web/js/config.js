import { api } from "./api.js";
import { initBeaconPreview } from "./beacon.js";
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
  "aprs_frequency_mhz",
  "aprs_offset_mhz",
  "aprs_tone_hz",
]);

const configForms = [...document.querySelectorAll("[data-config-form]")];

// Saving sends only the fields edited since the form was filled in, so it
// can't undo settings changed elsewhere (another browser, the API) in the
// meantime; `loadedFrom` spots when an edited field was one of those.
const edited = new WeakMap();
const loadedFrom = new WeakMap();

function editedFields(form) {
  if (!edited.has(form)) edited.set(form, new Set());
  return edited.get(form);
}

function namedFields(form) {
  return [...form.elements].filter((el) => el.name);
}

function collect(form, names = null) {
  const body = {};
  for (const el of namedFields(form)) {
    if (names && !names.has(el.name)) continue;
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
    else {
      const value = config[el.name] ?? "";
      // Keep values set through the API (e.g. an overlay symbol) instead of blanking them.
      if ("allowCustom" in el.dataset && value && ![...el.options].some((o) => o.value === value)) {
        el.add(new Option(value, value));
      }
      el.value = value;
    }
  }
  form.classList.remove("dirty");
  edited.set(form, new Set());
  loadedFrom.set(form, config);
}

function fieldLabel(form, name) {
  const el = namedFields(form).find((field) => field.name === name);
  const label = el?.closest("label");
  if (!label) return name;
  const text = [...label.childNodes]
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent)
    .join(" ")
    .trim();
  return text || label.querySelector("span:last-of-type")?.textContent.trim() || name;
}

// Edited fields that someone else has changed since this form was filled in.
function conflicts(form, names, current) {
  const base = loadedFrom.get(form);
  if (!base) return [];
  return [...names].filter((name) => name in current && JSON.stringify(current[name]) !== JSON.stringify(base[name]));
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
    ["ID every", `${formatDuration(config.id_interval)}${config.idle_id ? "" : " in use"}`],
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
    const names = editedFields(form);
    if (names.size === 0) {
      form.classList.remove("dirty");
      toast("No changes to save");
      return;
    }
    const current = await api("/api/config");
    const changed = conflicts(form, names, current);
    if (changed.length) {
      const labels = changed.map((name) => fieldLabel(form, name)).join(", ");
      const question =
        `${labels} ${changed.length === 1 ? "was" : "were"} changed elsewhere since this page loaded. ` +
        `Save your ${changed.length === 1 ? "value" : "values"} over ${changed.length === 1 ? "it" : "them"}?`;
      if (!confirm(question)) {
        store.set("config", current);
        return;
      }
    }
    const config = await api("/api/config", { method: "PUT", json: collect(form, names) });
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
  return Object.assign({}, ...configForms.map((form) => collect(form)));
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
    form.addEventListener("input", (event) => {
      form.classList.add("dirty");
      if (event.target.name) editedFields(form).add(event.target.name);
    });
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
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && store.state.config) loadConfig().catch(() => {});
  });
  window.addEventListener("beforeunload", (event) => {
    if (configForms.some((f) => f.classList.contains("dirty"))) event.preventDefault();
  });
  initPreviews();
  initLocationInputs(document.getElementById("aprs-locate").form, "aprs_lat", "aprs_lon");
  initBeaconPreview(document.getElementById("aprs-locate").form, document.getElementById("aprs-beacon-preview"));
  initLocationInputs(document.getElementById("wx-locate").form, "wx_lat", "wx_lon");
  loadTTSInfo();
}
