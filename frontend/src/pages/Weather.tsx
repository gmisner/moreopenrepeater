import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { labeled } from "../components/FieldHint";

type WeatherAlert = {
  id: string;
  event: string;
  severity: string;
  urgency: string;
  headline: string;
  area: string;
  expires: string | null;
  ends: string | null;
  announced: boolean;
  speech: string;
};

type WeatherStatus = {
  enabled: boolean;
  last_checked: string | null;
  last_error: string | null;
  alerts: WeatherAlert[];
};

type Airport = { icao: string; name: string };

type WxValues = {
  wx_alerts_enabled: boolean;
  wx_lat: number | "";
  wx_lon: number | "";
  wx_min_severity: string;
  wx_poll_interval: number;
  wx_repeat_minutes: number;
};

const WX_KEYS = [
  "wx_alerts_enabled",
  "wx_lat",
  "wx_lon",
  "wx_min_severity",
  "wx_poll_interval",
  "wx_repeat_minutes",
] as (keyof WxValues)[];

const WX_DEFAULTS: WxValues = {
  wx_alerts_enabled: false,
  wx_lat: "",
  wx_lon: "",
  wx_min_severity: "Severe",
  wx_poll_interval: 300,
  wx_repeat_minutes: 0,
};

const SEVERITY_COLOR: Record<string, string> = {
  Extreme: "red",
  Severe: "red",
  Moderate: "yellow",
};

const REFRESH_MS = 60_000;
const GRID = /^([A-R]{2})(\d{2})([A-X]{2}(\d{2})?)?$/i;
const PAIR = /^(-?\d{1,2}(?:\.\d+)?)\s*°?\s*([NS])?[\s,;]+(-?\d{1,3}(?:\.\d+)?)\s*°?\s*([EW])?$/i;

function fromGrid(match: RegExpMatchArray) {
  const [, field, square, sub = "", ext = ""] = match;
  const letter = (c: string, from: string) => c.toUpperCase().charCodeAt(0) - from.charCodeAt(0);
  let lon = -180 + letter(field[0], "A") * 20 + Number(square[0]) * 2;
  let lat = -90 + letter(field[1], "A") * 10 + Number(square[1]);
  let width = 2;
  let height = 1;
  if (sub) {
    width /= 24;
    height /= 24;
    lon += letter(sub[0], "A") * width;
    lat += letter(sub[1], "A") * height;
  }
  if (ext) {
    width /= 10;
    height /= 10;
    lon += Number(ext[0]) * width;
    lat += Number(ext[1]) * height;
  }
  return { lat: lat + height / 2, lon: lon + width / 2 };
}

function fromPair(match: RegExpMatchArray) {
  const [, latText, ns, lonText, ew] = match;
  let lat = Number(latText);
  let lon = Number(lonText);
  if (ns?.toUpperCase() === "S") lat = -Math.abs(lat);
  if (ew?.toUpperCase() === "W") lon = -Math.abs(lon);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
}

function parseLocation(text: string) {
  const value = text.trim().replace(/\s+/g, " ");
  const grid = value.replace(/\s/g, "").match(GRID);
  if (grid) return fromGrid(grid);
  const pair = value.match(PAIR);
  return pair ? fromPair(pair) : null;
}

