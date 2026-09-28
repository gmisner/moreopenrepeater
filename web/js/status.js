import { api } from "./api.js";
import { loadConfig } from "./config.js";
import { store } from "./store.js";
import { escapeHtml } from "./ui.js";

const STATE_DESCRIPTIONS = {
  idle: "Standing by — no signal",
  receiving: "Repeating a local signal",
  courtesy_tone: "Playing the courtesy tone",
  hang_time: "Hang time — transmitter still keyed",
  timeout: "Timed out — waiting for the user to unkey",
  transmitting_id: "Transmitting station ID",
  announcing: "Playing a scheduled announcement",
  patch: "Autopatch phone call in progress",
};
const MAX_ACTIVITY = 50;

const connectionIndicator = document.getElementById("connection-indicator");
const stateValue = document.getElementById("state-value");
const stateDescription = document.getElementById("state-description");
const linkedNodesValue = document.getElementById("linked-nodes-value");
const activityList = document.getElementById("activity-list");
const activityEmpty = document.getElementById("activity-empty");

let previous = null;

function setIndicator(id, on, valueText) {
  const el = document.getElementById(id);
  el.classList.toggle("on", on);
  el.querySelector(".indicator-value").textContent = valueText;
}

function setPill(id, on, label) {
  const el = document.getElementById(id);
  el.textContent = label;
  el.className = "pill " + (on ? "pill-on" : "pill-off");
}

function setStateBadge(el, state) {
  el.textContent = state.replace(/_/g, " ");
  el.className = `state-badge state-${state}`;
}

function addActivity(text, kind) {
  const item = document.createElement("li");
  item.className = `activity-${kind}`;
  item.innerHTML = `<time>${new Date().toLocaleTimeString()}</time><span>${escapeHtml(text)}</span>`;
  activityList.prepend(item);
  while (activityList.children.length > MAX_ACTIVITY) activityList.lastElementChild.remove();
  activityEmpty.hidden = true;
}

function recordChanges(prev, next) {
  if (!prev) return;
  if (prev.state !== next.state) addActivity(`State: ${prev.state} → ${next.state}`, next.state);
  if (prev.cos_active !== next.cos_active) addActivity(next.cos_active ? "Carrier detected (COS on)" : "Carrier dropped (COS off)", "cos");
  if (prev.ptt_active !== next.ptt_active) addActivity(next.ptt_active ? "Transmitter keyed (PTT on)" : "Transmitter unkeyed (PTT off)", "ptt");
  if (prev.last_clip !== next.last_clip && next.last_clip) addActivity(`Played clip: ${next.last_clip}`, "clip");
  if (prev.transmitter_enabled !== next.transmitter_enabled) {
    addActivity(next.transmitter_enabled ? "Transmitter turned on" : "Transmitter turned off", "ptt");
  }
  const before = new Set(prev.linked_nodes);
  const after = new Set(next.linked_nodes);
  for (const node of after) if (!before.has(node)) addActivity(`Node ${node} linked`, "link");
  for (const node of before) if (!after.has(node)) addActivity(`Node ${node} unlinked`, "link");
}

export function applyStatus(status) {
  recordChanges(previous, status);
  // Switched over the air: refresh the settings forms too.
  if (previous && previous.transmitter_enabled !== status.transmitter_enabled) loadConfig().catch(() => {});
  previous = status;
  document.getElementById("tx-disabled-tag").hidden = status.transmitter_enabled;

  setStateBadge(stateValue, status.state);
  stateDescription.textContent = STATE_DESCRIPTIONS[status.state] ?? "";
  setIndicator("ptt-indicator", status.ptt_active, status.ptt_active ? "TX" : "off");
  setIndicator("cos-indicator", status.cos_active, status.cos_active ? "RX" : "off");
  setIndicator("ctcss-indicator", status.ctcss_hz !== null, status.ctcss_hz !== null ? `${status.ctcss_hz} Hz` : "none");

  linkedNodesValue.innerHTML = status.linked_nodes.length
    ? status.linked_nodes.map((n) => `<span class="chip">${escapeHtml(n)}</span>`).join("")
    : '<span class="muted">(none)</span>';

  setStateBadge(document.getElementById("sim-state"), status.state);
  setPill("sim-ptt", status.ptt_active, status.ptt_active ? "PTT ON" : "PTT off");
  setPill("sim-cos", status.cos_active, status.cos_active ? "COS ON" : "COS off");

  store.set("status", status);
}

function setConnection(text, className) {
  connectionIndicator.textContent = text;
  connectionIndicator.className = `pill ${className}`;
}

export function connectStatusSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws/status`);
  let opened = false;

  ws.onopen = () => {
    opened = true;
    setConnection("live", "pill-on");
  };
  ws.onmessage = (event) => applyStatus(JSON.parse(event.data));
  ws.onerror = () => ws.close();
  ws.onclose = async () => {
    setConnection("reconnecting…", "pill-warn");
    if (!opened) {
      // A handshake rejected before opening is most likely an expired
      // session -- api() redirects to the login page on a 401.
      try {
        const session = await api("/api/session");
        if (session.auth_required && !session.authenticated) {
          location.replace("/login");
          return;
        }
      } catch {
        setConnection("offline", "pill-off");
      }
    }
    setTimeout(connectStatusSocket, opened ? 1000 : 3000);
  };
}

export function initStatus() {
  document.getElementById("activity-clear").addEventListener("click", () => {
    activityList.innerHTML = "";
    activityEmpty.hidden = false;
  });
}
