import { toastError } from "./ui.js";

// Frames arrive every ~100 ms; play this far behind to ride out network jitter.
const LEAD_SECONDS = 0.25;
const MAX_LAG_SECONDS = 1.0;
const NO_AUDIO_MS = 2000;

const toggle = document.getElementById("listen-toggle");
const statusLine = document.getElementById("listen-status");
const sourceButtons = document.querySelectorAll("#listen-source button");

const IDLE_TEXT = statusLine.textContent;
const SOURCE_TEXT = { tx: "on-air audio", rx: "the receiver" };

let source = "tx";
let context = null;
let socket = null;
let sampleRate = 16000;
let nextTime = 0;
let lastFrameAt = 0;
let watchdog = null;

function playFrame(buffer) {
  const ints = new Int16Array(buffer);
  const audio = context.createBuffer(1, ints.length, sampleRate);
  const channel = audio.getChannelData(0);
  for (let i = 0; i < ints.length; i++) channel[i] = ints[i] / 32768;
  const node = context.createBufferSource();
  node.buffer = audio;
  node.connect(context.destination);
  const now = context.currentTime;
  if (nextTime < now + 0.02 || nextTime > now + MAX_LAG_SECONDS) nextTime = now + LEAD_SECONDS;
  node.start(nextTime);
  nextTime += audio.duration;
}

function showStatus() {
  if (!socket) return;
  statusLine.textContent =
    Date.now() - lastFrameAt < NO_AUDIO_MS
      ? `Listening to ${SOURCE_TEXT[source]}.`
      : "Connected, but no audio is coming through. Is live audio turned on under Audio & tones?";
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws/audio?source=${source}`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      sampleRate = JSON.parse(event.data).sample_rate;
      return;
    }
    lastFrameAt = Date.now();
    playFrame(event.data);
  };
  ws.onclose = () => {
    if (socket === ws) {
      stop();
      statusLine.textContent = "The audio stream closed.";
    }
  };
  socket = ws;
  lastFrameAt = 0;
  nextTime = 0;
  statusLine.textContent = "Connecting…";
}

async function start() {
  try {
    context ??= new AudioContext();
    await context.resume();
  } catch (error) {
    toastError(error);
    return;
  }
  connect();
  watchdog = setInterval(showStatus, 500);
  toggle.textContent = "Stop";
  toggle.classList.replace("btn-primary", "btn-danger");
}

function stop() {
  const ws = socket;
  socket = null;
  ws?.close();
  clearInterval(watchdog);
  context?.suspend();
  toggle.textContent = "Listen";
  toggle.classList.replace("btn-danger", "btn-primary");
  statusLine.textContent = IDLE_TEXT;
}

export function initListen() {
  toggle.addEventListener("click", () => (socket ? stop() : start()));
  for (const button of sourceButtons) {
    button.addEventListener("click", () => {
      source = button.dataset.source;
      for (const b of sourceButtons) b.classList.toggle("active", b === button);
      if (socket) {
        const ws = socket;
        socket = null;
        ws.close();
        connect();
      }
    });
  }
}
