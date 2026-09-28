import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { escapeHtml } from "./ui.js";

const REFRESH_MS = 5_000;
const AREAS_REFRESH_MS = 60_000;
const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const KM_PER_MILE = 1.609344;
const KM_PER_DEGREE = 111.195;
const TILE_ERRORS_BEFORE_OFFLINE = 4;
const LABEL_ZOOM = 11;
const FILTERS_KEY = "moreopenrepeater.mapHidden";

const CATEGORIES = {
  repeater: { label: "Repeaters", one: "Repeater", emoji: "📡", color: "#4f9dff" },
  digipeater: { label: "Digipeaters", one: "Digipeater", emoji: "🔁", color: "#b58cff" },
  mobile: { label: "Mobile", one: "Mobile", emoji: "🚗", color: "#35d07f" },
  fixed: { label: "Fixed", one: "Fixed", emoji: "📍", color: "#c9ced6" },
  weather: { label: "Weather", one: "Weather station", emoji: "🌡️", color: "#ffb020" },
};
const LAYERS = { alerts: "⚠️ Weather alerts", trails: "〰️ Trails" };
const SEVERITY_COLORS = { Extreme: "#d946ef", Severe: "#ff5c5c", Moderate: "#ffb020", Minor: "#facc15" };
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

// APRS symbol code -> emoji. Drawn rather than using the APRS symbol sprite
// sheets, whose licensing is murky; the two tables mostly agree on meaning.
const SYMBOLS = {
  ">": "🚗", k: "🛻", u: "🚚", v: "🚐", j: "🚙", R: "🚐", U: "🚌", b: "🚲", "<": "🏍️", "[": "🚶",
  X: "🚁", "^": "✈️", "'": "🛩️", O: "🎈", s: "🚤", Y: "⛵", C: "🛶", "=": "🚆",
  f: "🚒", a: "🚑", P: "🚓", "!": "🚔", h: "🏥", "+": "➕", o: "🏛️", K: "🏫", ";": "⛺",
  "-": "🏠", y: "🏠", _: "🌡️", W: "🌩️", r: "📡", "`": "📡", "#": "🔁", "&": "🌐", $: "☎️",
};

const mapEl = document.getElementById("map");
const statusEl = document.getElementById("map-status");
const filtersEl = document.getElementById("map-filters");
const tileNote = document.getElementById("map-tile-note");
const setupCard = document.getElementById("map-setup");
const setupText = document.getElementById("map-setup-text");
const tbody = document.getElementById("map-tbody");
const empty = document.getElementById("map-empty");
const countEl = document.getElementById("map-count");

const hidden = new Set(JSON.parse(localStorage.getItem(FILTERS_KEY) ?? "[]"));
let L = null;
let map = null;
let leafletReady = null;
let rings, alerts, trails, stationLayer;
let tileLayer = null;
let tileUrl = null;
let centerKey = null;
let latest = null;
let areas = null;
let timers = [];
const markers = new Map(); // station name -> { marker, icon, popup }

function loadLeaflet() {
  leafletReady ??= new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "/vendor/leaflet/leaflet.css";
    document.head.appendChild(css);
    const script = document.createElement("script");
    script.src = "/vendor/leaflet/leaflet.js";
    script.onload = () => resolve(window.L);
    script.onerror = () => {
      leafletReady = null;
      reject(new Error("Couldn't load the map library"));
    };
    document.head.appendChild(script);
  });
  return leafletReady;
}

let mapReady = null;
function ensureMap() {
  mapReady ??= createMap().catch((error) => {
    mapReady = null;
    throw error;
  });
  return mapReady;
}

async function createMap() {
  L = await loadLeaflet();
  map = L.map(mapEl, { worldCopyJump: true });
  map.setView([39.8, -98.6], 4);
  alerts = L.geoJSON(null, {
    style: (feature) => {
      const color = SEVERITY_COLORS[feature.properties.severity] ?? "#8a919c";
      return { color, weight: 2, opacity: 0.8, fillColor: color, fillOpacity: 0.12 };
    },
    onEachFeature: (feature, layer) => layer.bindPopup(alertPopup(feature.properties)),
  });
  rings = L.layerGroup().addTo(map);
  trails = L.layerGroup().addTo(map);
  stationLayer = L.layerGroup().addTo(map);
  map.on("zoomend", () => mapEl.classList.toggle("map-labels", map.getZoom() >= LABEL_ZOOM));
}

