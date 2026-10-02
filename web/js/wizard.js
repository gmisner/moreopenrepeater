import { api } from "./api.js";
import { session } from "./session.js";
import { store } from "./store.js";
import { toast } from "./ui.js";

const POLL_MS = 400;
const BY_HAND = "";

const dialog = document.getElementById("setup-wizard");
const $ = (id) => document.getElementById(id);
const steps = [...dialog.querySelectorAll("[data-step]")];
const nextButton = $("wizard-next");
const backButton = $("wizard-back");
const errorLine = $("wizard-error");

let boards = [];
let devices = [];
let stepIndex = 0;
let pollTimer = null;
let autoOpened = false;

function activeSteps() {
  return steps.filter((step) => step.dataset.step !== "account" || session.role === "admin");
}

function selectedBoard() {
  return boards.find((b) => b.id === $("wizard-board").value) ?? null;
}

function showError(message) {
  errorLine.textContent = message ?? "";
  errorLine.hidden = !message;
}

function fillDeviceSelect(select, kind, current) {
  const names = devices.filter((d) => d[kind] > 0).map((d) => d.name);
  select.innerHTML = '<option value="">System default</option>';
  for (const name of names) select.add(new Option(name, name));
  if (current && !names.includes(current)) select.add(new Option(`${current} (not connected)`, current));
  select.value = current ?? "";
}

// The saved device if it's plugged in; otherwise the first device (or, for
// port 2, the next one) whose name matches the board's hints, so a single
// USB interface is picked without asking.
function pickDevice(kind, saved, skip = 0) {
  if (saved && devices.some((d) => d.name === saved && d[kind] > 0)) return saved;
  const hints = (selectedBoard()?.device_hints ?? []).map((h) => h.toLowerCase());
  const matches = devices.filter((d) => d[kind] > 0 && hints.some((h) => d.name.toLowerCase().includes(h)));
  return matches[skip]?.name ?? saved;
}

function renderBoard() {
  const board = selectedBoard();
  $("wizard-board-notes").textContent = board ? board.unsupported ?? board.notes : "Leave the wiring as it is and set it up on the Audio page.";
  $("wizard-mixer-row").hidden = !board?.mixer.length;
  $("wizard-mixer-levels").textContent = board?.mixer.length ? `(${board.mixer.map(([c, v]) => `${c} ${v}`).join(", ")})` : "";
  nextButton.disabled = Boolean(board?.unsupported);
}

function renderBoards() {
  const select = $("wizard-board");
  select.innerHTML = "";
  select.add(new Option("None of these: I'll set it up myself", BY_HAND));
  const groups = [
    ["USB interfaces", (b) => b.kind === "usb" && !b.unsupported],
    ["Raspberry Pi boards", (b) => b.kind === "pi" && !b.unsupported],
    ["Not supported yet", (b) => b.unsupported],
  ];
  for (const [label, test] of groups) {
    const group = document.createElement("optgroup");
    group.label = label;
    for (const board of boards.filter(test)) {
      group.appendChild(new Option(board.maker ? `${board.name} (${board.maker})` : board.name, board.id));
    }
    if (group.children.length) select.appendChild(group);
  }
  select.value = store.state.config?.board_preset ?? BY_HAND;
  renderBoard();
}

function renderDevices() {
  const config = store.state.config ?? {};
  const board = selectedBoard();
  fillDeviceSelect($("wizard-input"), "inputs", pickDevice("inputs", config.audio_input_device));
  fillDeviceSelect($("wizard-output"), "outputs", pickDevice("outputs", config.audio_output_device));
  $("wizard-link").hidden = !board?.two_port;
  $("wizard-link-on").checked = Boolean(board?.two_port && config.link_radio_enabled);
  fillDeviceSelect($("wizard-link-input"), "inputs", pickDevice("inputs", config.link_radio_input_device, 1));
  fillDeviceSelect($("wizard-link-output"), "outputs", pickDevice("outputs", config.link_radio_output_device, 1));
  $("wizard-link-devices").hidden = !$("wizard-link-on").checked;
}

