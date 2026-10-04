import { api } from "./api.js";
import { loadConfig } from "./config.js";
import { refreshFolds } from "./folds.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const REFRESH_MS = 1000;
const TRUNK_REFRESH_MS = 5000;

const statusLine = document.getElementById("patch-status");
const numberInput = document.getElementById("patch-number");
const dialButton = document.getElementById("patch-dial");
const hangupButton = document.getElementById("patch-hangup");

const callCard = document.getElementById("patch-card");
const callCardTitle = document.getElementById("patch-card-title");
const callCardDetail = document.getElementById("patch-card-detail");
const callCardHangup = document.getElementById("patch-card-hangup");

const trunkStatus = document.getElementById("trunk-status");
const trunkPill = document.getElementById("trunk-pill");
const trunkModules = document.getElementById("trunk-modules");
const enableButton = document.getElementById("trunk-enable");
const trunkForm = document.getElementById("trunk-form");
const removeButton = document.getElementById("trunk-remove");
const providerHint = document.getElementById("trunk-provider-hint");
const incomingSwitch = document.querySelector('[name="autopatch_incoming_enabled"]');
const incomingPin = document.querySelector('[name="autopatch_incoming_pin"]');

// www.twilio.com/docs/sip-trunking/ip-addresses, "Regional signaling"
const TWILIO_SIGNALING = [
  "54.172.60.0/30",
  "54.244.51.0/30",
  "54.171.127.192/30",
  "35.156.191.128/30",
  "54.65.63.192/30",
  "54.169.127.128/30",
  "54.252.254.64/30",
  "177.71.206.192/30",
];

const PROVIDERS = {
  voipms: {
    placeholder: "e.g. atlanta.voip.ms",
    prefix: "1",
    registers: true,
    hint: "The server your account or number is set to, and your 6-digit account ID or a sub-account (123456_repeater).",
  },
  telnyx: {
    server: "sip.telnyx.com",
    prefix: "+1",
    registers: true,
    hint: "The username and password of a Credentials connection.",
  },
  twilio: {
    placeholder: "yourtrunk.pstn.twilio.com",
    prefix: "+1",
    registers: false,
    incoming: TWILIO_SIGNALING,
    hint: "The trunk's termination URI, and a user from its credential list. Turn on Symmetric RTP in the trunk's settings. Caller ID must be a Twilio or verified number, like +18605550100. For calls in, set the trunk's origination URI to this Asterisk.",
  },
  other: {
    placeholder: "sip.example.com",
    prefix: "",
    registers: true,
    hint: "Your provider's Asterisk (PJSIP) setup guide lists these.",
  },
};

let timer = null;
let trunkTimer = null;
let trunkFormDirty = false;
let savedTrunk = undefined;

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
    const number = `<strong>${escapeHtml(call.number || "an unknown number")}</strong>`;
    const talking = clock(Date.now() / 1000 - call.connected_at);
    if (call.direction === "incoming") return `On a call from ${number} for ${talking}.`;
    const by = call.actor === "DTMF" ? "over the air" : `by ${escapeHtml(call.actor)}`;
    if (call.state === "dialing") return `Dialing ${number} (started ${by})…`;
    return `Connected to ${number} for ${talking} (started ${by}).`;
  }
  const off = status.enabled ? "" : " Autopatch is turned off.";
  if (!last) return `No calls yet.${off}`;
  const talk = last.connected_at ? `, ${clock(last.ended_at - last.connected_at)}` : "";
  const who = last.direction === "incoming" ? `from ${escapeHtml(last.number || "an unknown number")}` : escapeHtml(last.number);
  return `Last call: ${who} at ${time(last.started_at)} — ${escapeHtml(last.result)}${talk}.${off}`;
}

function cardTitle(call) {
  if (call.direction === "incoming") return "Phone call in";
  return call.state === "dialing" ? "Autopatch dialing" : "Autopatch call";
}

function render(status) {
  statusLine.innerHTML = describe(status);
  hangupButton.hidden = !status.call;
  dialButton.disabled = Boolean(status.call) || !status.available;
  callCard.hidden = !status.call;
  if (status.call) {
    callCardTitle.textContent = cardTitle(status.call);
    callCardDetail.innerHTML = describe(status);
  }
}

async function refresh() {
  render(await api("/api/autopatch"));
}

// The dashboard only asks about calls while the controller is in a call.
let dashboardTimer = null;

async function refreshDashboard() {
  const status = await api("/api/autopatch");
  render(status);
  if (!status.call) stopDashboard();
}

function startDashboard() {
  refreshDashboard().catch(() => {});
  dashboardTimer ??= setInterval(() => {
    if (!document.hidden) refreshDashboard().catch(() => {});
  }, REFRESH_MS);
}

function stopDashboard() {
  clearInterval(dashboardTimer);
  dashboardTimer = null;
}

