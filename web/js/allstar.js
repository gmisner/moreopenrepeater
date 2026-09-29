import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const REFRESH_MS = 2000;

const statusLine = document.getElementById("allstar-status");
const pill = document.getElementById("allstar-pill");
const form = document.getElementById("allstar-form");
const nodeSelect = form.elements.node;
const nodeHint = document.getElementById("allstar-node-hint");
const useButton = document.getElementById("allstar-use");
const releaseButton = document.getElementById("allstar-release");

let timer = null;
let lastNodes = "";
let latest = null;

function radioName(rxchannel) {
  if (/^usrp\//i.test(rxchannel)) return "USRP";
  if (/^simpleusb\//i.test(rxchannel)) return "a USB sound card (SimpleUSB)";
  if (/^usbradio\//i.test(rxchannel)) return "a USB sound card (USBRadio)";
  if (/^local\/pseudo$/i.test(rxchannel)) return "no radio (a hub)";
  return rxchannel || "no radio set";
}

function describe(status) {
  const { audio } = status;
  if (status.manual) {
    return {
      text: `The node at <strong>${escapeHtml(audio.node)}</strong> is set up by hand (<code>MOREOPENREPEATER_USRP_NODE</code>).`,
    };
  }
  if (!status.available) {
    return {
      text:
        "AllStarLink wasn't found on this machine (no <code>/etc/asterisk/rpt.conf</code>). Install ASL3 with " +
        "<code>install-pi.sh --allstar</code>, or see the setup guide to use a node elsewhere.",
      pill: ["Not installed", "off"],
    };
  }
  if (status.error) return { text: escapeHtml(status.error), pill: ["Error", "warn"] };
  if (!status.nodes.length) return { text: "rpt.conf has no nodes yet. Set one up with <code>sudo asl-menu</code>." };
  if (!status.node) {
    const next = session?.role === "admin" ? "Choose the node below." : "An admin can choose one here.";
    return { text: `The repeater isn't the radio for a node yet. ${next}`, pill: ["Not linked", "off"] };
  }
  const node = `node <strong>${escapeHtml(status.node)}</strong>`;
  if (status.restart_needed) {
    return { text: `Set up as ${node}. Restart Asterisk to apply it (<code>sudo systemctl restart asterisk</code>).`, pill: ["Restart needed", "warn"] };
  }
  if (audio.error) return { text: escapeHtml(audio.error), pill: ["Audio error", "warn"] };
  if (audio.keyed) return { text: `The repeater is ${node}'s radio. The node is transmitting.`, pill: ["Receiving", "on"] };
  return { text: `The repeater is ${node}'s radio.`, pill: ["Ready", "on"] };
}

function fillNodes(status) {
  const key = JSON.stringify(status.nodes);
  if (key === lastNodes) return;
  lastNodes = key;
  const chosen = status.node ?? nodeSelect.value;
  nodeSelect.innerHTML = status.nodes
    .map((n) => `<option value="${escapeHtml(n.number)}">${escapeHtml(n.number)}</option>`)
    .join("");
  if (status.nodes.some((n) => n.number === chosen)) nodeSelect.value = chosen;
  showHint();
}

function showHint() {
  const node = latest?.nodes.find((n) => n.number === nodeSelect.value);
  if (!node) {
    nodeHint.textContent = "";
    return;
  }
  nodeHint.textContent = node.controlled
    ? "The repeater is its radio."
    : `Its radio is ${radioName(node.rxchannel)} now.`;
  useButton.textContent = node.controlled ? "Set up again" : "Use this node";
}

function render(status) {
  latest = status;
  const view = describe(status);
  statusLine.innerHTML = view.text;
  pill.hidden = !view.pill;
  if (view.pill) {
    pill.textContent = view.pill[0];
    pill.className = `pill pill-${view.pill[1]}`;
  }
  const isAdmin = session?.role === "admin";
  form.hidden = !isAdmin || !status.available || Boolean(status.error) || !status.nodes.length;
  releaseButton.hidden = !status.node;
  if (!form.hidden) fillNodes(status);
}

async function refresh() {
  render(await api("/api/allstar"));
}

async function use(event) {
  event.preventDefault();
  const number = nodeSelect.value;
  const other = latest?.node && latest.node !== number ? ` Node ${latest.node} gets its old radio back.` : "";
  if (!confirm(`Make the repeater node ${number}'s radio? Asterisk restarts, dropping links for a few seconds.${other}`)) return;
  await withBusy(useButton, async () => {
    try {
      render(await api("/api/allstar", { method: "PUT", json: { node: number } }));
      toast(`The repeater is node ${number}'s radio`);
    } catch (error) {
      toastError(error);
      refresh().catch(() => {});
    }
  });
}

async function release() {
  if (!confirm(`Give node ${latest.node} its old radio back? Asterisk restarts, and the repeater stops linking.`)) return;
  await withBusy(releaseButton, async () => {
    try {
      render(await api("/api/allstar", { method: "DELETE" }));
      toast("Node settings put back");
    } catch (error) {
      toastError(error);
      refresh().catch(() => {});
    }
  });
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

export function initAllStar() {
  form.addEventListener("submit", use);
  nodeSelect.addEventListener("change", showHint);
  releaseButton.addEventListener("click", release);
  router.addEventListener("change", ({ detail }) => (detail === "allstar" ? start() : stop()));
  if (currentView === "allstar") start();
}