function formatTimestamp(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function untilText(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay ? time : `${date.toLocaleDateString([], { weekday: "short" })} ${time}`;
}

function geolocationError(error: GeolocationPositionError) {
  if (error.code === error.PERMISSION_DENIED) {
    if (!window.isSecureContext) {
      return "Browsers only share location with HTTPS pages or localhost. Enter a grid square or coordinates instead.";
    }
    return "Location access is blocked for this page. Allow it in the browser's site settings, or enter a grid square or coordinates instead.";
  }
  if (error.code === error.TIMEOUT) return "Timed out waiting for a location. Enter a grid square or coordinates instead.";
  return "This device couldn't work out its location. Enter a grid square or coordinates instead.";
}

function describeStatus(weather: WeatherStatus | null, enabled: boolean) {
  if (!weather) return "Loading…";
  if (weather.last_error) return `Last check failed: ${weather.last_error}`;
  if (!weather.last_checked) return enabled ? "Waiting for the first check…" : "Not checked yet.";
  const checked = `Checked ${formatTimestamp(weather.last_checked)}.`;
  return enabled ? checked : `${checked} Announcements are switched off.`;
}

export function Weather() {
  const { config, save } = useConfig();
  const wx = useConfigSlice<WxValues>(WX_KEYS, WX_DEFAULTS);

  const [weather, setWeather] = useState<WeatherStatus | null>(null);
  const [lookup, setLookup] = useState("");
  const [checking, setChecking] = useState(false);
  const [playBusy, setPlayBusy] = useState<string | null>(null);
  const [airports, setAirports] = useState<Airport[]>([]);
  const [airportsSaving, setAirportsSaving] = useState(false);
  const [metarPreview, setMetarPreview] = useState("");
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);

  const loadWeather = useCallback(async () => {
    setWeather(await api<WeatherStatus>("/api/weather"));
  }, []);

  useEffect(() => {
    loadWeather().catch(notifyError);
  }, [loadWeather]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (!document.hidden) loadWeather().catch(() => {});
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadWeather]);

  useEffect(() => {
    const list = (config?.metar_airports as Airport[] | undefined) ?? [];
    setAirports(list.map((airport) => ({ icao: airport.icao, name: airport.name })));
  }, [config]);

  const applyLookup = () => {
    if (!lookup.trim()) return;
    const location = parseLocation(lookup);
    if (!location) {
      notifyError(new Error("Enter a grid square like FN31pr, or coordinates like 41.7148, -72.7272"));
      return;
    }
    wx.setField("wx_lat", Number(location.lat.toFixed(4)));
    wx.setField("wx_lon", Number(location.lon.toFixed(4)));
    setLookup("");
  };

  const useDeviceLocation = () => {
    if (!("geolocation" in navigator)) {
      notifyError(new Error("This browser doesn't support geolocation."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        wx.setField("wx_lat", Number(coords.latitude.toFixed(4)));
        wx.setField("wx_lon", Number(coords.longitude.toFixed(4)));
      },
      (error) => notifyError(new Error(geolocationError(error))),
      { timeout: 15000 },
    );
  };

  const checkNow = async () => {
    setChecking(true);
    try {
      setWeather(await api<WeatherStatus>("/api/weather/check", { method: "POST" }));
    } catch (error) {
      notifyError(error);
    } finally {
      setChecking(false);
    }
  };

  const playAlert = async (alert: WeatherAlert) => {
    setPlayBusy(alert.id);
    try {
      await api(`/api/weather/alerts/${encodeURIComponent(alert.id)}/play`, { method: "POST" });
      notifySuccess(`Queued "${alert.event}" — it plays as soon as the channel is clear`);
    } catch (error) {
      notifyError(error);
    } finally {
      setPlayBusy(null);
    }
  };

  const saveAirports = async (event: FormEvent) => {
    event.preventDefault();
    setAirportsSaving(true);
    try {
      const metar_airports = airports.map((airport) => ({
        icao: airport.icao.trim().toUpperCase(),
        name: airport.name.trim(),
      }));
      await save({ metar_airports });
      notifySuccess("Airports saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setAirportsSaving(false);
    }
  };

  const previewMetar = async (index: number) => {
    const icao = airports[index]?.icao.trim();
    if (!icao || !/^[A-Za-z0-9]{4}$/.test(icao)) {
      notifyError(new Error("Enter a 4-character ICAO code first"));
      return;
    }
    setPreviewBusy(icao);
    try {
      const { text } = await api<{ text: string }>(`/api/metar/${encodeURIComponent(icao)}`);
      setMetarPreview(text);
    } catch (error) {
      notifyError(error);
    } finally {
      setPreviewBusy(null);
    }
  };

  const alerts = weather?.alerts ?? [];
  const enabled = wx.values.wx_alerts_enabled;

  return (
    <Page
      title="Weather"
      description="Announce National Weather Service watches and warnings for the repeater's location (US only, from api.weather.gov). Each alert is spoken once when it's issued, as soon as the channel is clear. Airport weather, below, can be read out by a DTMF macro."
    >
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void wx.submit();
        }}
      >
        <Stack gap="md">
          <Switch
            label="Announce weather alerts on the air"
            checked={wx.values.wx_alerts_enabled}
            onChange={(event) => wx.setField("wx_alerts_enabled", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="Latitude"
              min={-90}
              max={90}
              step={0.0001}
              decimalScale={4}
              placeholder="e.g. 39.0997"
              value={wx.values.wx_lat === "" ? undefined : wx.values.wx_lat}
              onChange={(value) => wx.setField("wx_lat", value === "" || value === undefined ? "" : Number(value))}
            />
            <NumberInput
              label="Longitude"
              min={-180}
              max={180}
              step={0.0001}
              decimalScale={4}
              placeholder="e.g. -94.5786"
              value={wx.values.wx_lon === "" ? undefined : wx.values.wx_lon}
              onChange={(value) => wx.setField("wx_lon", value === "" || value === undefined ? "" : Number(value))}
            />
            <TextInput
              label={labeled("Grid square or coordinates", "Fills in the latitude and longitude.")}
              placeholder="FN31pr or 41.7148, -72.7272"
              value={lookup}
              onChange={(event) => setLookup(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                applyLookup();
              }}
              rightSectionWidth={68}
              rightSection={
                <Button size="compact-sm" variant="subtle" onClick={applyLookup}>
                  Set
                </Button>
              }
            />
            <Select
              label="Announce alerts that are at least"
              data={[
                { value: "Extreme", label: "Extreme (e.g. tornado warning)" },
                { value: "Severe", label: "Severe (warnings, most watches)" },
                { value: "Moderate", label: "Moderate (advisories)" },
                { value: "Minor", label: "Minor (everything)" },
              ]}
              value={wx.values.wx_min_severity}
              onChange={(value) => wx.setField("wx_min_severity", value || "Severe")}
              allowDeselect={false}
            />
            <NumberInput
              label="Check every"
              suffix=" s"
              min={30}
              step={30}
              value={wx.values.wx_poll_interval}
              onChange={(value) => wx.setField("wx_poll_interval", Number(value) || 0)}
              required
            />
            <NumberInput
              label={labeled("Repeat ongoing alerts every", "0 = announce each alert only once.")}
              suffix=" min"
              min={0}
              step={5}
              value={wx.values.wx_repeat_minutes}
              onChange={(value) => wx.setField("wx_repeat_minutes", Number(value) || 0)}
              required
            />
          </SimpleGrid>
          <Group justify="space-between">
            <Button type="button" variant="subtle" onClick={useDeviceLocation}>
              Use this device&apos;s location
            </Button>
            <ConfigActions dirty={wx.dirty} saving={wx.saving} onReset={wx.reset} />
          </Group>
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md">
        <Group justify="space-between" mb="md">
          <Title order={2}>Active alerts</Title>
          <Button variant="subtle" size="compact-sm" loading={checking} onClick={() => void checkNow()}>
            Check now
          </Button>
        </Group>
        <Text size="sm" c="dimmed" mb="md">
          {describeStatus(weather, enabled)}
        </Text>
        <Table.ScrollContainer minWidth={720}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Alert</Table.Th>
                <Table.Th>Severity</Table.Th>
                <Table.Th>Until</Table.Th>
                <Table.Th>What the repeater says</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {alerts.map((alert) => (
                <Table.Tr key={alert.id}>
                  <Table.Td>
                    <Text fw={600}>{alert.event}</Text>
                    {alert.announced ? (
                      <Badge ml={6} color="green" variant="light" size="sm">
                        announced
                      </Badge>
                    ) : null}
                    <Text size="xs" c="dimmed">
                      {alert.area}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={SEVERITY_COLOR[alert.severity] ?? "gray"} variant="light">
                      {alert.severity}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{untilText(alert.ends ?? alert.expires)}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{alert.speech}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Button
                      size="compact-sm"
                      variant="subtle"
                      loading={playBusy === alert.id}
                      title="Transmit now (waits for a clear channel)"
                      onClick={() => void playAlert(alert)}
                    >
                      Play now
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {alerts.length === 0 ? (
          <Text size="sm" c="dimmed" mt="sm">
            No active alerts at or above the chosen severity.
          </Text>
        ) : null}
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void saveAirports(event)}>
        <Stack gap="md">
          <Title order={2}>Airport weather</Title>
          <Text c="dimmed" size="sm" maw="75ch">
            Current conditions (METAR) from aviationweather.gov, for an &quot;Airport weather&quot; DTMF macro. Reports are fetched when asked for and kept 10 minutes.
          </Text>
          <Table.ScrollContainer minWidth={480}>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>ICAO code</Table.Th>
                  <Table.Th>Spoken name</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {airports.map((airport, index) => (
                  <Table.Tr key={index}>
                    <Table.Td>
                      <TextInput
                        required
                        maxLength={4}
                        pattern="[A-Za-z0-9]{4}"
                        placeholder="KAMA"
                        autoCapitalize="characters"
                        value={airport.icao}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setAirports((prev) => prev.map((row, i) => (i === index ? { ...row, icao: value } : row)));
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <TextInput
                        maxLength={40}
                        placeholder="Blank spells out the code"
                        value={airport.name}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setAirports((prev) => prev.map((row, i) => (i === index ? { ...row, name: value } : row)));
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" justify="flex-end" wrap="nowrap">
                        <Button
                          size="compact-sm"
                          variant="subtle"
                          loading={previewBusy === airport.icao.trim().toUpperCase()}
                          onClick={() => void previewMetar(index)}
                        >
                          Preview
                        </Button>
                        <Button
                          size="compact-sm"
                          color="red"
                          variant="subtle"
                          onClick={() => setAirports((prev) => prev.filter((_, i) => i !== index))}
                        >
                          Remove
                        </Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {airports.length === 0 ? (
            <Text size="sm" c="dimmed">
              No airports yet.
            </Text>
          ) : null}
          {metarPreview ? (
            <Text size="sm" c="dimmed">
              {metarPreview}
            </Text>
          ) : null}
          <Group justify="space-between">
            <Button type="button" variant="subtle" onClick={() => setAirports((prev) => [...prev, { icao: "", name: "" }])}>
              Add an airport
            </Button>
            <Button type="submit" loading={airportsSaving}>
              Save airports
            </Button>
          </Group>
        </Stack>
      </Card>
    </Page>
  );
}
