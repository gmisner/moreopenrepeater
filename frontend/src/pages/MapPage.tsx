import { Badge, Button, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Page } from "../layout/AppShell";
import "./MapPage.css";

const REFRESH_MS = 5_000;
const AREAS_REFRESH_MS = 60_000;
const KM_PER_MILE = 1.609344;
const KM_PER_DEGREE = 111.195;
const TILE_ERRORS_BEFORE_OFFLINE = 4;
const LABEL_ZOOM = 11;
const FILTERS_KEY = "moreopenrepeater.mapHidden";
const LEAFLET_CSS = "/vendor/leaflet/leaflet.css";
const LEAFLET_JS = "/vendor/leaflet/leaflet.js";

const CATEGORIES: Record<string, { label: string; one: string; letter: string; color: string }> = {
  repeater: { label: "Repeaters", one: "Repeater", letter: "R", color: "#4f9dff" },
  digipeater: { label: "Digipeaters", one: "Digipeater", letter: "D", color: "#b58cff" },
  mobile: { label: "Mobile", one: "Mobile", letter: "M", color: "#35d07f" },
  fixed: { label: "Fixed", one: "Fixed", letter: "F", color: "#c9ced6" },
  weather: { label: "Weather", one: "Weather station", letter: "W", color: "#ffb020" },
};

const LAYERS: Record<string, string> = {
  alerts: "Weather alerts",
  trails: "Trails",
};

const SEVERITY_COLORS: Record<string, string> = {
  Extreme: "#d946ef",
  Severe: "#ff5c5c",
  Moderate: "#ffb020",
  Minor: "#facc15",
};

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

type Weather = {
  temp_f?: number | null;
  wind_mph?: number | null;
  wind_gust_mph?: number | null;
  wind_dir?: number | null;
  humidity?: number | null;
  pressure_mbar?: number | null;
  rain_24h_in?: number | null;
};

type Station = {
  name: string;
  source: string;
  kind: string;
  category: string;
  lat: number;
  lon: number;
  symbol_code: string;
  comment: string;
  course?: number | null;
  speed_kmh?: number | null;
  altitude_m?: number | null;
  weather?: Weather | null;
  last_heard: number;
  packets: number;
  trail: number[][];
  distance_km?: number | null;
  bearing?: number | null;
};

type MapBody = {
  enabled: boolean;
  center: number[] | null;
  radius_km: number;
  hours: number;
  tiles: string;
  distance_units: "mi" | "km";
  connected: boolean;
  server: string;
  error?: string | null;
  last_packet?: string | null;
  packets: number;
  stations: Station[];
};

type LeafletNS = {
  map: (el: HTMLElement, opts?: object) => LeafletMap;
  tileLayer: (url: string, opts?: object) => LeafletLayer;
  layerGroup: () => LeafletLayerGroup;
  geoJSON: (data: unknown, opts?: object) => LeafletGeoJSON;
  circle: (latlng: unknown, opts?: object) => LeafletLayer;
  marker: (latlng: unknown, opts?: object) => LeafletMarker;
  polyline: (latlngs: unknown, opts?: object) => LeafletLayer;
  latLng: (latlng: unknown) => { lat: number; lng: number; toBounds: (size: number) => unknown };
  divIcon: (opts: object) => unknown;
};

type LeafletMap = {
  setView: (latlng: unknown, zoom?: number) => void;
  fitBounds: (bounds: unknown) => void;
  getZoom: () => number;
  invalidateSize: () => void;
  on: (event: string, fn: () => void) => void;
  remove: () => void;
};

type LeafletLayer = {
  addTo: (map: unknown) => LeafletLayer;
  remove: () => void;
  on: (event: string, fn: () => void) => void;
};

type LeafletLayerGroup = {
  addTo: (map: unknown) => LeafletLayerGroup;
  remove: () => void;
  on: (event: string, fn: () => void) => void;
  clearLayers: () => void;
  addLayer: (layer: unknown) => void;
  removeLayer: (layer: unknown) => void;
};

type LeafletGeoJSON = LeafletLayerGroup & {
  addData: (data: unknown) => void;
  addTo: (map: unknown) => LeafletGeoJSON;
};

