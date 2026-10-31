import {
  Accordion,
  Badge,
  Box,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Slider,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiBlob } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { useSetupWizard } from "../wizard/SetupWizard";

type Asset = { id: string; kind: string; filename: string; uploaded_at: string };
type AudioDevice = { name: string; inputs: number; outputs: number; default_samplerate: number };
type SerialPort = { path: string; target: string };
type SoundLevel = {
  side: "input" | "output";
  card: number;
  control: string;
  min: number;
  max: number;
  value: number;
  db: number | null;
  saved: boolean;
};
type EngineStatus = {
  enabled: boolean;
  running: boolean;
  error: string | null;
  sample_rate: number;
  device_sample_rate: number | null;
  rx_level_db: number;
  cos_open: boolean;
  ctcss_hz: number | null;
  transmitting: boolean;
  dropped_input_blocks: number;
  starved_output_blocks: number;
  input_overflows: number;
  output_underflows: number;
  hardware_ptt: string | null;
};

type SoundValues = {
  courtesy_tone_style: string;
  courtesy_tone_asset_id: string;
  id_asset_id: string;
  timeout_tone_asset_id: string;
  courtesy_tone_link_style: string;
  courtesy_tone_link_asset_id: string;
  courtesy_tone_patch_style: string;
  courtesy_tone_patch_asset_id: string;
  courtesy_tone_custom: string;
};

type EngineValues = {
  audio_enabled: boolean;
  audio_input_device: string;
  audio_output_device: string;
  rx_deemphasis: boolean;
  tx_preemphasis: boolean;
  cos_source: string;
  cos_polarity: string;
  cos_gpio_pin: number;
  cos_serial_device: string;
  cos_serial_line: string;
  vox_threshold_db: number;
  vox_hold: number;
  vox_dtmf_hold: number;
  squelch_tail_ms: number;
  ptt_output: string;
  ptt_gpio_pin: number;
  ptt_serial_device: string;
  ptt_serial_line: string;
  ptt_polarity: string;
  tx_delay_ms: number;
  tx_gain_db: number;
  tx_ctcss_hz: number | "";
  tx_ctcss_level_db: number;
  dtmf_mute: boolean;
};

const TONE_STYLES = [
  { value: "beep", label: "Beep" },
  { value: "high_low", label: "High–low" },
  { value: "low_high", label: "Low–high" },
  { value: "bee_boo", label: "Bee-boo" },
  { value: "triple", label: "Triple beep" },
  { value: "chirp", label: "Chirp" },
  { value: "bumblebee", label: "Bumblebee" },
  { value: "up_run", label: "Three tones up" },
  { value: "down_run", label: "Three tones down" },
  { value: "bonk", label: "Bonk" },
  { value: "nextel", label: "Nextel chirp" },
  { value: "cw_k", label: 'CW "K"' },
  { value: "cw_r", label: 'CW "R"' },
  { value: "cw_t", label: 'CW "T"' },
  { value: "custom", label: "Custom" },
];

const TONE_VARIANT_STYLES = [{ value: "same", label: "Same as local users" }, ...TONE_STYLES];

const SOUND_KEYS = [
  "courtesy_tone_style",
  "courtesy_tone_asset_id",
  "id_asset_id",
  "timeout_tone_asset_id",
  "courtesy_tone_link_style",
  "courtesy_tone_link_asset_id",
  "courtesy_tone_patch_style",
  "courtesy_tone_patch_asset_id",
  "courtesy_tone_custom",
] as (keyof SoundValues)[];

const SOUND_DEFAULTS: SoundValues = {
  courtesy_tone_style: "beep",
  courtesy_tone_asset_id: "",
  id_asset_id: "",
  timeout_tone_asset_id: "",
  courtesy_tone_link_style: "same",
  courtesy_tone_link_asset_id: "",
  courtesy_tone_patch_style: "same",
  courtesy_tone_patch_asset_id: "",
  courtesy_tone_custom: "880:100 0:40 1320:100",
};

const ENGINE_KEYS = [
  "audio_enabled",
  "audio_input_device",
  "audio_output_device",
  "rx_deemphasis",
  "tx_preemphasis",
  "cos_source",
  "cos_polarity",
  "cos_gpio_pin",
  "cos_serial_device",
  "cos_serial_line",
  "vox_threshold_db",
  "vox_hold",
  "vox_dtmf_hold",
  "squelch_tail_ms",
  "ptt_output",
  "ptt_gpio_pin",
  "ptt_serial_device",
  "ptt_serial_line",
  "ptt_polarity",
  "tx_delay_ms",
  "tx_gain_db",
  "tx_ctcss_hz",
  "tx_ctcss_level_db",
  "dtmf_mute",
] as (keyof EngineValues)[];

