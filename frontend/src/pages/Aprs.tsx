import {
  Accordion,
  Anchor,
  Button,
  Card,
  Code,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMemo, useState } from "react";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { labeled } from "../components/FieldHint";

const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const MAX_COMMENT = 43;
const GRID = /^([A-R]{2})(\d{2})([A-X]{2}(\d{2})?)?$/i;
const PAIR = /^(-?\d{1,2}(?:\.\d+)?)\s*°?\s*([NS])?[\s,;]+(-?\d{1,3}(?:\.\d+)?)\s*°?\s*([EW])?$/i;

type BeaconValues = {
  aprs_enabled: boolean;
  aprs_callsign: string;
  aprs_beacon_interval: number;
  aprs_comment: string;
  aprs_lat: number | "";
  aprs_lon: number | "";
  aprs_symbol: string;
  aprs_frequency_mhz: number | "";
  aprs_offset_mhz: number | "";
  aprs_tone_hz: number | "";
  aprs_server: string;
  aprs_port: number;
};

type MapValues = {
  aprs_map_enabled: boolean;
  aprs_map_radius_km: number;
  aprs_map_hours: number;
  distance_units: string;
  aprs_map_tiles: string;
};

const BEACON_KEYS = [
  "aprs_enabled",
  "aprs_callsign",
  "aprs_beacon_interval",
  "aprs_comment",
  "aprs_lat",
  "aprs_lon",
  "aprs_symbol",
  "aprs_frequency_mhz",
  "aprs_offset_mhz",
  "aprs_tone_hz",
  "aprs_server",
  "aprs_port",
] as (keyof BeaconValues)[];

const BEACON_DEFAULTS: BeaconValues = {
  aprs_enabled: false,
  aprs_callsign: "",
  aprs_beacon_interval: 1800,
  aprs_comment: "",
  aprs_lat: "",
  aprs_lon: "",
  aprs_symbol: "/r",
  aprs_frequency_mhz: "",
  aprs_offset_mhz: "",
  aprs_tone_hz: "",
  aprs_server: "rotate.aprs2.net",
  aprs_port: 14580,
};

const MAP_KEYS = [
  "aprs_map_enabled",
  "aprs_map_radius_km",
  "aprs_map_hours",
  "distance_units",
  "aprs_map_tiles",
] as (keyof MapValues)[];

const MAP_DEFAULTS: MapValues = {
  aprs_map_enabled: false,
  aprs_map_radius_km: 50,
  aprs_map_hours: 3,
  distance_units: "mi",
  aprs_map_tiles: OSM_TILES,
};

const SYMBOLS = [
  { value: "/r", label: "Repeater" },
  { value: "/#", label: "Digipeater" },
  { value: "/&", label: "Gateway" },
  { value: "/-", label: "House" },
  { value: "/_", label: "Weather station" },
];

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