async function hangup(button) {
  await withBusy(button, async () => {
    try {
      render(await api("/api/autopatch/hangup", { method: "POST" }));
    } catch (error) {
      toastError(error);
    }
  });
}

function providerFor(server) {
  if (/(^|\.)voip\.ms$/i.test(server)) return "voipms";
  if (/(^|\.)telnyx\.com$/i.test(server)) return "telnyx";
  if (/\.pstn\.twilio\.com$/i.test(server)) return "twilio";
  return "other";
}

function showProvider(key) {
  const provider = PROVIDERS[key];
  trunkForm.elements.server.placeholder = provider.placeholder ?? "";
  providerHint.textContent = provider.hint;
}

function chooseProvider() {
  const { elements } = trunkForm;
  const provider = PROVIDERS[elements.provider.value];
  const fixedServers = Object.values(PROVIDERS).map((p) => p.server).filter(Boolean);
  if (provider.server) elements.server.value = provider.server;
  else if (fixedServers.includes(elements.server.value)) elements.server.value = "";
  elements.ten_digit_prefix.value = provider.prefix;
  elements.registers.checked = provider.registers;
  elements.incoming_from.value = (provider.incoming ?? []).join(" ");
  showProvider(elements.provider.value);
}

function recognizeServer() {
  const { elements } = trunkForm;
  const detected = providerFor(elements.server.value.trim());
  if (detected === "other" || detected === elements.provider.value) return;
  elements.provider.value = detected;
  elements.ten_digit_prefix.value = PROVIDERS[detected].prefix;
  elements.registers.checked = PROVIDERS[detected].registers;
  elements.incoming_from.value = (PROVIDERS[detected].incoming ?? []).join(" ");
  showProvider(detected);
}

function populateTrunk(trunk, prefix) {
  const { elements } = trunkForm;
  const values = trunk ?? { server: "", port: 5060, transport: "udp", username: "", auth_username: "", registers: true };
  elements.provider.value = trunk ? providerFor(trunk.server) : "voipms";
  for (const name of ["server", "port", "transport", "username", "auth_username"]) elements[name].value = values[name];
  elements.registers.checked = values.registers;
  elements.incoming_from.value = (values.incoming_from ?? []).join(" ");
  elements.password.value = "";
  elements.password.required = !trunk?.has_password;
  elements.password.placeholder = trunk?.has_password ? "Saved; leave blank to keep it" : "";
  if (trunk) elements.ten_digit_prefix.value = prefix;
  else chooseProvider();
  showProvider(elements.provider.value);
  trunkFormDirty = false;
  refreshFolds();
}

const SIP_MODULE = /pjsip|pjproject|rtp|sorcery|codec/;

function describeTrunk(status) {
  const { trunk } = status;
  if (!status.configured) {
    return {
      text:
        "The phone line settings appear here once the controller can reach Asterisk. Set " +
        "<code>MOREOPENREPEATER_AMI_HOST</code>, <code>MOREOPENREPEATER_AMI_USER</code> and " +
        "<code>MOREOPENREPEATER_AMI_SECRET</code> in the service's environment " +
        "(<code>/etc/moreopenrepeater/env</code> on a Pi) and restart it.",
      pill: ["Not connected", "off"],
    };
  }
  if (status.error) return { text: escapeHtml(status.error) };
  const missing = status.missing_modules;
  if (missing.length) {
    const what = missing.some((m) => SIP_MODULE.test(m))
      ? "SIP"
      : missing.some((m) => m.includes("audiosocket"))
        ? "AudioSocket"
        : "Part of the dialplan autopatch uses";
    const who = session?.role === "admin" ? "Turning it on" : "An admin can turn it on here, which";
    return {
      text: `${what} is turned off in Asterisk. ${who} also sets it to load whenever Asterisk starts.`,
      needsModules: true,
    };
  }
  if (!trunk) {
    const next = session?.role === "admin" ? "Enter your SIP provider's details." : "An admin can set one up here.";
    return { text: `No phone line yet. ${next}` };
  }
  const line = `<strong>${escapeHtml(trunk.username)}</strong> at <strong>${escapeHtml(trunk.server)}</strong>`;
  let unused = status.in_use ? "" : " Autopatch isn't using it: the dial string below points somewhere else.";
  if (!trunk.answers_calls) unused += " Save it again to answer calls in.";
  if (trunk.registers) {
    const registration = status.registration ?? "Unknown";
    if (registration === "Registered") return { text: `Registered as ${line}.${unused}`, pill: ["Registered", "on"] };
    if (registration.startsWith("Rejected"))
      return { text: `The provider rejected the login for ${line}. Check the username and password.${unused}`, pill: ["Rejected", "warn"] };
    return { text: `Registering ${line}&hellip; Asterisk retries every minute if the provider doesn't answer.${unused}`, pill: ["Not registered", "off"] };
  }
  if (status.reachability === "Reachable") return { text: `Calls go to ${line}.${unused}`, pill: ["Reachable", "on"] };
  if (status.reachability === "Unreachable")
    return { text: `Asterisk can't reach ${line}.${unused}`, pill: ["Unreachable", "warn"] };
  return { text: `Calls go to ${line}.${unused}` };
}

