import { api } from "./api.js";
import { session } from "./session.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const card = document.getElementById("echolink-card");
const statusLine = document.getElementById("echolink-status");
const pill = document.getElementById("echolink-pill");
const form = document.getElementById("echolink-form");
const saveButton = document.getElementById("echolink-save");
const offButton = document.getElementById("echolink-off");

let latest = null;
let node = null;
let filled = false;
let edited = false;

function describe(status) {
  if (status.error) return { text: escapeHtml(status.error), pill: ["Error", "warn"] };
  const settings = status.settings;
  if (!status.enabled) {
    const next = !node
      ? "Choose the repeater's node above first."
      : session?.role === "admin"
        ? "Fill in the station's details to turn it on."
        : "An admin can turn it on here.";
    return { text: `EchoLink is off. ${next}`, pill: ["Off", "off"] };
  }
  const station = `<strong>${escapeHtml(settings.callsign || "?")}</strong> (node ${escapeHtml(settings.node_number || "?")})`;
  if (status.loaded === false) {
    return {
      text: `EchoLink is set up as ${station} but Asterisk hasn't loaded its driver. Check the Asterisk log (<code>sudo journalctl -u asterisk</code>).`,
      pill: ["Not loaded", "warn"],
    };
  }
  if (node && settings.astnode && settings.astnode !== node) {
    return { text: `EchoLink is on as ${station}, but connects to node ${escapeHtml(settings.astnode)}. Save to move it to node ${escapeHtml(node)}.`, pill: ["On", "warn"] };
  }
  return { text: `EchoLink is on as ${station}. EchoLink users connect to it like any AllStar link.`, pill: ["On", "on"] };
}

function defaults() {
  const config = store.state.config;
  if (!config) return {};
  const tone = config.aprs_tone_hz ?? config.require_ctcss_hz;
  return {
    callsign: config.callsign ? `${config.callsign.toUpperCase()}-R` : "",
    lat: config.aprs_lat ?? "",
    lon: config.aprs_lon ?? "",
    frequency_mhz: config.aprs_frequency_mhz ?? "",
    tone_hz: tone ?? "",
  };
}

function fill(settings) {
  const fallback = defaults();
  const pick = (key, empty) => (settings[key] === empty || settings[key] == null ? fallback[key] ?? "" : settings[key]);
  for (const key of ["callsign", "name", "location", "email", "node_number"]) form.elements[key].value = pick(key, "");
  for (const key of ["lat", "lon", "frequency_mhz", "tone_hz"]) form.elements[key].value = pick(key, 0);
  form.elements.max_stations.value = settings.max_stations || 20;
  form.elements.password.value = "";
  form.elements.password.required = !settings.has_password;
  form.elements.password.placeholder = settings.has_password ? "Saved (leave blank to keep it)" : "";
  filled = true;
  edited = false;
}

function render(status) {
  latest = status;
  card.hidden = !status.available && !status.error;
  if (card.hidden) return;
  const view = describe(status);
  statusLine.innerHTML = view.text;
  pill.hidden = false;
  pill.textContent = view.pill[0];
  pill.className = `pill pill-${view.pill[1]}`;
  form.hidden = session?.role !== "admin" || !status.settings;
  offButton.hidden = !status.enabled;
  saveButton.disabled = !node;
  saveButton.textContent = status.enabled ? "Save" : "Save and turn on";
  if (!form.hidden && !filled) fill(status.settings);
}

export async function refreshEchoLink() {
  render(await api("/api/allstar/echolink"));
}

export function setEchoLinkNode(number) {
  if (node === number) return;
  node = number;
  if (latest) render(latest);
}

function number(name) {
  const value = form.elements[name].value;
  return value === "" ? 0 : Number(value);
}

async function save(event) {
  event.preventDefault();
  const body = {
    callsign: form.elements.callsign.value.trim().toUpperCase(),
    password: form.elements.password.value,
    name: form.elements.name.value.trim(),
    location: form.elements.location.value.trim(),
    email: form.elements.email.value.trim(),
    node_number: form.elements.node_number.value.trim(),
    lat: number("lat"),
    lon: number("lon"),
    frequency_mhz: number("frequency_mhz"),
    tone_hz: number("tone_hz"),
    max_stations: number("max_stations"),
  };
  if (!confirm(`Save EchoLink as ${body.callsign}? Asterisk restarts, dropping links for a few seconds.`)) return;
  await withBusy(saveButton, async () => {
    try {
      const status = await api("/api/allstar/echolink", { method: "PUT", json: body });
      filled = false;
      render(status);
      toast(status.loaded === false ? "Saved, but Asterisk didn't load EchoLink" : `EchoLink is on as ${body.callsign}`);
    } catch (error) {
      toastError(error);
    }
  });
}

async function turnOff() {
  if (!confirm("Turn off EchoLink? Asterisk restarts, and EchoLink stations are disconnected. The details are kept.")) return;
  await withBusy(offButton, async () => {
    try {
      render(await api("/api/allstar/echolink", { method: "DELETE" }));
      toast("EchoLink turned off");
    } catch (error) {
      toastError(error);
    }
  });
}

export function initEchoLink() {
  form.addEventListener("submit", save);
  offButton.addEventListener("click", turnOff);
  form.addEventListener("input", () => (edited = true));
  store.addEventListener("config", () => {
    if (latest?.settings && !edited) {
      filled = false;
      render(latest);
    }
  });
}