function renderAccount() {
  $("wizard-account-intro").textContent = session.auth_required
    ? "Sign-in is on. Add your own admin account so you don't need the installer's generated password, or leave this blank."
    : "Anyone who can open this page can change the repeater. Create an admin account to turn on sign-in.";
}

function setTag(tag, on, text, onClass = "tag-on") {
  tag.className = `tag ${on ? onClass : ""}`;
  tag.textContent = text;
}

async function pollEngine() {
  try {
    const engine = await api("/api/audio/engine");
    setTag($("wizard-engine"), engine.running || engine.error, engine.running ? "running" : engine.error ? "error" : "off", engine.error ? "tag-danger" : "tag-on");
    setTag($("wizard-cos"), engine.running && engine.cos_open, "carrier");
    setTag($("wizard-tx"), engine.running && engine.transmitting, "TX", "tag-danger");
    $("wizard-engine-error").textContent = engine.error ?? "";
    $("wizard-engine-error").hidden = !engine.error;
  } catch {
    // The next poll retries.
  }
  pollTimer = setTimeout(pollEngine, POLL_MS);
}

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

function renderMixerResults(result) {
  const list = $("wizard-mixer-results");
  const lines = [
    ...result.mixer.map((r) => (r.error ? `Card ${r.card}: couldn't set ${r.control}: ${r.error}` : `Card ${r.card}: ${r.control} set to ${r.value}`)),
    ...result.mixer_skipped.map((name) => `Levels not set on ${name}: pick a specific sound card to set them.`),
  ];
  list.replaceChildren(...lines.map((text) => Object.assign(document.createElement("li"), { textContent: text })));
  list.hidden = !lines.length;
}

function show(index) {
  const visible = activeSteps();
  stepIndex = Math.max(0, Math.min(index, visible.length - 1));
  const step = visible[stepIndex];
  for (const s of steps) s.hidden = s !== step;
  $("wizard-title").textContent = step.dataset.title;
  $("wizard-progress").textContent = `Step ${stepIndex + 1} of ${visible.length}`;
  backButton.hidden = stepIndex === 0;
  nextButton.textContent = stepIndex === visible.length - 1 ? "Finish" : "Next";
  nextButton.disabled = false;
  showError(null);
  if (step.dataset.step === "board") renderBoard();
  if (step.dataset.step === "devices") renderDevices();
  if (step.dataset.step === "test") {
    if (!pollTimer) pollEngine();
  } else stopPolling();
  step.querySelector("input, select")?.focus();
}

async function saveConfig(changes) {
  store.set("config", await api("/api/config", { method: "PUT", json: changes }));
}

async function finish() {
  await saveConfig({ setup_wizard_done: true });
  close();
}

function close() {
  stopPolling();
  dialog.close();
}

