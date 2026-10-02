// The public /listen page: no sign-in, so it only reads /api/public/status
// and plays /ws/public/audio.

const POLL_MS = 2000;
const LEAD_SECONDS = 0.25;
const MAX_LAG_SECONDS = 1.0;

const callsign = document.getElementById("public-callsign");
const text = document.getElementById("public-text");
const stateBadge = document.getElementById("public-state");
const netLine = document.getElementById("public-net");
const audioBlock = document.getElementById("public-audio");
const listenButton = document.getElementById("public-listen");
const listenStatus = document.getElementById("public-listen-status");

let context = null;
let socket = null;
let sampleRate = 16000;
let nextTime = 0;

function showState(status) {
  callsign.textContent = status.callsign || "Repeater";
  document.title = `${status.callsign || "Repeater"} · Listen live`;
  text.textContent = status.text;
  text.hidden = !status.text;
  const [label, cls] = status.on_air
    ? [status.receiving ? "Someone's talking" : "On the air", "state-receiving"]
    : ["Quiet", "state-idle"];
  stateBadge.textContent = label;
  stateBadge.className = `state-badge ${cls}`;
  netLine.textContent = status.net ? `${status.net} is in session.` : "";
  netLine.hidden = !status.net;
  audioBlock.hidden = !status.audio;
  if (!status.audio && socket) stop();
  if (!socket) {
    listenStatus.textContent =
      status.listeners >= status.max_listeners ? "Every listening spot is taken right now. Try again in a while." : "";
  }
}

async function poll() {
  try {
    const response = await fetch("/api/public/status", { cache: "no-store" });
    if (response.status === 404) {
      stateBadge.textContent = "This page is turned off.";
      stateBadge.className = "state-badge state-unknown";
      audioBlock.hidden = true;
      return;
    }
    showState(await response.json());
  } catch {
    stateBadge.textContent = "Can't reach the repeater.";
    stateBadge.className = "state-badge state-unknown";
  }
}

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

async function start() {
  context ??= new AudioContext();
  await context.resume();
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws/public/audio`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => (listenStatus.textContent = "Listening. Audio plays when the repeater is on the air.");
  ws.onmessage = (event) => {
    if (typeof event.data === "string") sampleRate = JSON.parse(event.data).sample_rate;
    else playFrame(event.data);
  };
  ws.onclose = (event) => {
    if (socket !== ws) return;
    stop();
    listenStatus.textContent =
      event.code === 1013 ? "Every listening spot is taken right now. Try again in a while." : "The audio stopped.";
  };
  socket = ws;
  nextTime = 0;
  listenStatus.textContent = "Connecting…";
  listenButton.textContent = "Stop";
}

function stop() {
  const ws = socket;
  socket = null;
  ws?.close();
  context?.suspend();
  listenButton.textContent = "Listen live";
  listenStatus.textContent = "";
}

listenButton.addEventListener("click", () => (socket ? stop() : start()));
poll();
setInterval(poll, POLL_MS);
