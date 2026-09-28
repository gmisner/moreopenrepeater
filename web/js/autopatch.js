import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { escapeHtml, toastError, withBusy } from "./ui.js";

const REFRESH_MS = 1000;

const statusLine = document.getElementById("patch-status");
const numberInput = document.getElementById("patch-number");
const dialButton = document.getElementById("patch-dial");
const hangupButton = document.getElementById("patch-hangup");

let timer = null;

function clock(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function time(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function describe(status) {
  const { call, last_call: last } = status;
  if (!status.configured) {
    return "Asterisk isn't connected. Set <code>MOREOPENREPEATER_AMI_HOST</code> and the other AMI settings in the service's environment (see the setup guide).";
  }
  if (status.error) return escapeHtml(status.error);
  if (call) {
    const number = `<strong>${escapeHtml(call.number)}</strong>`;
    const by = call.actor === "DTMF" ? "over the air" : `by ${escapeHtml(call.actor)}`;
    if (call.state === "dialing") return `Dialing ${number} (started ${by})…`;
    return `Connected to ${number} for ${clock(Date.now() / 1000 - call.connected_at)} (started ${by}).`;
  }
  const off = status.enabled ? "" : " Autopatch is turned off.";
  if (!last) return `No calls yet.${off}`;
  const talk = last.connected_at ? `, ${clock(last.ended_at - last.connected_at)}` : "";
  return `Last call: ${escapeHtml(last.number)} at ${time(last.started_at)} — ${escapeHtml(last.result)}${talk}.${off}`;
}

function render(status) {
  statusLine.innerHTML = describe(status);
  hangupButton.hidden = !status.call;
  dialButton.disabled = Boolean(status.call) || !status.available;
}

async function refresh() {
  render(await api("/api/autopatch"));
}

function start() {
  refresh().catch(toastError);
  timer ??= setInterval(() => {
    if (!document.hidden) refresh().catch(() => {});
  }, REFRESH_MS);
}

function stop() {
  clearInterval(timer);
  timer = null;
}

async function dial() {
  const number = numberInput.value.replace(/[^0-9]/g, "");
  if (!number) {
    numberInput.focus();
    return;
  }
  dialButton.disabled = true;
  try {
    render(await api("/api/autopatch/dial", { method: "POST", json: { number } }));
  } catch (error) {
    toastError(error);
    await refresh().catch(() => {});
  }
}

export function initAutopatch() {
  dialButton.addEventListener("click", dial);
  numberInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !dialButton.disabled) dial();
  });
  hangupButton.addEventListener("click", () =>
    withBusy(hangupButton, async () => {
      try {
        render(await api("/api/autopatch/hangup", { method: "POST" }));
      } catch (error) {
        toastError(error);
      }
    }),
  );
  router.addEventListener("change", ({ detail }) => (detail === "autopatch" ? start() : stop()));
  if (currentView === "autopatch") start();
}