// ---------- formatting ----------

function compass(degrees) {
  return degrees == null ? "" : COMPASS[Math.round(degrees / 22.5) % 16];
}

function distance(km, units) {
  const value = units === "mi" ? km / KM_PER_MILE : km;
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units}`;
}

function speed(kmh, units) {
  return units === "mi" ? `${Math.round(kmh / KM_PER_MILE)} mph` : `${Math.round(kmh)} km/h`;
}

function ago(seconds) {
  const elapsed = Math.max(0, Date.now() / 1000 - seconds);
  if (elapsed < 60) return `${Math.round(elapsed)} s ago`;
  if (elapsed < 3600) return `${Math.round(elapsed / 60)} min ago`;
  return `${(elapsed / 3600).toFixed(1)} h ago`;
}

function weatherRows(w, units) {
  const rows = [];
  if (w.temp_f != null) rows.push(["Temperature", units === "mi" ? `${w.temp_f} °F` : `${Math.round(((w.temp_f - 32) * 5) / 9)} °C`]);
  if (w.wind_mph != null) {
    const wind = units === "mi" ? `${w.wind_mph} mph` : `${Math.round(w.wind_mph * KM_PER_MILE)} km/h`;
    const gust = w.wind_gust_mph ? `, gusting ${units === "mi" ? w.wind_gust_mph : Math.round(w.wind_gust_mph * KM_PER_MILE)}` : "";
    rows.push(["Wind", `${wind}${w.wind_dir != null ? ` from ${compass(w.wind_dir)}` : ""}${gust}`]);
  }
  if (w.humidity != null) rows.push(["Humidity", `${w.humidity}%`]);
  if (w.pressure_mbar != null) rows.push(["Pressure", `${w.pressure_mbar} mbar`]);
  if (w.rain_24h_in != null) rows.push(["Rain (24 h)", units === "mi" ? `${w.rain_24h_in} in` : `${Math.round(w.rain_24h_in * 25.4)} mm`]);
  return rows;
}

function emoji(station) {
  return SYMBOLS[station.symbol_code] ?? CATEGORIES[station.category]?.emoji ?? "📍";
}

function stationPopup(s, units) {
  const rows = [["Type", CATEGORIES[s.category]?.one ?? s.category]];
  if (s.kind !== "station") rows.push([s.kind === "item" ? "Item from" : "Object from", s.source]);
  if (s.distance_km != null) rows.push(["Distance", `${distance(s.distance_km, units)} ${compass(s.bearing)}`]);
  if (s.speed_kmh != null) rows.push(["Speed", `${speed(s.speed_kmh, units)}${s.course ? ` heading ${compass(s.course)}` : ""}`]);
  if (s.altitude_m != null) rows.push(["Altitude", units === "mi" ? `${Math.round(s.altitude_m * 3.28084)} ft` : `${Math.round(s.altitude_m)} m`]);
  if (s.weather) rows.push(...weatherRows(s.weather, units));
  rows.push(["Heard", `${ago(s.last_heard)} · ${s.packets} packet${s.packets === 1 ? "" : "s"}`]);
  const call = s.kind === "station" ? s.name : s.source;
  return `
    <strong>${escapeHtml(s.name)}</strong>
    ${s.comment ? `<div class="map-popup-comment">${escapeHtml(s.comment)}</div>` : ""}
    <dl class="kv small">${rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>
    <a class="small" href="https://aprs.fi/#!call=a%2F${encodeURIComponent(call)}" target="_blank" rel="noopener">View on aprs.fi</a>
  `;
}

function alertPopup(p) {
  return `
    <strong>${escapeHtml(p.event)}</strong>
    <div class="small">${escapeHtml(p.area)}</div>
    ${p.headline ? `<div class="small muted">${escapeHtml(p.headline)}</div>` : ""}
  `;
}

// ---------- layers ----------

function ringDistances(radiusKm, units) {
  const unitKm = units === "mi" ? KM_PER_MILE : 1;
  const radius = radiusKm / unitKm;
  const step = [1, 2, 5, 10, 20, 25, 50, 100, 200].find((s) => radius / s <= 4) ?? 250;
  const out = [];
  for (let d = step; d < radius * 0.85; d += step) out.push({ km: d * unitKm, label: `${d} ${units}` });
  out.push({ km: radiusKm, label: `${+radius.toFixed(1)} ${units}`, edge: true });
  return out;
}

function applyCenter(body) {
  const key = JSON.stringify([body.center, body.radius_km, body.distance_units]);
  if (key === centerKey) return;
  centerKey = key;
  rings.clearLayers();
  if (!body.center) return;
  const center = L.latLng(body.center);
  for (const ring of ringDistances(body.radius_km, body.distance_units)) {
    L.circle(center, {
      radius: ring.km * 1000, color: "#4f9dff", weight: ring.edge ? 2 : 1.5, opacity: ring.edge ? 0.9 : 0.7,
      dashArray: ring.edge ? null : "4 6", fill: false, interactive: false,
    }).addTo(rings);
    L.marker([center.lat + ring.km / KM_PER_DEGREE, center.lng], {
      icon: L.divIcon({ className: "map-ring-label", html: escapeHtml(ring.label), iconSize: null }),
      interactive: false, keyboard: false,
    }).addTo(rings);
  }
  L.marker(center, {
    icon: L.divIcon({ className: "map-home", html: "<span></span>", iconSize: [18, 18] }),
    title: "The repeater", zIndexOffset: 1000, keyboard: false,
  }).bindPopup("<strong>The repeater</strong>").addTo(rings);
  map.fitBounds(center.toBounds(body.radius_km * 2000));
}

function setOffline(offline, note = "") {
  mapEl.classList.toggle("map-offline", offline);
  tileNote.textContent = note;
  tileNote.hidden = !note;
}

function applyTiles(url) {
  if (url === tileUrl) return;
  tileUrl = url;
  tileLayer?.remove();
  tileLayer = null;
  if (!url) {
    setOffline(true, "No background map is set, so only range rings are shown.");
    return;
  }
  setOffline(false);
  let loaded = false;
  let errors = 0;
  tileLayer = L.tileLayer(url, { maxZoom: 18, attribution: url.includes("openstreetmap.org") ? OSM_ATTRIBUTION : "" });
  tileLayer.on("tileload", () => {
    if (!loaded) setOffline(false);
    loaded = true;
  });
  tileLayer.on("tileerror", () => {
    errors += 1;
    if (!loaded && errors === TILE_ERRORS_BEFORE_OFFLINE) {
      setOffline(true, "Map tiles couldn't be loaded (no internet?), so only range rings are shown.");
    }
  });
  tileLayer.addTo(map);
}

function renderStations(body) {
  const units = body.distance_units;
  const seen = new Set();
  trails.clearLayers();
  for (const s of body.stations) {
    seen.add(s.name);
    const color = CATEGORIES[s.category]?.color ?? "#c9ced6";
    const icon = `<span class="map-icon" style="border-color:${color}">${emoji(s)}</span><span class="map-label">${escapeHtml(s.name)}</span>`;
    const popup = stationPopup(s, units);
    let entry = markers.get(s.name);
    if (!entry) {
      entry = { marker: L.marker([s.lat, s.lon], { title: s.name, riseOnHover: true }).bindPopup(""), icon: "", popup: "" };
      markers.set(s.name, entry);
    }
    entry.marker.setLatLng([s.lat, s.lon]);
    if (entry.icon !== icon) {
      entry.marker.setIcon(L.divIcon({ className: "map-station", html: icon, iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -14] }));
      entry.icon = icon;
    }
    if (entry.popup !== popup) {
      entry.marker.setPopupContent(popup);
      entry.popup = popup;
    }
    const visible = !hidden.has(s.category);
    if (visible) stationLayer.addLayer(entry.marker);
    else stationLayer.removeLayer(entry.marker);
    if (visible && !hidden.has("trails") && s.trail.length > 1) {
      L.polyline(s.trail, { color, weight: 2, opacity: 0.7, interactive: false }).addTo(trails);
    }
  }
  for (const [name, entry] of markers) {
    if (!seen.has(name)) {
      stationLayer.removeLayer(entry.marker);
      markers.delete(name);
    }
  }
}

function renderAlerts() {
  alerts.clearLayers();
  if (areas) alerts.addData(areas);
  if (hidden.has("alerts")) alerts.remove();
  else alerts.addTo(map);
}

// ---------- page ----------

function renderFilters(body) {
  const counts = {};
  for (const s of body.stations) counts[s.category] = (counts[s.category] ?? 0) + 1;
  const chips = Object.entries(CATEGORIES).map(
    ([key, c]) => [key, `${c.emoji} ${c.label} <span class="map-filter-count">${counts[key] ?? 0}</span>`],
  );
  chips.push(...Object.entries(LAYERS));
  filtersEl.innerHTML = chips
    .map(([key, html]) => `<button type="button" class="map-filter" data-filter="${key}" aria-pressed="${!hidden.has(key)}">${html}</button>`)
    .join("");
}

function renderTable(body) {
  const units = body.distance_units;
  const shown = body.stations
    .filter((s) => !hidden.has(s.category))
    .sort((a, b) => (a.distance_km ?? Infinity) - (b.distance_km ?? Infinity));
  countEl.textContent = body.stations.length ? `${shown.length} of ${body.stations.length} in the last ${body.hours} h` : "";
  empty.hidden = shown.length > 0;
  empty.textContent = body.stations.length ? "Every station type is filtered out." : "No stations heard yet.";
  tbody.innerHTML = shown
    .map(
      (s) => `
      <tr class="clickable" data-station="${escapeHtml(s.name)}">
        <td><span class="map-table-icon">${emoji(s)}</span> <strong>${escapeHtml(s.name)}</strong>
          ${s.kind !== "station" ? `<span class="muted small">via ${escapeHtml(s.source)}</span>` : ""}</td>
        <td>${escapeHtml(CATEGORIES[s.category]?.one ?? s.category)}</td>
        <td class="nowrap">${s.distance_km != null ? `${distance(s.distance_km, units)} ${compass(s.bearing)}` : ""}</td>
        <td class="nowrap">${ago(s.last_heard)}</td>
        <td class="small">${escapeHtml(s.comment)}</td>
      </tr>`,
    )
    .join("");
}

function describe(body) {
  if (body.connected) {
    const last = body.last_packet ? `, last ${ago(new Date(body.last_packet).getTime() / 1000)}` : "";
    return `Receiving from ${body.server}: ${body.packets.toLocaleString()} packets${last}.`;
  }
  if (!body.enabled || !body.center) return "Not receiving.";
  if (body.error) return `Not connected (${body.error}). Retrying…`;
  return `Connecting to ${body.server || "APRS-IS"}…`;
}

function renderSetup(body) {
  let text = "";
  if (!body.enabled) text = "The map is switched off. Turn it on under APRS settings to start receiving nearby stations.";
  else if (!body.center) text = "Set the repeater's latitude and longitude under APRS settings so the map knows where to look.";
  setupText.textContent = text;
  setupCard.hidden = !text;
}

function render(body) {
  latest = body;
  statusEl.textContent = describe(body);
  renderSetup(body);
  applyTiles(body.tiles);
  applyCenter(body);
  renderStations(body);
  renderFilters(body);
  renderTable(body);
}

async function refresh() {
  try {
    const body = await api("/api/aprs/stations");
    await ensureMap();
    render(body);
  } catch (error) {
    statusEl.textContent = error.message;
  }
}

async function refreshAreas() {
  try {
    areas = await api("/api/weather/areas");
    await ensureMap();
    renderAlerts();
  } catch {
    // The map is still useful without alert outlines.
  }
}

function start() {
  if (timers.length) return;
  refresh().then(() => map?.invalidateSize());
  refreshAreas();
  timers = [
    setInterval(() => !document.hidden && refresh(), REFRESH_MS),
    setInterval(() => !document.hidden && refreshAreas(), AREAS_REFRESH_MS),
  ];
}

function stop() {
  timers.forEach(clearInterval);
  timers = [];
}

export function initMap() {
  filtersEl.addEventListener("click", (event) => {
    const button = event.target.closest("[data-filter]");
    if (!button) return;
    const key = button.dataset.filter;
    if (hidden.has(key)) hidden.delete(key);
    else hidden.add(key);
    localStorage.setItem(FILTERS_KEY, JSON.stringify([...hidden]));
    if (latest) render(latest);
    if (key === "alerts") renderAlerts();
  });
  tbody.addEventListener("click", (event) => {
    const entry = markers.get(event.target.closest("[data-station]")?.dataset.station);
    if (!entry) return;
    map.setView(entry.marker.getLatLng(), Math.max(map.getZoom(), 12));
    entry.marker.openPopup();
    mapEl.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  document.getElementById("aprs-tiles-default").addEventListener("click", (event) => {
    const input = event.target.form.elements.aprs_map_tiles;
    input.value = OSM_TILES;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  router.addEventListener("change", ({ detail }) => (detail === "map" ? start() : stop()));
  if (currentView === "map") start();
}