// Each step saves as it goes, so closing part-way keeps what was done.
const leaveStep = {
  async station() {
    const callsign = $("wizard-callsign").value.trim().toUpperCase();
    if (!callsign) throw new Error("Enter the station's callsign.");
    await saveConfig({ callsign, id_mode: $("wizard-id-mode").value });
  },
  async board() {},
  async devices() {
    const board = selectedBoard();
    const linkOn = board?.two_port && $("wizard-link-on").checked;
    if (linkOn && (!$("wizard-link-input").value || !$("wizard-link-output").value)) {
      throw new Error("Pick the link radio's input and output.");
    }
    const devicesChosen = { input_device: $("wizard-input").value, output_device: $("wizard-output").value };
    if (!board) {
      await saveConfig({ audio_enabled: true, audio_input_device: devicesChosen.input_device, audio_output_device: devicesChosen.output_device });
      renderMixerResults({ mixer: [], mixer_skipped: [] });
      return;
    }
    const result = await api(`/api/boards/${encodeURIComponent(board.id)}/apply`, {
      method: "POST",
      json: {
        ...devicesChosen,
        ...(linkOn ? { link_input_device: $("wizard-link-input").value, link_output_device: $("wizard-link-output").value } : {}),
        set_mixer: $("wizard-mixer").checked,
      },
    });
    store.set("config", result.config);
    renderMixerResults(result);
  },
  async test() {},
  async account() {
    const username = $("wizard-username").value.trim();
    const password = $("wizard-password").value;
    if (!username && !password) return;
    if (!username) throw new Error("Enter a username, or clear the password to skip this.");
    if (password.length < 8) throw new Error("Use a password of at least 8 characters.");
    if (password !== $("wizard-password-2").value) throw new Error("The passwords don't match.");
    const signInWasOn = session.auth_required;
    await saveConfig({ setup_wizard_done: true });
    await api("/api/users", { method: "POST", json: { username, password, role: "admin" } });
    if (!signInWasOn) {
      // That account turned sign-in on, and this browser isn't signed in to it.
      location.replace("/login");
      return "left";
    }
    toast(`Admin account ${username} added`);
  },
};

async function next() {
  const visible = activeSteps();
  const step = visible[stepIndex];
  nextButton.disabled = true;
  showError(null);
  try {
    const outcome = await leaveStep[step.dataset.step]();
    if (outcome === "left") return;
    if (stepIndex === visible.length - 1) await finish();
    else show(stepIndex + 1);
  } catch (error) {
    showError(error?.message || String(error));
  } finally {
    nextButton.disabled = Boolean(selectedBoard()?.unsupported && visible[stepIndex]?.dataset.step === "board");
  }
}

export async function openWizard() {
  const config = store.state.config ?? (await api("/api/config"));
  [boards, devices] = await Promise.all([api("/api/boards"), api("/api/audio/devices").catch(() => [])]);
  $("wizard-callsign").value = config.callsign ?? "";
  $("wizard-id-mode").value = config.id_mode ?? "voice";
  for (const id of ["wizard-username", "wizard-password", "wizard-password-2"]) $(id).value = "";
  renderBoards();
  renderAccount();
  if (!dialog.open) dialog.showModal();
  show(0);
}

function renderBoardName(config) {
  const board = boards.find((b) => b.id === config.board_preset);
  $("board-preset-name").textContent = config.board_preset ? board?.name ?? config.board_preset : "set up by hand";
}

export function initWizard() {
  nextButton.addEventListener("click", next);
  backButton.addEventListener("click", () => show(stepIndex - 1));
  $("wizard-skip").addEventListener("click", async () => {
    try {
      await finish();
    } catch (error) {
      showError(error?.message || String(error));
    }
  });
  dialog.addEventListener("close", stopPolling);
  $("wizard-board").addEventListener("change", renderBoard);
  $("wizard-link-on").addEventListener("change", () => {
    $("wizard-link-devices").hidden = !$("wizard-link-on").checked;
  });
  $("wizard-test").addEventListener("click", async () => {
    const button = $("wizard-test");
    button.disabled = true;
    showError(null);
    try {
      await api("/api/audio/test-id", { method: "POST" });
    } catch (error) {
      showError(error?.message || String(error));
    } finally {
      button.disabled = false;
    }
  });
  for (const button of document.querySelectorAll("[data-open-wizard]")) {
    button.addEventListener("click", () => openWizard().catch((error) => toast(error?.message || String(error), "error")));
  }
  api("/api/boards")
    .then((list) => {
      boards = list;
      if (store.state.config) renderBoardName(store.state.config);
    })
    .catch(() => {});
  store.addEventListener("config", ({ detail }) => {
    renderBoardName(detail);
    if (!autoOpened && !detail.setup_wizard_done && session.role === "admin") {
      autoOpened = true;
      openWizard().catch(() => {});
    }
  });
}