function parseLocation(text: string): { lat: number; lon: number } | null {
  const value = text.trim().replace(/\s+/g, " ");
  const grid = value.replace(/\s/g, "").match(GRID);
  if (grid) return fromGrid(grid);
  const pair = value.match(PAIR);
  if (!pair) return null;
  const [, latText, ns, lonText, ew] = pair;
  let lat = Number(latText);
  let lon = Number(lonText);
  if (ns?.toUpperCase() === "S") lat = -Math.abs(lat);
  if (ew?.toUpperCase() === "W") lon = -Math.abs(lon);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
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

function frequencyComment(frequency: number, tone: number | null, offset: number | null) {
  const parts = [frequency < 1000 ? `${frequency.toFixed(3).padStart(7, "0")}MHz` : `${frequency.toFixed(2)}MHz`];
  if (tone !== null) parts.push(`T${String(Math.trunc(tone)).padStart(3, "0")}`);
  if (offset !== null) {
    if (tone === null) parts.push("Toff");
    parts.push(`${offset > 0 ? "+" : "-"}${String(Math.round(Math.abs(offset) * 100)).padStart(3, "0")}`);
  }
  return parts.join(" ");
}

function optionalNumber(value: number | "") {
  return value === "" ? null : Number(value);
}

export function Aprs() {
  const { config } = useConfig();
  const beacon = useConfigSlice<BeaconValues>(BEACON_KEYS, BEACON_DEFAULTS);
  const map = useConfigSlice<MapValues>(MAP_KEYS, MAP_DEFAULTS);
  const [lookup, setLookup] = useState("");
  const [locating, setLocating] = useState(false);

  const requiredTone = (config?.require_ctcss_hz as number | undefined) ?? null;

  const preview = useMemo(() => {
    let comment = beacon.values.aprs_comment.trim();
    const frequency = optionalNumber(beacon.values.aprs_frequency_mhz);
    if (frequency !== null) {
      const tone = optionalNumber(beacon.values.aprs_tone_hz) ?? requiredTone;
      comment = `${frequencyComment(frequency, tone, optionalNumber(beacon.values.aprs_offset_mhz))} ${comment}`.trim();
    }
    return comment;
  }, [beacon.values, requiredTone]);

  const applyLookup = () => {
    if (!lookup.trim()) return;
    const location = parseLocation(lookup);
    if (!location) {
      notifyError(new Error("Enter a grid square like FN31pr, or coordinates like 41.7148, -72.7272"));
      return;
    }
    beacon.setField("aprs_lat", Number(location.lat.toFixed(4)));
    beacon.setField("aprs_lon", Number(location.lon.toFixed(4)));
    setLookup("");
  };

  const useDeviceLocation = () => {
    if (!("geolocation" in navigator)) {
      notifyError(new Error("This browser can't share a location."));
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        beacon.setField("aprs_lat", Number(coords.latitude.toFixed(4)));
        beacon.setField("aprs_lon", Number(coords.longitude.toFixed(4)));
        setLocating(false);
      },
      (error) => {
        notifyError(new Error(geolocationError(error)));
        setLocating(false);
      },
      { timeout: 15000 },
    );
  };

  const symbolData = SYMBOLS.some((s) => s.value === beacon.values.aprs_symbol)
    ? SYMBOLS
    : [...SYMBOLS, { value: beacon.values.aprs_symbol, label: beacon.values.aprs_symbol }];

  return (
    <Page title="APRS" description="Periodically beacon the repeater's position or status to APRS-IS.">
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void beacon.submit();
        }}
      >
        <Stack gap="md">
          <Switch
            label="Enable APRS beaconing"
            checked={beacon.values.aprs_enabled}
            onChange={(event) => beacon.setField("aprs_enabled", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label={labeled("APRS callsign", "Your passcode is derived from this automatically.")}
              placeholder="e.g. W1AW-10"
              value={beacon.values.aprs_callsign}
              onChange={(event) => beacon.setField("aprs_callsign", event.currentTarget.value)}
            />
            <NumberInput
              label="Beacon interval"
              suffix=" s"
              min={60}
              step={60}
              required
              value={beacon.values.aprs_beacon_interval}
              onChange={(value) => beacon.setField("aprs_beacon_interval", Number(value) || 0)}
            />
            <TextInput
              label={labeled("Beacon comment", preview
                  ? `Sends: ${preview}${preview.length > MAX_COMMENT ? ` — ${preview.length} characters; radios may only show the first ${MAX_COMMENT}.` : ""}`
                  : undefined)}
              placeholder="e.g. Hartford CT, net Tue 8pm"
              value={beacon.values.aprs_comment}
              onChange={(event) => beacon.setField("aprs_comment", event.currentTarget.value)}
              style={{ gridColumn: "1 / -1" }}
            />
          </SimpleGrid>

          <Accordion variant="contained" multiple defaultValue={["position"]}>
            <Accordion.Item value="position">
              <Accordion.Control>Position</Accordion.Control>
              <Accordion.Panel>
                <Stack gap="md">
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <NumberInput
                      label="Latitude"
                      placeholder="blank = status beacon"
                      min={-90}
                      max={90}
                      decimalScale={4}
                      step={0.0001}
                      value={beacon.values.aprs_lat === "" ? undefined : beacon.values.aprs_lat}
                      onChange={(value) =>
                        beacon.setField("aprs_lat", value === "" || value === undefined ? "" : Number(value))
                      }
                    />
                    <NumberInput
                      label="Longitude"
                      placeholder="blank = status beacon"
                      min={-180}
                      max={180}
                      decimalScale={4}
                      step={0.0001}
                      value={beacon.values.aprs_lon === "" ? undefined : beacon.values.aprs_lon}
                      onChange={(value) =>
                        beacon.setField("aprs_lon", value === "" || value === undefined ? "" : Number(value))
                      }
                    />
                    <TextInput
                      label={labeled("Grid square or coordinates", "Fills in the latitude and longitude.")}
                      placeholder="FN31pr or 41.7148, -72.7272"
                      autoComplete="off"
                      value={lookup}
                      onChange={(event) => setLookup(event.currentTarget.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          applyLookup();
                        }
                      }}
                      rightSectionWidth={64}
                      rightSection={
                        <Button size="compact-sm" variant="subtle" onClick={applyLookup}>
                          Set
                        </Button>
                      }
                    />
                    <Select
                      label="Map symbol"
                      data={symbolData}
                      value={beacon.values.aprs_symbol}
                      onChange={(value) => beacon.setField("aprs_symbol", value || "/r")}
                      allowDeselect={false}
                      searchable
                    />
                  </SimpleGrid>
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="frequency">
              <Accordion.Control>Frequency for APRS radios</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <NumberInput
                    label={labeled("Output frequency", "Lets APRS radios tune to the repeater with one button.")}
                    suffix=" MHz"
                    placeholder="blank = leave out"
                    min={28}
                    max={1300}
                    decimalScale={3}
                    step={0.001}
                    value={beacon.values.aprs_frequency_mhz === "" ? undefined : beacon.values.aprs_frequency_mhz}
                    onChange={(value) =>
                      beacon.setField("aprs_frequency_mhz", value === "" || value === undefined ? "" : Number(value))
                    }
                  />
                  <NumberInput
                    label={labeled("Offset", "e.g. -0.6 or 5. Use 0 for simplex.")}
                    suffix=" MHz"
                    placeholder="standard"
                    min={-9.99}
                    max={9.99}
                    decimalScale={2}
                    step={0.01}
                    value={beacon.values.aprs_offset_mhz === "" ? undefined : beacon.values.aprs_offset_mhz}
                    onChange={(value) =>
                      beacon.setField("aprs_offset_mhz", value === "" || value === undefined ? "" : Number(value))
                    }
                  />
                  <NumberInput
                    label="Access tone"
                    suffix=" Hz"
                    placeholder={requiredTone ? `${requiredTone} (required CTCSS)` : "none"}
                    min={60}
                    max={260}
                    decimalScale={1}
                    step={0.1}
                    value={beacon.values.aprs_tone_hz === "" ? undefined : beacon.values.aprs_tone_hz}
                    onChange={(value) =>
                      beacon.setField("aprs_tone_hz", value === "" || value === undefined ? "" : Number(value))
                    }
                  />
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="server">
              <Accordion.Control>APRS-IS server</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <TextInput
                    label="APRS-IS server"
                    required
                    value={beacon.values.aprs_server}
                    onChange={(event) => beacon.setField("aprs_server", event.currentTarget.value)}
                  />
                  <NumberInput
                    label="Port"
                    min={1}
                    max={65535}
                    required
                    value={beacon.values.aprs_port}
                    onChange={(value) => beacon.setField("aprs_port", Number(value) || 0)}
                  />
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <Group justify="space-between" wrap="wrap">
            <Button type="button" variant="subtle" loading={locating} onClick={useDeviceLocation}>
              Use this device&apos;s location
            </Button>
            <ConfigActions dirty={beacon.dirty} saving={beacon.saving} onReset={beacon.reset} />
          </Group>
        </Stack>
      </Card>

      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void map.submit();
        }}
      >
        <Stack gap="md">
          <Group justify="space-between" align="flex-start">
            <Title order={2}>Map</Title>
            <Anchor href="/#/map" size="sm">
              Open the map
            </Anchor>
          </Group>
          <Switch
            label="Map nearby APRS stations"
            checked={map.values.aprs_map_enabled}
            onChange={(event) => map.setField("aprs_map_enabled", event.currentTarget.checked)}
          />
          <Text c="dimmed" size="sm" maw="75ch">
            Receives position reports from the APRS-IS server above, centered on the latitude and longitude above (or the
            weather alert location). Nothing is transmitted.
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="Range"
              suffix=" km"
              min={1}
              max={500}
              required
              value={map.values.aprs_map_radius_km}
              onChange={(value) => map.setField("aprs_map_radius_km", Number(value) || 0)}
            />
            <NumberInput
              label="Keep stations for"
              suffix=" h"
              min={0.25}
              max={48}
              step={0.25}
              decimalScale={2}
              required
              value={map.values.aprs_map_hours}
              onChange={(value) => map.setField("aprs_map_hours", Number(value) || 0)}
            />
            <Select
              label="Distances in"
              data={[
                { value: "mi", label: "Miles" },
                { value: "km", label: "Kilometers" },
              ]}
              value={map.values.distance_units}
              onChange={(value) => map.setField("distance_units", value || "mi")}
              allowDeselect={false}
            />
            <TextInput
              label={labeled("Map tiles", <>
                  A <Code>{`{z}/{x}/{y}`}</Code> tile URL. Leave blank on repeaters without internet to show range rings
                  only.
                </>)}
              placeholder="blank = no background map"
              value={map.values.aprs_map_tiles}
              onChange={(event) => map.setField("aprs_map_tiles", event.currentTarget.value)}
              style={{ gridColumn: "1 / -1" }}
            />
          </SimpleGrid>
          <Group justify="space-between" wrap="wrap">
            <Button type="button" variant="subtle" onClick={() => map.setField("aprs_map_tiles", OSM_TILES)}>
              Use OpenStreetMap
            </Button>
            <ConfigActions dirty={map.dirty} saving={map.saving} onReset={map.reset} />
          </Group>
        </Stack>
      </Card>
    </Page>
  );
}