type LeafletMarker = {
  setLatLng: (latlng: unknown) => void;
  setIcon: (icon: unknown) => void;
  bindPopup: (html: string) => LeafletMarker;
  setPopupContent: (html: string) => void;
  openPopup: () => void;
  getLatLng: () => unknown;
  addTo: (map: unknown) => LeafletMarker;
};

declare global {
  interface Window {
    L?: LeafletNS;
  }
}

function loadLeaflet(): Promise<LeafletNS> {
  if (window.L) return Promise.resolve(window.L);
  return new Promise((resolve, reject) => {
    if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = LEAFLET_CSS;
      document.head.appendChild(css);
    }
    const existing = document.querySelector(`script[src="${LEAFLET_JS}"]`) as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener("load", () => (window.L ? resolve(window.L) : reject(new Error("Couldn't load the map library"))));
      existing.addEventListener("error", () => reject(new Error("Couldn't load the map library")));
      if (window.L) resolve(window.L);
      return;
    }
    const script = document.createElement("script");
    script.src = LEAFLET_JS;
    script.onload = () => (window.L ? resolve(window.L) : reject(new Error("Couldn't load the map library")));
    script.onerror = () => reject(new Error("Couldn't load the map library"));
    document.head.appendChild(script);
  });
}

function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function compass(degrees: number | null | undefined) {
  return degrees == null ? "" : COMPASS[Math.round(degrees / 22.5) % 16];
}

