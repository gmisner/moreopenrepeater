import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { store } from "./store.js";
import { toast, toastError, withBusy } from "./ui.js";

const pageLink = document.getElementById("public-page-link");
const pageUrl = document.getElementById("public-page-url");
const form = document.getElementById("stream-form");
const stateTag = document.getElementById("stream-state");
const detail = document.getElementById("stream-detail");
const passwordNote = document.getElementById("stream-password-note");

const STATE_LABELS = { off: "Off", connecting: "Connecting", streaming: "Streaming", retrying: "Retrying", error: "Problem" };
const STATE_TAGS = { streaming: "tag-on", retrying: "tag-warn", error: "tag-danger" };
const FIELDS = ["host", "port", "mount", "username", "name", "description", "genre", "bitrate"];
const STATUS_POLL_MS = 3000;

let poller = null;

function renderLink(config) {
  pageLink.hidden = config.public_page_mode === "off";
  pageUrl.textContent = `${location.origin}/listen`;
}

function renderStatus(status) {
  stateTag.textContent = STATE_LABELS[status.state] ?? status.state;
  stateTag.className = `tag ${STATE_TAGS[status.state] ?? ""}`;
  detail.textContent = status.detail;
}

function populate(stream) {
  form.elements.enabled.checked = stream.enabled;
  form.elements.legacy_icecast.checked = stream.legacy_icecast;
  for (const field of FIELDS) form.elements[field].value = stream[field];
  form.elements.password.value = "";
  form.elements.password.placeholder = stream.password_set ? "(saved; leave blank to keep)" : "";
  passwordNote.textContent = stream.password_set
    ? "Saved on this controller only, out of backups."
    : "Kept on this controller only, out of backups.";
  renderStatus(stream.status);
}

async function refreshStatus() {
  renderStatus((await api("/api/stream")).status);
}

async function load() {
  if (session?.role !== "admin") return;
  populate(await api("/api/stream"));
  clearInterval(poller);
  poller = setInterval(() => {
    if (currentView === "public") refreshStatus().catch(() => {});
    else clearInterval(poller);
  }, STATUS_POLL_MS);
}

async function save() {
  const els = form.elements;
  const body = Object.fromEntries(FIELDS.map((field) => [field, els[field].value.trim()]));
  body.port = Number(body.port);
  body.bitrate = Number(body.bitrate);
  body.enabled = els.enabled.checked;
  body.legacy_icecast = els.legacy_icecast.checked;
  if (els.password.value) body.password = els.password.value;
  try {
    populate(await api("/api/stream", { method: "PUT", json: body }));
    toast("Saved the feed settings");
  } catch (error) {
    toastError(error);
  }
}

export function initPublic() {
  store.addEventListener("config", ({ detail: config }) => renderLink(config));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    withBusy(form.querySelector("button[type=submit]"), save);
  });
  router.addEventListener("change", ({ detail: view }) => {
    if (view === "public") load().catch(toastError);
  });
  if (currentView === "public") load().catch(toastError);
}