const ENGINE_DEFAULTS: EngineValues = {
  audio_enabled: false,
  audio_input_device: "",
  audio_output_device: "",
  rx_deemphasis: false,
  tx_preemphasis: false,
  cos_source: "vox",
  cos_polarity: "low",
  cos_gpio_pin: 27,
  cos_serial_device: "",
  cos_serial_line: "cts",
  vox_threshold_db: -40,
  vox_hold: 0.4,
  vox_dtmf_hold: 2,
  squelch_tail_ms: 0,
  ptt_output: "cm108",
  ptt_gpio_pin: 17,
  ptt_serial_device: "",
  ptt_serial_line: "rts",
  ptt_polarity: "high",
  tx_delay_ms: 0,
  tx_gain_db: 0,
  tx_ctcss_hz: "",
  tx_ctcss_level_db: -20,
  dtmf_mute: true,
};

const CTCSS_TONES = [
  67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0,
  127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2,
  189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
];

const METER_FLOOR_DB = -80;
const SILENCE_DB = -120;
const SILENT_POLLS_BEFORE_HINT = 20;
const POLL_MS = 250;
const SIDE_LABELS = { input: "Receive", output: "Transmit" } as const;
const PTT_LABELS: Record<string, string> = { cm108: " · CM108 PTT", gpio: " · GPIO PTT", serial: " · serial PTT" };
const GLITCH_SHORT: Record<string, string> = {
  dropped_input_blocks: "receive drops",
  starved_output_blocks: "transmit gaps",
  input_overflows: "receive overruns",
  output_underflows: "transmit underruns",
};
const BOARD_NAMES: Record<string, string> = {
  cm108: "Generic CM108/CM119 USB interface",
  "dmk-uri": "DMK URI",
  "digirig-lite": "Digirig Lite",
  "usb-rim-lite": "USB-RIM Lite",
  aioc: "AIOC (All-In-One-Cable)",
  "svxlink-card": "SVXLink Card",
  "svxlink-basic": "SVXLink Basic Board",
  "ics-1x": "ICS Pi Repeater 1X",
  "ics-2x": "ICS Pi Repeater 2X",
};

let previewAudio: HTMLAudioElement | null = null;

async function playPreview(clip: string, config: Record<string, unknown> = {}) {
  const blob = await apiBlob("/api/audio/preview", { method: "POST", json: { clip, config, net: false } });
  if (previewAudio) {
    previewAudio.pause();
    URL.revokeObjectURL(previewAudio.src);
  }
  const next = new window.Audio(URL.createObjectURL(blob));
  previewAudio = next;
  await next.play();
}

function meterPercent(db: number) {
  return Math.max(0, Math.min(100, ((db - METER_FLOOR_DB) / -METER_FLOOR_DB) * 100));
}

function parseCustomTone(text: string): [string, string][] {
  return (text || "")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => {
      const [hz = "", ms = ""] = part.split(":");
      return [hz, ms] as [string, string];
    });
}

function serializeCustomTone(parts: [string, string][]) {
  return parts
    .filter(([hz, ms]) => hz !== "" && ms !== "")
    .map(([hz, ms]) => `${Number(hz)}:${Number(ms)}`)
    .join(" ");
}

function assetOptions(assets: Asset[], kind: string) {
  return assets.filter((asset) => asset.kind === kind).map((asset) => ({ value: asset.id, label: asset.filename }));
}

function deviceOptions(devices: AudioDevice[], side: "inputs" | "outputs", saved: string) {
  const names = devices.filter((device) => device[side] > 0).map((device) => device.name);
  const options = names.map((name) => ({ value: name, label: name }));
  if (saved && !names.includes(saved)) options.push({ value: saved, label: `${saved} (not connected)` });
  return options;
}

function soundLevelText(level: SoundLevel) {
  if (level.db == null) return `${Math.round((100 * (level.value - level.min)) / (level.max - level.min || 1))}%`;
  return `${level.db > 0 ? "+" : ""}${level.db.toFixed(1)} dB`;
}

function engineState(engine: EngineStatus | null) {
  if (!engine) return { label: "…", color: "gray" as const };
  if (engine.running) return { label: "running", color: "green" as const };
  if (engine.error) return { label: "error", color: "red" as const };
  return { label: engine.enabled ? "stopped" : "off", color: "gray" as const };
}

