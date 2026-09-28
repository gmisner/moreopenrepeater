import { api } from "./api.js";
import { router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const SEVERITY_TAGS = { Extreme: "tag-danger", Severe: "tag-danger", Moderate: "tag-warn" };
// The server re-polls on its own schedule; this only refreshes the page's copy.
const REFRESH_MS = 60_000;

const tbody = document.getElementById("wx-tbody");
const empty = document.getElementById("wx-empty");
const statusLine = document.getElementById("wx-status");
const checkButton = document.getElementById("wx-check");

function describeStatus(weather) {
  if (weather.last_error) return `Last check failed: ${escapeHtml(weather.last_error)}`;
  if (!weather.last_checked) return weather.enabled ? "Waiting for the first check…" : "Not checked yet.";
  const checked = `Checked ${escapeHtml(formatTimestamp(weather.last_checked))}.`;
  return weather.enabled ? checked : `${checked} Announcements are switched off.`;
}

function render(weather) {
  statusLine.innerHTML = describeStatus(weather);
  tbody.innerHTML = "";
  empty.hidden = weather.alerts.length > 0;
  for (const alert of weather.alerts) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(alert.event)}</strong>
        ${alert.announced ? '<span class="tag tag-on">announced</span>' : ""}
        <div class="muted small">${escapeHtml(alert.area)}</div></td>
      <td><span class="tag ${SEVERITY_TAGS[alert.severity] ?? ""}">${escapeHtml(alert.severity)}</span></td>
      <td class="small">${escapeHtml(formatTimestamp(alert.ends ?? alert.expires ?? ""))}</td>
      <td class="small">${escapeHtml(alert.speech)}</td>
      <td class="row-actions">
        <button type="button" class="btn btn-ghost btn-sm" title="Transmit now (waits for a clear channel)">Play now</button>
      </td>
    `;
    const button = row.querySelector("button");
    button.addEventListener("click", () => withBusy(button, () => play(alert)));
    tbody.appendChild(row);
  }
}

async function play(alert) {
  try {
    await api(`/api/weather/alerts/${encodeURIComponent(alert.id)}/play`, { method: "POST" });
    toast(`Queued "${alert.event}" — it plays as soon as the channel is clear`);
  } catch (error) {
    toastError(error);
  }
}

export async function loadWeather() {
  store.set("weather", await api("/api/weather"));
}

export function initWeather() {
  store.addEventListener("weather", ({ detail }) => render(detail));
  store.addEventListener("config", () => {
    if (store.state.weather) render({ ...store.state.weather, enabled: store.state.config.wx_alerts_enabled });
  });
  checkButton.addEventListener("click", () =>
    withBusy(checkButton, async () => {
      try {
        store.set("weather", await api("/api/weather/check", { method: "POST" }));
      } catch (error) {
        toastError(error);
      }
    }),
  );
  router.addEventListener("change", ({ detail }) => {
    if (detail === "weather") loadWeather().catch(toastError);
  });
  setInterval(() => {
    if (!document.hidden && store.state.weather) loadWeather().catch(() => {});
  }, REFRESH_MS);
}
