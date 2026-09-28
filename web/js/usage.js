import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { escapeHtml, formatTimestamp, toast, toastError } from "./ui.js";

const REFRESH_MS = 30_000;
const CHART = { height: 150, left: 52, bottom: 20, top: 6 };
const KERCHUNK_NOTE = "under 1.5 s";

const rangeButtons = document.querySelectorAll("#usage-range button");
const statsEl = document.getElementById("usage-stats");
const byHourEl = document.getElementById("usage-by-hour");
const byDayEl = document.getElementById("usage-by-day");
const dailyCard = document.getElementById("usage-daily-card");
const tbody = document.getElementById("usage-tbody");
const empty = document.getElementById("usage-empty");
const recordingsTbody = document.getElementById("recordings-tbody");
const recordingsEmpty = document.getElementById("recordings-empty");

let days = 7;

function formatAirtime(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

function stat(label, value, note = "") {
  return `<div class="card stat"><span class="label">${label}</span><span class="stat-value">${value}</span>${
    note ? `<span class="stat-note">${escapeHtml(note)}</span>` : ""
  }</div>`;
}

// series: [{ values, className, title(i) }]; drawn in order, so later series sit on top.
// Drawn at the container's real pixel width so axis text stays a fixed size.
function barChart(container, labels, series, labelEvery = 1) {
  const { height, left, bottom, top } = CHART;
  const width = container.clientWidth || 720;
  const plotHeight = height - bottom - top;
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const slot = (width - left) / labels.length;
  const y = (value) => top + plotHeight - (value / max) * plotHeight;
  let svg = "";
  for (const fraction of [0, 0.5, 1]) {
    const lineY = y(max * fraction);
    svg += `<line class="gridline" x1="${left}" x2="${width}" y1="${lineY}" y2="${lineY}" />`;
    svg += `<text class="axis" x="${left - 6}" y="${lineY + 3}" text-anchor="end">${formatAirtime(max * fraction)}</text>`;
  }
  series.forEach((s, seriesIndex) => {
    const inset = slot * (seriesIndex === series.length - 1 ? 0.22 : 0.1);
    s.values.forEach((value, i) => {
      if (value <= 0) return;
      const x = left + i * slot + inset;
      svg += `<rect class="${s.className}" x="${x}" y="${y(value)}" width="${Math.max(1, slot - 2 * inset)}" height="${
        top + plotHeight - y(value)
      }" rx="2"><title>${escapeHtml(s.title(i))}</title></rect>`;
    });
  });
  labels.forEach((label, i) => {
    if (i % labelEvery) return;
    svg += `<text class="axis" x="${left + (i + 0.5) * slot}" y="${height - 4}" text-anchor="middle">${escapeHtml(label)}</text>`;
  });
  container.innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">${svg}</svg>`;
}

function renderSummary(s) {
  const kerchunkShare = s.rx_count ? ` · ${Math.round((100 * s.kerchunks) / s.rx_count)}%` : "";
  statsEl.innerHTML = [
    stat("User airtime", formatAirtime(s.rx_seconds), `${s.rx_count} transmissions`),
    stat("Transmitter on", formatAirtime(s.tx_seconds), "incl. tails, IDs, announcements"),
    stat("Longest", formatAirtime(s.longest_rx_seconds), "single transmission"),
    stat(
      "Kerchunks",
      String(s.kerchunks),
      KERCHUNK_NOTE + kerchunkShare + (s.kerchunks_filtered ? ` · ${s.kerchunks_filtered} more filtered out` : ""),
    ),
    stat("Timeouts", String(s.timeouts), "timeout timer tripped"),
    stat("IDs / announcements", `${s.ids} / ${s.announcements}`),
  ].join("");

  const hours = [...Array(24).keys()];
  barChart(
    byHourEl,
    hours.map((h) => String(h).padStart(2, "0")),
    [{ values: s.by_hour, className: "bar-rx", title: (i) => `${String(i).padStart(2, "0")}:00 — ${formatAirtime(s.by_hour[i])}` }],
    3,
  );

  dailyCard.hidden = s.by_day.length < 3;
  if (!dailyCard.hidden) {
    const labels = s.by_day.map((d) => {
      const [, month, day] = d.date.split("-");
      return `${Number(month)}/${Number(day)}`;
    });
    const describe = (i) => {
      const d = s.by_day[i];
      return `${d.date}: users ${formatAirtime(d.rx_seconds)} (${d.rx_count}), transmitter ${formatAirtime(d.tx_seconds)}`;
    };
    barChart(
      byDayEl,
      labels,
      [
        { values: s.by_day.map((d) => d.tx_seconds), className: "bar-tx", title: describe },
        { values: s.by_day.map((d) => d.rx_seconds), className: "bar-rx", title: describe },
      ],
      Math.ceil(labels.length / 15),
    );
  }
}

function renderTransmissions(transmissions) {
  tbody.innerHTML = "";
  empty.hidden = transmissions.length > 0;
  for (const t of transmissions) {
    const row = document.createElement("tr");
    const flag = t.timed_out ? '<span class="tag tag-danger">timed out</span>' : t.duration < 1.5 ? '<span class="tag">kerchunk</span>' : "";
    row.innerHTML = `<td>${escapeHtml(formatTimestamp(t.started_at))}</td><td>${formatAirtime(t.duration)}</td><td>${flag}</td>`;
    tbody.appendChild(row);
  }
}

let shownRecordings = "";

function renderRecordings(recordings) {
  // Re-rendering would stop a recording that's playing, so only redraw on change.
  const ids = recordings.map((r) => r.id).join(",");
  if (ids === shownRecordings) return;
  shownRecordings = ids;
  recordingsTbody.innerHTML = "";
  recordingsEmpty.hidden = recordings.length > 0;
  for (const r of recordings) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(formatTimestamp(r.started_at))}</td>
      <td>${formatAirtime(r.duration)}</td>
      <td><audio controls preload="none" src="/api/recordings/${encodeURIComponent(r.id)}/audio"></audio></td>
      <td class="row-actions"><button type="button" class="btn btn-danger btn-sm">Delete</button></td>
    `;
    row.querySelector("button").addEventListener("click", () => removeRecording(r));
    recordingsTbody.appendChild(row);
  }
}

async function removeRecording(recording) {
  if (!confirm(`Delete the recording from ${formatTimestamp(recording.started_at)}?`)) return;
  try {
    await api(`/api/recordings/${encodeURIComponent(recording.id)}`, { method: "DELETE" });
    toast("Recording deleted");
    await load();
  } catch (error) {
    toastError(error);
  }
}

async function load() {
  const [summary, transmissions, recordings] = await Promise.all([
    api(`/api/activity/summary?days=${days}`),
    api("/api/activity/transmissions?limit=25"),
    api("/api/recordings?limit=50"),
  ]);
  renderSummary(summary);
  renderTransmissions(transmissions);
  renderRecordings(recordings);
}

export function initUsage() {
  for (const button of rangeButtons) {
    button.addEventListener("click", () => {
      days = Number(button.dataset.days);
      for (const b of rangeButtons) b.classList.toggle("active", b === button);
      load().catch(toastError);
    });
  }
  router.addEventListener("change", ({ detail }) => {
    if (detail === "activity") load().catch(toastError);
  });
  setInterval(() => {
    if (currentView === "activity" && !document.hidden) load().catch(() => {});
  }, REFRESH_MS);
  if (currentView === "activity") load().catch(toastError);
}