function LevelMeter({
  db,
  running,
  open,
  thresholdDb,
  showThreshold,
}: {
  db: number;
  running: boolean;
  open: boolean;
  thresholdDb: number;
  showThreshold: boolean;
}) {
  const pct = running ? meterPercent(db) : 0;
  return (
    <Box
      pos="relative"
      h={12}
      style={{
        borderRadius: 6,
        background: "var(--mantine-color-default-hover)",
        overflow: "hidden",
      }}
    >
      <Box
        style={{
          position: "absolute",
          inset: 0,
          transformOrigin: "left center",
          transform: `scaleX(${pct / 100})`,
          background: open ? "var(--mantine-color-green-6)" : "var(--mantine-color-accent-5)",
          transition: "transform 80ms linear",
        }}
      />
      {showThreshold ? (
        <Box
          title="VOX threshold"
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${meterPercent(thresholdDb)}%`,
            width: 2,
            background: "var(--mantine-color-yellow-6)",
          }}
        />
      ) : null}
    </Box>
  );
}

export function Audio() {
  const { session } = useSession();
  const { config } = useConfig();
  const isViewer = session?.role === "viewer";
  const sounds = useConfigSlice<SoundValues>(SOUND_KEYS, SOUND_DEFAULTS);
  const engine = useConfigSlice<EngineValues>(ENGINE_KEYS, ENGINE_DEFAULTS);

  const [assets, setAssets] = useState<Asset[]>([]);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [serialPorts, setSerialPorts] = useState<SerialPort[]>([]);
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [levels, setLevels] = useState<SoundLevel[]>([]);
  const [levelNotes, setLevelNotes] = useState<string[]>([]);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);
  const [levelsSaving, setLevelsSaving] = useState(false);
  const silentPolls = useRef(0);
  const [showSilenceHint, setShowSilenceHint] = useState(false);

  const customParts = useMemo(() => {
    const parsed = parseCustomTone(sounds.values.courtesy_tone_custom);
    return Array.from({ length: 4 }, (_, i) => parsed[i] ?? (["", ""] as [string, string]));
  }, [sounds.values.courtesy_tone_custom]);

  const loadAssets = useCallback(async () => {
    setAssets(await api<Asset[]>("/api/assets"));
  }, []);

  const loadDevices = useCallback(async () => {
    try {
      setDevices(await api<AudioDevice[]>("/api/audio/devices"));
    } catch {
      setDevices([]);
    }
    try {
      setSerialPorts(await api<SerialPort[]>("/api/serial/ports"));
    } catch {
      setSerialPorts([]);
    }
  }, []);

  const loadLevels = useCallback(async () => {
    try {
      const body = await api<{ levels: SoundLevel[]; notes: string[] }>("/api/audio/levels");
      setLevels(body.levels);
      setLevelNotes(body.notes);
    } catch (error) {
      setLevels([]);
      setLevelNotes([error instanceof Error ? error.message : String(error)]);
    }
  }, []);

  useEffect(() => {
    loadAssets().catch(notifyError);
    loadDevices();
    loadLevels();
  }, [loadAssets, loadDevices, loadLevels]);

  useEffect(() => {
    void loadLevels();
  }, [config?.audio_input_device, config?.audio_output_device, loadLevels]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api<EngineStatus>("/api/audio/engine");
        if (cancelled) return;
        setEngineStatus(next);
        silentPolls.current = next.running && next.rx_level_db <= SILENCE_DB ? silentPolls.current + 1 : 0;
        setShowSilenceHint(silentPolls.current >= SILENT_POLLS_BEFORE_HINT);
      } catch {
        // Next poll retries; session handling covers sign-out.
      }
    };
    void tick();
    const id = window.setInterval(() => {
      if (!document.hidden) void tick();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const preview = async (clip: string, label: string, override: Record<string, unknown> = {}) => {
    setPreviewBusy(label);
    try {
      await playPreview(clip, { ...sounds.values, ...override });
    } catch (error) {
      notifyError(error);
    } finally {
      setPreviewBusy(null);
    }
  };

  const setCustomPart = (index: number, which: 0 | 1, value: string | number) => {
    const next = customParts.map((part) => [...part] as [string, string]);
    next[index][which] = value === "" || value === undefined ? "" : String(value);
    sounds.setField("courtesy_tone_custom", serializeCustomTone(next));
  };

  const setLevel = async (level: SoundLevel, value: number) => {
    try {
      const body = await api<{ levels: SoundLevel[]; notes: string[] }>("/api/audio/levels", {
        method: "PUT",
        json: { side: level.side, control: level.control, value },
      });
      setLevels(body.levels);
      setLevelNotes(body.notes);
    } catch (error) {
      notifyError(error);
      void loadLevels();
    }
  };

  const saveUnsavedLevels = async () => {
    setLevelsSaving(true);
    try {
      for (const level of levels.filter((item) => !item.saved)) {
        await api("/api/audio/levels", {
          method: "PUT",
          json: { side: level.side, control: level.control, value: level.value },
        });
      }
      notifySuccess("Levels saved");
      await loadLevels();
    } catch (error) {
      notifyError(error);
    } finally {
      setLevelsSaving(false);
    }
  };

  const { openWizard } = useSetupWizard();
  const state = engineState(engineStatus);
  const boardPreset = (config?.board_preset as string) || "";
  const cosHardware = ["cm108", "gpio", "serial"].includes(engine.values.cos_source);
  const glitchCount = (counter: keyof typeof GLITCH_SHORT) => {
    if (!engineStatus) return 0;
    if (counter === "dropped_input_blocks") return engineStatus.dropped_input_blocks;
    if (counter === "starved_output_blocks") return engineStatus.starved_output_blocks;
    if (counter === "input_overflows") return engineStatus.input_overflows;
    return engineStatus.output_underflows;
  };
  const glitches = Object.entries(GLITCH_SHORT)
    .filter(([counter]) => glitchCount(counter as keyof typeof GLITCH_SHORT))
    .map(([counter, label]) => `${glitchCount(counter as keyof typeof GLITCH_SHORT)} ${label}`)
    .join(" · ");
  const rate = engineStatus?.device_sample_rate ?? engineStatus?.sample_rate;
  const rateText =
    engineStatus && rate != null
      ? rate === engineStatus.sample_rate
        ? `${rate / 1000} kHz`
        : `device ${rate / 1000} kHz → ${engineStatus.sample_rate / 1000} kHz`
      : "";
  const health = engineStatus?.running
    ? `${rateText}${PTT_LABELS[engineStatus.hardware_ptt ?? ""] ?? ""}${glitches ? ` · ${glitches}` : ""}`
    : "";
  const unsavedLevels = levels.some((level) => !level.saved);

  return (
    <Page
      title="Audio & tones"
      description="Choose the sounds the repeater transmits. Use the preview buttons to hear exactly what goes out on the air, including unsaved changes."
    >
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void sounds.submit();
        }}
      >
        <Stack gap="md">
          <Title order={2}>Sounds</Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Built-in courtesy tone"
              data={TONE_STYLES}
              value={sounds.values.courtesy_tone_style}
              onChange={(value) => sounds.setField("courtesy_tone_style", value || "beep")}
              allowDeselect={false}
            />
            <Select
              label="Courtesy tone clip"
              data={assetOptions(assets, "courtesy_tone")}
              value={sounds.values.courtesy_tone_asset_id || null}
              onChange={(value) => sounds.setField("courtesy_tone_asset_id", value || "")}
              clearable
              searchable
              placeholder="(built-in)"
            />
            <Select
              label="Voice ID"
              data={assetOptions(assets, "id")}
              value={sounds.values.id_asset_id || null}
              onChange={(value) => sounds.setField("id_asset_id", value || "")}
              clearable
              searchable
              placeholder="(built-in)"
            />
            <Select
              label="Timeout tone"
              data={assetOptions(assets, "timeout_tone")}
              value={sounds.values.timeout_tone_asset_id || null}
              onChange={(value) => sounds.setField("timeout_tone_asset_id", value || "")}
              clearable
              searchable
              placeholder="(built-in)"
            />
          </SimpleGrid>
          <Text c="dimmed" size="sm" maw="75ch">
            The courtesy tone above follows local users. A different one after a linked station or a phone call tells listeners who just spoke.
          </Text>

          <Accordion variant="separated" radius="md">
            <Accordion.Item value="link">
              <Accordion.Control>After a linked station</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label="Built-in tone"
                    data={TONE_VARIANT_STYLES}
                    value={sounds.values.courtesy_tone_link_style}
                    onChange={(value) => sounds.setField("courtesy_tone_link_style", value || "same")}
                    allowDeselect={false}
                  />
                  <Select
                    label="Clip"
                    description="Plays instead of the built-in tone."
                    data={assetOptions(assets, "courtesy_tone")}
                    value={sounds.values.courtesy_tone_link_asset_id || null}
                    onChange={(value) => sounds.setField("courtesy_tone_link_asset_id", value || "")}
                    clearable
                    searchable
                    placeholder="(none)"
                  />
                </SimpleGrid>
                <Button
                  mt="md"
                  variant="subtle"
                  loading={previewBusy === "link"}
                  onClick={() => void preview("courtesy_tone_link", "link")}
                >
                  Linked station tone
                </Button>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="patch">
              <Accordion.Control>After a phone call</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label="Built-in tone"
                    data={TONE_VARIANT_STYLES}
                    value={sounds.values.courtesy_tone_patch_style}
                    onChange={(value) => sounds.setField("courtesy_tone_patch_style", value || "same")}
                    allowDeselect={false}
                  />
                  <Select
                    label="Clip"
                    description="Plays instead of the built-in tone."
                    data={assetOptions(assets, "courtesy_tone")}
                    value={sounds.values.courtesy_tone_patch_asset_id || null}
                    onChange={(value) => sounds.setField("courtesy_tone_patch_asset_id", value || "")}
                    clearable
                    searchable
                    placeholder="(none)"
                  />
                </SimpleGrid>
                <Button
                  mt="md"
                  variant="subtle"
                  loading={previewBusy === "patch"}
                  onClick={() => void preview("courtesy_tone_patch", "patch")}
                >
                  Phone call tone
                </Button>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="custom">
              <Accordion.Control>Custom tone</Accordion.Control>
              <Accordion.Panel>
                <Text c="dimmed" size="sm" mb="md" maw="75ch">
                  Up to four parts, played in order. Pick &quot;Custom&quot; in any tone list above (or for nets) to use it. A pitch of 0 is a pause, and a part left empty is skipped.
                </Text>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  {customParts.map((part, index) => (
                    <Group key={index} grow align="flex-end">
                      <Text size="sm" c="dimmed" style={{ flex: "0 0 auto" }}>
                        Part {index + 1}
                      </Text>
                      <NumberInput
                        label="Pitch"
                        suffix=" Hz"
                        min={0}
                        max={3000}
                        step={10}
                        value={part[0] === "" ? undefined : Number(part[0])}
                        onChange={(value) => setCustomPart(index, 0, value)}
                      />
                      <NumberInput
                        label="Length"
                        suffix=" ms"
                        min={20}
                        max={1000}
                        step={10}
                        value={part[1] === "" ? undefined : Number(part[1])}
                        onChange={(value) => setCustomPart(index, 1, value)}
                      />
                    </Group>
                  ))}
                </SimpleGrid>
                <Button
                  mt="md"
                  variant="subtle"
                  loading={previewBusy === "custom"}
                  onClick={() =>
                    void preview("courtesy_tone", "custom", {
                      courtesy_tone_style: "custom",
                      clear_courtesy_tone_asset_id: true,
                    })
                  }
                >
                  Custom tone
                </Button>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <Group gap="sm" wrap="wrap">
            <Button variant="subtle" loading={previewBusy === "courtesy"} onClick={() => void preview("courtesy_tone", "courtesy")}>
              Courtesy tone
            </Button>
            <Button variant="subtle" loading={previewBusy === "id"} onClick={() => void preview("id", "id")}>
              ID
            </Button>
            <Button variant="subtle" loading={previewBusy === "timeout"} onClick={() => void preview("timeout_tone", "timeout")}>
              Timeout tone
            </Button>
          </Group>
          <ConfigActions dirty={sounds.dirty} saving={sounds.saving} onReset={sounds.reset} />
        </Stack>
      </Card>

      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void engine.submit();
        }}
      >
        <Stack gap="md">
          <Group justify="space-between">
            <Title order={2}>Radio interface</Title>
            <Badge color={state.color} variant="light">
              {state.label}
            </Badge>
          </Group>
          <Text c="dimmed" size="sm" maw="75ch">
            Run the repeater on this computer&apos;s sound devices: receive audio comes in on the input, and repeated audio, tones and IDs go out on the output. Without a carrier-detect wire, the repeater can open on audio level or on a CTCSS tone.
          </Text>
          <Group justify="space-between" align="center">
            <Text size="sm" c="dimmed">
              Interface board: <Text span fw={600}>{BOARD_NAMES[boardPreset] ?? (boardPreset || "set up by hand")}</Text>
            </Text>
            {!isViewer ? (
              <Button variant="default" size="compact-sm" onClick={openWizard}>
                Run setup wizard
              </Button>
            ) : null}
          </Group>
          <Switch
            label="Enable live audio"
            checked={engine.values.audio_enabled}
            onChange={(event) => engine.setField("audio_enabled", event.currentTarget.checked)}
          />

          <Accordion multiple defaultValue={["devices"]} variant="separated" radius="md">
            <Accordion.Item value="devices">
              <Accordion.Control>Sound devices</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label="Input (receiver)"
                    data={deviceOptions(devices, "inputs", engine.values.audio_input_device)}
                    value={engine.values.audio_input_device || null}
                    onChange={(value) => engine.setField("audio_input_device", value || "")}
                    searchable
                    clearable
                    placeholder="System default"
                  />
                  <Select
                    label="Output (transmitter)"
                    data={deviceOptions(devices, "outputs", engine.values.audio_output_device)}
                    value={engine.values.audio_output_device || null}
                    onChange={(value) => engine.setField("audio_output_device", value || "")}
                    searchable
                    clearable
                    placeholder="System default"
                  />
                </SimpleGrid>
                <Stack gap="sm" mt="md">
                  <Switch
                    label="De-emphasize receive audio"
                    description="For flat audio taken from the discriminator, which sounds tinny without it (6 dB per octave, 300–3000 Hz)"
                    checked={engine.values.rx_deemphasis}
                    onChange={(event) => engine.setField("rx_deemphasis", event.currentTarget.checked)}
                  />
                  <Switch
                    label="Pre-emphasize transmit audio"
                    description="For a flat transmitter input that skips the radio's own pre-emphasis; turn transmit gain down if the highs clip"
                    checked={engine.values.tx_preemphasis}
                    onChange={(event) => engine.setField("tx_preemphasis", event.currentTarget.checked)}
                  />
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>

            <Accordion.Item value="cos">
              <Accordion.Control>Carrier detect</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label="Detect a carrier by"
                    data={[
                      { value: "vox", label: "Audio level (VOX)" },
                      { value: "ctcss", label: "CTCSS tone present" },
                      { value: "cm108", label: "CM108 COS input" },
                      { value: "gpio", label: "Raspberry Pi GPIO pin" },
                      { value: "serial", label: "Serial port (CTS, DSR or DCD)" },
                    ]}
                    value={engine.values.cos_source}
                    onChange={(value) => engine.setField("cos_source", value || "vox")}
                    allowDeselect={false}
                  />
                  {cosHardware ? (
                    <Select
                      label="COS polarity"
                      description="On a serial port, active high means the line is on (asserted) during a carrier."
                      data={[
                        { value: "low", label: "Active low (pulled to ground on carrier)" },
                        { value: "high", label: "Active high" },
                      ]}
                      value={engine.values.cos_polarity}
                      onChange={(value) => engine.setField("cos_polarity", value || "low")}
                      allowDeselect={false}
                    />
                  ) : null}
                  {engine.values.cos_source === "gpio" ? (
                    <NumberInput
                      label="COS pin"
                      description="BCM number; GPIO27 is header pin 13. 3.3 V only."
                      suffix=" GPIO"
                      min={2}
                      max={27}
                      value={engine.values.cos_gpio_pin}
                      onChange={(value) => engine.setField("cos_gpio_pin", Number(value) || 0)}
                      required
                    />
                  ) : null}
                  {engine.values.cos_source === "serial" ? (
                    <>
                      <TextInput
                        label="COS serial port"
                        description={
                          serialPorts.length
                            ? `Known ports: ${serialPorts.map((p) => p.path).join(", ")}`
                            : "A /dev/serial/by-id name stays the same when adapters are plugged in a different order."
                        }
                        placeholder="/dev/ttyUSB0"
                        spellCheck={false}
                        autoComplete="off"
                        value={engine.values.cos_serial_device}
                        onChange={(event) => engine.setField("cos_serial_device", event.currentTarget.value)}
                      />
                      <Select
                        label="COS line"
                        data={[
                          { value: "cts", label: "CTS" },
                          { value: "dsr", label: "DSR" },
                          { value: "dcd", label: "DCD" },
                        ]}
                        value={engine.values.cos_serial_line}
                        onChange={(value) => engine.setField("cos_serial_line", value || "cts")}
                        allowDeselect={false}
                      />
                    </>
                  ) : null}
                  <NumberInput
                    label="VOX threshold"
                    suffix=" dBFS"
                    min={-90}
                    max={0}
                    value={engine.values.vox_threshold_db}
                    onChange={(value) => engine.setField("vox_threshold_db", Number(value) || 0)}
                    required
                  />
                  <NumberInput
                    label="VOX hold"
                    description="How long audio can drop out before the carrier counts as gone."
                    suffix=" s"
                    min={0}
                    max={5}
                    step={0.05}
                    decimalScale={2}
                    value={engine.values.vox_hold}
                    onChange={(value) => engine.setField("vox_hold", Number(value) || 0)}
                    required
                  />
                  <NumberInput
                    label="VOX hold after a DTMF digit"
                    description="The hold when the quiet comes right after a keypad tone. The longer of the two holds applies."
                    suffix=" s"
                    min={0}
                    max={10}
                    step={0.5}
                    decimalScale={1}
                    value={engine.values.vox_dtmf_hold}
                    onChange={(value) => engine.setField("vox_dtmf_hold", Number(value) || 0)}
                    required
                  />
                  <NumberInput
                    label="Squelch-tail removal"
                    description="Cut this much from the end of each transmission. 0 = off."
                    suffix=" ms"
                    min={0}
                    max={300}
                    step={10}
                    value={engine.values.squelch_tail_ms}
                    onChange={(value) => engine.setField("squelch_tail_ms", Number(value) || 0)}
                    required
                  />
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>

            <Accordion.Item value="ptt">
              <Accordion.Control>PTT</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label="Key the transmitter with"
                    data={[
                      { value: "cm108", label: "CM108 interface (if set up)" },
                      { value: "gpio", label: "Raspberry Pi GPIO pin" },
                      { value: "serial", label: "Serial port (RTS or DTR)" },
                    ]}
                    value={engine.values.ptt_output}
                    onChange={(value) => engine.setField("ptt_output", value || "cm108")}
                    allowDeselect={false}
                  />
                  {engine.values.ptt_output === "gpio" ? (
                    <NumberInput
                      label="PTT pin"
                      description="BCM number; GPIO17 is header pin 11. Drive the radio through a transistor."
                      suffix=" GPIO"
                      min={2}
                      max={27}
                      value={engine.values.ptt_gpio_pin}
                      onChange={(value) => engine.setField("ptt_gpio_pin", Number(value) || 0)}
                      required
                    />
                  ) : null}
                  {engine.values.ptt_output === "serial" ? (
                    <>
                      <TextInput
                        label="PTT serial port"
                        description={
                          serialPorts.length
                            ? `Known ports: ${serialPorts.map((p) => p.path).join(", ")}`
                            : undefined
                        }
                        placeholder="/dev/ttyUSB0"
                        spellCheck={false}
                        autoComplete="off"
                        value={engine.values.ptt_serial_device}
                        onChange={(event) => engine.setField("ptt_serial_device", event.currentTarget.value)}
                      />
                      <Select
                        label="PTT line"
                        data={[
                          { value: "rts", label: "RTS" },
                          { value: "dtr", label: "DTR" },
                        ]}
                        value={engine.values.ptt_serial_line}
                        onChange={(value) => engine.setField("ptt_serial_line", value || "rts")}
                        allowDeselect={false}
                      />
                    </>
                  ) : null}
                  {engine.values.ptt_output === "gpio" || engine.values.ptt_output === "serial" ? (
                    <Select
                      label="PTT polarity"
                      description="On a serial port, active high turns the line on (asserts it) to transmit."
                      data={[
                        { value: "high", label: "Active high (pin goes high to transmit)" },
                        { value: "low", label: "Active low" },
                      ]}
                      value={engine.values.ptt_polarity}
                      onChange={(value) => engine.setField("ptt_polarity", value || "high")}
                      allowDeselect={false}
                    />
                  ) : null}
                  <NumberInput
                    label="Transmit delay"
                    description="Key up, then wait this long before any audio. 0 = off."
                    suffix=" ms"
                    min={0}
                    max={500}
                    step={10}
                    value={engine.values.tx_delay_ms}
                    onChange={(value) => engine.setField("tx_delay_ms", Number(value) || 0)}
                    required
                  />
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>

            <Accordion.Item value="tx">
              <Accordion.Control>Transmit audio</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <NumberInput
                    label="Transmit gain"
                    description="Applied to repeated receive audio."
                    suffix=" dB"
                    min={-40}
                    max={20}
                    step={0.5}
                    decimalScale={1}
                    value={engine.values.tx_gain_db}
                    onChange={(value) => engine.setField("tx_gain_db", Number(value) || 0)}
                    required
                  />
                  <Select
                    label="Transmit CTCSS tone"
                    description="Added under everything the repeater sends. Leave blank for none."
                    data={CTCSS_TONES.map((hz) => ({ value: String(hz), label: `${hz.toFixed(1)} Hz` }))}
                    value={engine.values.tx_ctcss_hz === "" ? null : String(engine.values.tx_ctcss_hz)}
                    onChange={(value) =>
                      engine.setField("tx_ctcss_hz", value == null || value === "" ? "" : Number(value))
                    }
                    searchable
                    clearable
                    placeholder="off"
                  />
                  <NumberInput
                    label="CTCSS level"
                    description="Adjust for about 500–750 Hz of deviation on the transmitter."
                    suffix=" dBFS"
                    min={-40}
                    max={-6}
                    value={engine.values.tx_ctcss_level_db}
                    onChange={(value) => engine.setField("tx_ctcss_level_db", Number(value) || 0)}
                    required
                  />
                </SimpleGrid>
                <Switch
                  mt="md"
                  label="Mute DTMF tones"
                  description="Commands are still decoded, but the tones aren't transmitted, sent to links or recorded"
                  checked={engine.values.dtmf_mute}
                  onChange={(event) => engine.setField("dtmf_mute", event.currentTarget.checked)}
                />
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <LevelMeter
            db={engineStatus?.rx_level_db ?? METER_FLOOR_DB}
            running={Boolean(engineStatus?.running)}
            open={Boolean(engineStatus?.running && engineStatus.cos_open)}
            thresholdDb={engine.values.vox_threshold_db}
            showThreshold={engine.values.cos_source === "vox"}
          />
          <Group gap="sm" wrap="wrap">
            <Text size="sm">
              RX <Text span fw={700}>{engineStatus?.running ? `${engineStatus.rx_level_db.toFixed(0)} dBFS` : "–"}</Text>
            </Text>
            <Badge color={engineStatus?.running && engineStatus.cos_open ? "green" : "gray"} variant="light">
              carrier
            </Badge>
            <Badge color={engineStatus?.running && engineStatus.ctcss_hz != null ? "green" : "gray"} variant="light">
              {engineStatus?.ctcss_hz != null ? `${engineStatus.ctcss_hz} Hz` : "no tone"}
            </Badge>
            <Badge color={engineStatus?.running && engineStatus.transmitting ? "red" : "gray"} variant="light">
              TX
            </Badge>
            {health ? (
              <Text size="sm" c="dimmed">
                {health}
              </Text>
            ) : null}
          </Group>
          {showSilenceHint ? (
            <Text size="sm" c="dimmed">
              The input has been pure digital silence. If you expected a signal: on macOS, the app running the server needs Microphone permission in System Settings › Privacy &amp; Security › Microphone, then a restart.
            </Text>
          ) : null}
          {engineStatus?.error ? (
            <Text c="red" size="sm">
              {engineStatus.error}
            </Text>
          ) : null}
          <ConfigActions dirty={engine.dirty} saving={engine.saving} onReset={engine.reset} />
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Title order={2}>Sound card levels</Title>
          <Text c="dimmed" size="sm" maw="75ch">
            The interface&apos;s own volume controls: how loud the receiver comes in, and how loud audio goes out to the transmitter. A change takes effect right away and is saved here, then set again every time live audio starts.
          </Text>
          {levels.length === 0 ? (
            <Text size="sm" c="dimmed">
              {levelNotes.join(" ") || "No mixer controls found for the selected devices."}
            </Text>
          ) : (
            <Stack gap="lg">
              {levels.map((level) => (
                <Box key={`${level.side}-${level.control}-${level.card}`}>
                  <Group justify="space-between" mb={4}>
                    <Text size="sm">
                      {SIDE_LABELS[level.side]}{" "}
                      <Text span c="dimmed" size="xs">
                        {level.control}, card {level.card}
                      </Text>
                      {!level.saved ? (
                        <Badge ml="xs" color="yellow" variant="light" size="sm">
                          not saved
                        </Badge>
                      ) : null}
                    </Text>
                    <Text size="sm" ff="monospace">
                      {soundLevelText(level)}
                    </Text>
                  </Group>
                  <Slider
                    min={level.min}
                    max={level.max}
                    step={1}
                    value={level.value}
                    disabled={isViewer}
                    onChange={(value) => {
                      setLevels((prev) =>
                        prev.map((item) =>
                          item.side === level.side && item.control === level.control && item.card === level.card
                            ? { ...item, value }
                            : item,
                        ),
                      );
                    }}
                    onChangeEnd={(value) => void setLevel(level, value)}
                  />
                </Box>
              ))}
            </Stack>
          )}
          {levelNotes.length > 0 && levels.length > 0 ? (
            <Text size="sm" c="dimmed">
              {levelNotes.join(" ")}
            </Text>
          ) : null}
          {!isViewer && unsavedLevels ? (
            <Group justify="space-between">
              <Text size="sm" c="dimmed">
                Some levels were set outside the dashboard and aren&apos;t saved yet.
              </Text>
              <Button variant="subtle" loading={levelsSaving} onClick={() => void saveUnsavedLevels()}>
                Save current levels
              </Button>
            </Group>
          ) : null}
        </Stack>
      </Card>
    </Page>
  );
}