function renderTrunk(status) {
  const view = describeTrunk(status);
  const isAdmin = session?.role === "admin";
  trunkStatus.innerHTML = view.text;
  trunkPill.hidden = !view.pill;
  if (view.pill) {
    trunkPill.textContent = view.pill[0];
    trunkPill.className = `pill pill-${view.pill[1]}`;
  }
  trunkModules.hidden = !(view.needsModules && isAdmin);
  trunkForm.hidden = !isAdmin || !status.configured || Boolean(status.error) || Boolean(view.needsModules);
  removeButton.hidden = !status.trunk;
  const trunkChanged = JSON.stringify(status.trunk) !== JSON.stringify(savedTrunk);
  if (!trunkForm.hidden && (trunkChanged || !trunkFormDirty) && !trunkForm.contains(document.activeElement)) {
    populateTrunk(status.trunk, status.ten_digit_prefix);
  }
  savedTrunk = status.trunk;
}

async function refreshTrunk() {
  renderTrunk(await api("/api/autopatch/trunk"));
}

async function saveTrunk(event) {
  event.preventDefault();
  const { elements } = trunkForm;
  const button = trunkForm.querySelector("button[type=submit]");
  await withBusy(button, async () => {
    try {
      const status = await api("/api/autopatch/trunk", {
        method: "PUT",
        json: {
          server: elements.server.value.trim(),
          port: Number(elements.port.value),
          transport: elements.transport.value,
          username: elements.username.value.trim(),
          auth_username: elements.auth_username.value.trim(),
          password: elements.password.value,
          registers: elements.registers.checked,
          ten_digit_prefix: elements.ten_digit_prefix.value,
          incoming_from: elements.incoming_from.value.split(/[\s,]+/).filter(Boolean),
        },
      });
      trunkFormDirty = false;
      savedTrunk = undefined;
      renderTrunk(status);
      toast("Phone line saved");
      loadConfig().catch(() => {});
    } catch (error) {
      toastError(error);
    }
  });
}

async function removeTrunk() {
  if (!confirm("Remove the phone line from Asterisk? Autopatch calls will fail until one is set up again.")) return;
  await withBusy(removeButton, async () => {
    try {
      savedTrunk = undefined;
      renderTrunk(await api("/api/autopatch/trunk", { method: "DELETE" }));
      toast("Phone line removed");
    } catch (error) {
      toastError(error);
    }
  });
}

function start() {
  refresh().catch(toastError);
  refreshTrunk().catch(toastError);
  timer ??= setInterval(() => {
    if (!document.hidden) refresh().catch(() => {});
  }, REFRESH_MS);
  trunkTimer ??= setInterval(() => {
    if (!document.hidden) refreshTrunk().catch(() => {});
  }, TRUNK_REFRESH_MS);
}

function stop() {
  clearInterval(timer);
  clearInterval(trunkTimer);
  timer = null;
  trunkTimer = null;
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

function requirePin() {
  incomingPin.required = incomingSwitch.checked;
}

export function initAutopatch() {
  incomingSwitch.addEventListener("change", requirePin);
  store.addEventListener("config", () => queueMicrotask(requirePin)); // after the form is filled in
  dialButton.addEventListener("click", dial);
  numberInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !dialButton.disabled) dial();
  });
  hangupButton.addEventListener("click", () => hangup(hangupButton));
  callCardHangup.addEventListener("click", () => hangup(callCardHangup));
  store.addEventListener("status", ({ detail }) => {
    if (detail.state === "patch" && currentView === "dashboard" && !dashboardTimer) startDashboard();
  });
  trunkForm.addEventListener("input", () => (trunkFormDirty = true));
  trunkForm.elements.provider.addEventListener("change", chooseProvider);
  trunkForm.elements.server.addEventListener("change", recognizeServer);
  trunkForm.addEventListener("submit", saveTrunk);
  removeButton.addEventListener("click", removeTrunk);
  enableButton.addEventListener("click", () =>
    withBusy(enableButton, async () => {
      try {
        renderTrunk(await api("/api/autopatch/trunk/modules", { method: "POST" }));
        toast("SIP is on in Asterisk");
      } catch (error) {
        toastError(error);
        refreshTrunk().catch(() => {});
      }
    }),
  );
  router.addEventListener("change", ({ detail }) => {
    if (detail === "autopatch") start();
    else stop();
    if (detail !== "dashboard") stopDashboard();
    else if (store.state.status?.state === "patch") startDashboard();
    else callCard.hidden = true;
  });
  if (currentView === "autopatch") start();
}