function distance(km: number, units: "mi" | "km") {
  const value = units === "mi" ? km / KM_PER_MILE : km;
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units}`;
}

function speed(kmh: number, units: "mi" | "km") {
  return units === "mi" ? `${Math.round(kmh / KM_PER_MILE)} mph` : `${Math.round(kmh)} km/h`;
}

function ago(seconds: number) {
  const elapsed = Math.max(0, Date.now() / 1000 - seconds);
  if (elapsed < 60) return `${Math.round(elapsed)} s ago`;
  if (elapsed < 3600) return `${Math.round(elapsed / 60)} min ago`;
  return `${(elapsed / 3600).toFixed(1)} h ago`;
}

function weatherRows(w: Weather, units: "mi" | "km") {
  const rows: [string, string][] = [];
  if (w.temp_f != null) {
    rows.push(["Temperature", units === "mi" ? `${w.temp_f} °F` : `${Math.round(((w.temp_f - 32) * 5) / 9)} °C`]);
  }
  if (w.wind_mph != null) {
    const wind = units === "mi" ? `${w.wind_mph} mph` : `${Math.round(w.wind_mph * KM_PER_MILE)} km/h`;
    const gust = w.wind_gust_mph
      ? `, gusting ${units === "mi" ? w.wind_gust_mph : Math.round(w.wind_gust_mph * KM_PER_MILE)}`
      : "";
    rows.push(["Wind", `${wind}${w.wind_dir != null ? ` from ${compass(w.wind_dir)}` : ""}${gust}`]);
  }
  if (w.humidity != null) rows.push(["Humidity", `${w.humidity}%`]);
  if (w.pressure_mbar != null) rows.push(["Pressure", `${w.pressure_mbar} mbar`]);
  if (w.rain_24h_in != null) {
    rows.push(["Rain (24 h)", units === "mi" ? `${w.rain_24h_in} in` : `${Math.round(w.rain_24h_in * 25.4)} mm`]);
  }
  return rows;
}

function stationPopup(s: Station, units: "mi" | "km") {
  const rows: [string, string][] = [["Type", CATEGORIES[s.category]?.one ?? s.category]];
  if (s.kind !== "station") rows.push([s.kind === "item" ? "Item from" : "Object from", s.source]);
  if (s.distance_km != null) rows.push(["Distance", `${distance(s.distance_km, units)} ${compass(s.bearing)}`]);
  if (s.speed_kmh != null) {
    rows.push(["Speed", `${speed(s.speed_kmh, units)}${s.course ? ` heading ${compass(s.course)}` : ""}`]);
  }
  if (s.altitude_m != null) {
    rows.push(["Altitude", units === "mi" ? `${Math.round(s.altitude_m * 3.28084)} ft` : `${Math.round(s.altitude_m)} m`]);
  }
  if (s.weather) rows.push(...weatherRows(s.weather, units));
  rows.push(["Heard", `${ago(s.last_heard)} · ${s.packets} packet${s.packets === 1 ? "" : "s"}`]);
  const call = s.kind === "station" ? s.name : s.source;
  return `
    <strong>${escapeHtml(s.name)}</strong>
    ${s.comment ? `<div class="mor-map-popup-comment">${escapeHtml(s.comment)}</div>` : ""}
    <dl class="mor-map-popup-kv">${rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>
    <a href="https://aprs.fi/#!call=a%2F${encodeURIComponent(call)}" target="_blank" rel="noopener">View on aprs.fi</a>
  `;
}

function alertPopup(p: { event?: string; area?: string; headline?: string }) {
  return `
    <strong>${escapeHtml(p.event || "")}</strong>
    <div>${escapeHtml(p.area || "")}</div>
    ${p.headline ? `<div style="opacity:0.7">${escapeHtml(p.headline)}</div>` : ""}
  `;
}

function ringDistances(radiusKm: number, units: "mi" | "km") {
  const unitKm = units === "mi" ? KM_PER_MILE : 1;
  const radius = radiusKm / unitKm;
  const step = [1, 2, 5, 10, 20, 25, 50, 100, 200].find((s) => radius / s <= 4) ?? 250;
  const out: { km: number; label: string; edge?: boolean }[] = [];
  for (let d = step; d < radius * 0.85; d += step) out.push({ km: d * unitKm, label: `${d} ${units}` });
  out.push({ km: radiusKm, label: `${+radius.toFixed(1)} ${units}`, edge: true });
  return out;
}

function describe(body: MapBody) {
  if (body.connected) {
    const last = body.last_packet ? `, last ${ago(new Date(body.last_packet).getTime() / 1000)}` : "";
    return `Receiving from ${body.server}: ${body.packets.toLocaleString()} packets${last}.`;
  }
  if (!body.enabled || !body.center) return "Not receiving.";
  if (body.error) return `Not connected (${body.error}). Retrying…`;
  return `Connecting to ${body.server || "APRS-IS"}…`;
}

function setupText(body: MapBody) {
  if (!body.enabled) return "The map is switched off. Turn it on under APRS settings to start receiving nearby stations.";
  if (!body.center) return "Set the repeater's latitude and longitude under APRS settings so the map knows where to look.";
  return "";
}

function readHidden(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(FILTERS_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

export function MapPage() {
  const mapEl = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const LRef = useRef<LeafletNS | null>(null);
  const layersRef = useRef<{
    rings: LeafletLayerGroup;
    alerts: LeafletGeoJSON;
    trails: LeafletLayerGroup;
    stations: LeafletLayerGroup;
    tile: LeafletLayer | null;
  } | null>(null);
  const markersRef = useRef(new Map<string, { marker: LeafletMarker; icon: string; popup: string }>());
  const centerKeyRef = useRef<string | null>(null);
  const tileUrlRef = useRef<string | null>(null);
  const areasRef = useRef<unknown>(null);

  const [body, setBody] = useState<MapBody | null>(null);
  const [statusText, setStatusText] = useState("Loading…");
  const [tileNote, setTileNote] = useState("");
  const [offline, setOffline] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(readHidden);
  const hiddenRef = useRef(hidden);
  const [mapError, setMapError] = useState<string | null>(null);

  useEffect(() => {
    hiddenRef.current = hidden;
  }, [hidden]);

  const ensureMap = useCallback(async () => {
    if (mapRef.current && LRef.current) return { L: LRef.current, map: mapRef.current };
    const L = await loadLeaflet();
    if (!mapEl.current) throw new Error("Map container missing");
    LRef.current = L;
    const map = L.map(mapEl.current, { worldCopyJump: true });
    map.setView([39.8, -98.6], 4);
    const alerts = L.geoJSON(null, {
      style: (feature: { properties?: { severity?: string } }) => {
        const color = SEVERITY_COLORS[feature.properties?.severity || ""] ?? "#8a919c";
        return { color, weight: 2, opacity: 0.8, fillColor: color, fillOpacity: 0.12 };
      },
      onEachFeature: (feature: { properties?: object }, layer: { bindPopup: (html: string) => void }) =>
        layer.bindPopup(alertPopup((feature.properties || {}) as { event?: string; area?: string; headline?: string })),
    });
    const rings = L.layerGroup().addTo(map);
    const trails = L.layerGroup().addTo(map);
    const stations = L.layerGroup().addTo(map);
    layersRef.current = { rings, alerts, trails, stations, tile: null };
    map.on("zoomend", () => {
      mapEl.current?.classList.toggle("mor-map-labels", map.getZoom() >= LABEL_ZOOM);
    });
    mapRef.current = map;
    return { L, map };
  }, []);

  const applyTiles = useCallback((L: LeafletNS, map: LeafletMap, url: string) => {
    const layers = layersRef.current;
    if (!layers || url === tileUrlRef.current) return;
    tileUrlRef.current = url;
    layers.tile?.remove();
    layers.tile = null;
    if (!url) {
      setOffline(true);
      setTileNote("No background map is set, so only range rings are shown.");
      return;
    }
    setOffline(false);
    setTileNote("");
    let loaded = false;
    let errors = 0;
    const tile = L.tileLayer(url, {
      maxZoom: 18,
      attribution: url.includes("openstreetmap.org")
        ? '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        : "",
    });
    tile.on("tileload", () => {
      if (!loaded) {
        setOffline(false);
        setTileNote("");
      }
      loaded = true;
    });
    tile.on("tileerror", () => {
      errors += 1;
      if (!loaded && errors === TILE_ERRORS_BEFORE_OFFLINE) {
        setOffline(true);
        setTileNote("Map tiles couldn't be loaded (no internet?), so only range rings are shown.");
      }
    });
    tile.addTo(map);
    layers.tile = tile;
  }, []);

  const applyCenter = useCallback((L: LeafletNS, map: LeafletMap, next: MapBody) => {
    const layers = layersRef.current;
    if (!layers) return;
    const key = JSON.stringify([next.center, next.radius_km, next.distance_units]);
    if (key === centerKeyRef.current) return;
    centerKeyRef.current = key;
    layers.rings.clearLayers();
    if (!next.center) return;
    const center = L.latLng(next.center);
    for (const ring of ringDistances(next.radius_km, next.distance_units)) {
      L.circle(center, {
        radius: ring.km * 1000,
        color: "#4f9dff",
        weight: ring.edge ? 2 : 1.5,
        opacity: ring.edge ? 0.9 : 0.7,
        dashArray: ring.edge ? null : "4 6",
        fill: false,
        interactive: false,
      }).addTo(layers.rings);
      L.marker([center.lat + ring.km / KM_PER_DEGREE, center.lng], {
        icon: L.divIcon({ className: "mor-map-ring-label", html: escapeHtml(ring.label), iconSize: null }),
        interactive: false,
        keyboard: false,
      }).addTo(layers.rings);
    }
    L.marker(center, {
      icon: L.divIcon({ className: "mor-map-home", html: "<span></span>", iconSize: [18, 18] }),
      title: "The repeater",
      zIndexOffset: 1000,
      keyboard: false,
    })
      .bindPopup("<strong>The repeater</strong>")
      .addTo(layers.rings);
    map.fitBounds(center.toBounds(next.radius_km * 2000));
  }, []);

  const renderStations = useCallback(
    (L: LeafletNS, next: MapBody, filterHidden: Set<string>) => {
      const layers = layersRef.current;
      if (!layers) return;
      const units = next.distance_units;
      const seen = new Set<string>();
      layers.trails.clearLayers();
      for (const s of next.stations) {
        seen.add(s.name);
        const color = CATEGORIES[s.category]?.color ?? "#c9ced6";
        const letter = CATEGORIES[s.category]?.letter ?? "?";
        const icon = `<span class="mor-map-icon" style="border-color:${color}">${letter}</span><span class="mor-map-label">${escapeHtml(s.name)}</span>`;
        const popup = stationPopup(s, units);
        let entry = markersRef.current.get(s.name);
        if (!entry) {
          entry = {
            marker: L.marker([s.lat, s.lon], { title: s.name, riseOnHover: true }).bindPopup(""),
            icon: "",
            popup: "",
          };
          markersRef.current.set(s.name, entry);
        }
        entry.marker.setLatLng([s.lat, s.lon]);
        if (entry.icon !== icon) {
          entry.marker.setIcon(
            L.divIcon({
              className: "mor-map-station",
              html: icon,
              iconSize: [28, 28],
              iconAnchor: [14, 14],
              popupAnchor: [0, -14],
            }),
          );
          entry.icon = icon;
        }
        if (entry.popup !== popup) {
          entry.marker.setPopupContent(popup);
          entry.popup = popup;
        }
        const visible = !filterHidden.has(s.category);
        if (visible) layers.stations.addLayer(entry.marker);
        else layers.stations.removeLayer(entry.marker);
        if (visible && !filterHidden.has("trails") && s.trail.length > 1) {
          L.polyline(s.trail, { color, weight: 2, opacity: 0.7, interactive: false }).addTo(layers.trails);
        }
      }
      for (const [name, entry] of markersRef.current) {
        if (!seen.has(name)) {
          layers.stations.removeLayer(entry.marker);
          markersRef.current.delete(name);
        }
      }
    },
    [],
  );

  const renderAlerts = useCallback((filterHidden: Set<string>) => {
    const layers = layersRef.current;
    const map = mapRef.current;
    if (!layers || !map) return;
    layers.alerts.clearLayers();
    if (areasRef.current) layers.alerts.addData(areasRef.current);
    if (filterHidden.has("alerts")) layers.alerts.remove();
    else layers.alerts.addTo(map);
  }, []);

  const paint = useCallback(
    async (next: MapBody, filterHidden: Set<string>) => {
      const { L, map } = await ensureMap();
      applyTiles(L, map, next.tiles);
      applyCenter(L, map, next);
      renderStations(L, next, filterHidden);
      renderAlerts(filterHidden);
      map.invalidateSize();
    },
    [applyCenter, applyTiles, ensureMap, renderAlerts, renderStations],
  );

  const refresh = useCallback(async () => {
    try {
      const next = await api<MapBody>("/api/aprs/stations");
      setBody(next);
      setStatusText(describe(next));
      setMapError(null);
      await paint(next, hiddenRef.current);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
      setMapError(error instanceof Error ? error.message : String(error));
    }
  }, [paint]);

  const refreshAreas = useCallback(async () => {
    try {
      areasRef.current = await api("/api/weather/areas");
      await ensureMap();
      renderAlerts(hiddenRef.current);
    } catch {
      // Map is still useful without alert outlines.
    }
  }, [ensureMap, renderAlerts]);

  useEffect(() => {
    void refresh();
    void refreshAreas();
    const container = mapEl.current;
    const timers = [
      window.setInterval(() => {
        if (!document.hidden) void refresh();
      }, REFRESH_MS),
      window.setInterval(() => {
        if (!document.hidden) void refreshAreas();
      }, AREAS_REFRESH_MS),
    ];
    return () => {
      timers.forEach(clearInterval);
      mapRef.current?.remove();
      mapRef.current = null;
      layersRef.current = null;
      markersRef.current.clear();
      centerKeyRef.current = null;
      tileUrlRef.current = null;
      if (container) container.innerHTML = "";
    };
  }, [refresh, refreshAreas]);

  useEffect(() => {
    if (!body) return;
    void paint(body, hidden);
  }, [hidden]); // eslint-disable-line react-hooks/exhaustive-deps -- re-paint when filters change

  const toggleFilter = (key: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      localStorage.setItem(FILTERS_KEY, JSON.stringify([...next]));
      return next;
    });
  };

  const focusStation = (name: string) => {
    const entry = markersRef.current.get(name);
    const map = mapRef.current;
    if (!entry || !map) return;
    map.setView(entry.marker.getLatLng(), Math.max(map.getZoom(), 12));
    entry.marker.openPopup();
    mapEl.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const s of body?.stations || []) out[s.category] = (out[s.category] ?? 0) + 1;
    return out;
  }, [body]);

  const shown = useMemo(() => {
    if (!body) return [];
    return body.stations
      .filter((s) => !hidden.has(s.category))
      .sort((a, b) => (a.distance_km ?? Infinity) - (b.distance_km ?? Infinity));
  }, [body, hidden]);

  const setup = body ? setupText(body) : "";

  return (
    <Page title="APRS map" description="Stations heard on APRS-IS near the repeater, with active weather alert areas.">
      {setup ? (
        <Card withBorder padding="lg" radius="md">
          <Text c="dimmed" mb="sm">
            {setup}
          </Text>
          <Button component={Link} to="/aprs" variant="subtle" size="compact-sm">
            APRS settings
          </Button>
        </Card>
      ) : null}

      <Card withBorder padding="lg" radius="md">
        <Stack gap="sm">
          <Group justify="space-between" align="flex-start" wrap="wrap">
            <Text size="sm" c="dimmed">
              {statusText}
            </Text>
            <Group gap="xs" wrap="wrap">
              {Object.entries(CATEGORIES).map(([key, cat]) => (
                <Button
                  key={key}
                  size="compact-xs"
                  variant={hidden.has(key) ? "default" : "light"}
                  color={hidden.has(key) ? "gray" : undefined}
                  style={{ opacity: hidden.has(key) ? 0.5 : 1, textDecoration: hidden.has(key) ? "line-through" : undefined }}
                  onClick={() => toggleFilter(key)}
                >
                  {cat.label}{" "}
                  <Text span size="xs" c="dimmed" ml={4}>
                    {counts[key] ?? 0}
                  </Text>
                </Button>
              ))}
              {Object.entries(LAYERS).map(([key, label]) => (
                <Button
                  key={key}
                  size="compact-xs"
                  variant={hidden.has(key) ? "default" : "light"}
                  style={{ opacity: hidden.has(key) ? 0.5 : 1, textDecoration: hidden.has(key) ? "line-through" : undefined }}
                  onClick={() => toggleFilter(key)}
                >
                  {label}
                </Button>
              ))}
            </Group>
          </Group>

          <div
            ref={mapEl}
            className={`mor-map${offline ? " mor-map-offline" : ""}`}
            role="img"
            aria-label="APRS station map"
          />
          {tileNote ? (
            <Text size="sm" c="dimmed">
              {tileNote}
            </Text>
          ) : null}
          {mapError ? (
            <Text size="sm" c="red">
              {mapError}
            </Text>
          ) : null}
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md">
        <Group justify="space-between" mb="md">
          <Title order={2}>Stations</Title>
          <Text size="sm" c="dimmed">
            {body?.stations.length
              ? `${shown.length} of ${body.stations.length} in the last ${body.hours} h`
              : ""}
          </Text>
        </Group>
        <Table.ScrollContainer minWidth={640}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Station</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Distance</Table.Th>
                <Table.Th>Heard</Table.Th>
                <Table.Th>Comment</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((s) => (
                <Table.Tr key={s.name} style={{ cursor: "pointer" }} onClick={() => focusStation(s.name)}>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Badge
                        size="sm"
                        variant="outline"
                        style={{ borderColor: CATEGORIES[s.category]?.color, color: CATEGORIES[s.category]?.color }}
                      >
                        {CATEGORIES[s.category]?.letter ?? "?"}
                      </Badge>
                      <div>
                        <Text fw={600}>{s.name}</Text>
                        {s.kind !== "station" ? (
                          <Text size="xs" c="dimmed">
                            via {s.source}
                          </Text>
                        ) : null}
                      </div>
                    </Group>
                  </Table.Td>
                  <Table.Td>{CATEGORIES[s.category]?.one ?? s.category}</Table.Td>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>
                    {s.distance_km != null && body
                      ? `${distance(s.distance_km, body.distance_units)} ${compass(s.bearing)}`
                      : ""}
                  </Table.Td>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>{ago(s.last_heard)}</Table.Td>
                  <Table.Td>
                    <Text size="sm" lineClamp={2}>
                      {s.comment}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {!shown.length ? (
          <Text c="dimmed" size="sm" mt="sm">
            {body?.stations.length ? "Every station type is filtered out." : "No stations heard yet."}
          </Text>
        ) : null}
      </Card>
    </Page>
  );
}
