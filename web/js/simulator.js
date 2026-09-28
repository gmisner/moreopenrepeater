import { api } from "./api.js";
import { applyStatus } from "./status.js";
import { toastError } from "./ui.js";

const DTMF_KEYS = ["1", "2", "3", "A", "4", "5", "6", "B", "7", "8", "9", "C", "*", "0", "#", "D"];
const sentEl = document.getElementById("dtmf-sent");
let sent = "";

async function post(path, json) {
  try {
    applyStatus(await api(path, { method: "POST", json }));
  } catch (error) {
    toastError(error);
  }
}

const setCOS = (active) => post("/api/simulate/cos", { active });

async function sendDigit(digit) {
  await post("/api/simulate/dtmf", { digit });
  sent = (sent + digit).slice(-24);
  sentEl.textContent = sent;
}

function initHoldToKey() {
  const button = document.getElementById("hold-to-key");
  let held = false;
  const down = (event) => {
    event.preventDefault();
    if (held) return;
    held = true;
    button.classList.add("active");
    setCOS(true);
  };
  const up = () => {
    if (!held) return;
    held = false;
    button.classList.remove("active");
    setCOS(false);
  };
  button.addEventListener("pointerdown", down);
  button.addEventListener("pointerup", up);
  button.addEventListener("pointerleave", up);
  button.addEventListener("pointercancel", up);
}

export function initSimulator() {
  document.querySelectorAll("[data-cos]").forEach((button) => {
    button.addEventListener("click", () => setCOS(button.dataset.cos === "true"));
  });
  initHoldToKey();

  const toneInput = document.getElementById("ctcss-tone-input");
  document.getElementById("ctcss-apply").addEventListener("click", () => {
    const value = parseFloat(toneInput.value);
    if (!Number.isNaN(value)) post("/api/simulate/ctcss", { tone_hz: value });
  });
  document.getElementById("ctcss-clear").addEventListener("click", () => post("/api/simulate/ctcss", { tone_hz: null }));

  const keypad = document.getElementById("dtmf-keypad");
  for (const digit of DTMF_KEYS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn key";
    button.textContent = digit;
    button.addEventListener("click", () => sendDigit(digit));
    keypad.appendChild(button);
  }

  const sequenceForm = document.getElementById("dtmf-sequence-form");
  sequenceForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const sequence = sequenceForm.elements.sequence.value.trim().toUpperCase();
    for (const digit of sequence) await sendDigit(digit);
    sequenceForm.reset();
  });

  document.querySelectorAll("[data-remote-keyed]").forEach((button) => {
    button.addEventListener("click", () => {
      const nodeId = document.getElementById("remote-node-id").value.trim() || "unknown";
      post("/api/simulate/remote-keyed", { node_id: nodeId, keyed: button.dataset.remoteKeyed === "true" });
    });
  });
}
