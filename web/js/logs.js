import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { escapeHtml } from "./ui.js";

const POLL_INTERVAL_MS = 3000;
const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LEVEL_PATTERN = /\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b/;

const view = document.getElementById("logs-view");
const filterInput = document.getElementById("logs-filter");
const levelSelect = document.getElementById("logs-level");
const linesSelect = document.getElementById("logs-lines");
const pauseToggle = document.getElementById("logs-pause");

let lines = [];
let timer = null;

function render() {
  const filter = filterInput.value.trim().toLowerCase();
  const minLevel = LEVELS.indexOf(levelSelect.value);
  const visible = lines.filter((line) => {
    if (filter && !line.toLowerCase().includes(filter)) return false;
    if (minLevel > 0) {
      const level = line.match(LEVEL_PATTERN)?.[1];
      if (level && LEVELS.indexOf(level) < minLevel) return false;
    }
    return true;
  });
  const atBottom = view.scrollHeight - view.scrollTop <= view.clientHeight + 20;
  view.innerHTML = visible
    .map((line) => {
      const level = line.match(LEVEL_PATTERN)?.[1]?.toLowerCase() ?? "";
      return `<span class="log-line log-${level}">${escapeHtml(line)}</span>`;
    })
    .join("\n") || '<span class="muted">No log lines match.</span>';
  if (atBottom) view.scrollTop = view.scrollHeight;
}

async function poll() {
  if (pauseToggle.checked) return;
  try {
    lines = await api(`/api/logs?lines=${linesSelect.value}`);
    render();
  } catch {
    // Transient failure; the next poll retries.
  }
}

function syncPolling(viewName) {
  clearInterval(timer);
  timer = null;
  if (viewName === "logs") {
    poll();
    timer = setInterval(poll, POLL_INTERVAL_MS);
  }
}

export function initLogs() {
  filterInput.addEventListener("input", render);
  levelSelect.addEventListener("change", render);
  linesSelect.addEventListener("change", poll);
  pauseToggle.addEventListener("change", poll);
  router.addEventListener("change", ({ detail }) => syncPolling(detail));
  syncPolling(currentView);
}
