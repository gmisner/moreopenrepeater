import {
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  FileInput,
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
import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import {
  SPARE_PINS,
  fanText,
  gpioStateText,
  pinLabel,
  shortTime,
  type GpioPinConfig,
  type GpioSchedule,
  type GpioStatus,
} from "../gpio";
import { labeled, InfoNote } from "./FieldHint";
import { ConfigActions } from "./ConfigActions";
import { soundAssignments, soundDeviceOptions } from "../deviceUsage";
import { ActionsHeader } from "./ActionsHeader";

type Asset = { id: string; kind: string; filename: string; uploaded_at: string };
type AudioDevice = { name: string; inputs: number };
type Macro = { pattern: string; description: string; action: string };

type MonitorValues = {
  monitor_enabled: boolean;
  monitor_name: string;
  monitor_input_device: string;
  monitor_squelch: string;
  monitor_vox_threshold_db: number;
  monitor_gpio_pin: number;
  monitor_gpio_polarity: string;
  monitor_gain_db: number;
  monitor_record: boolean;
};

type MonitorStatus = {
  enabled: boolean;
  running: boolean;
  error: string | null;
  level_db: number;
  squelch_open: boolean;
  device_sample_rate: number | null;
  sample_rate: number;
  dropped_input_blocks: number;
  listeners: number;
};

type GpioValues = { gpio_pins: Record<string, GpioPinConfig> };
type ScheduleValues = { gpio_schedules: GpioSchedule[] };
type FanValues = {
  fan_output: "none" | "cm108" | "gpio";
  fan_cm108_pin: number;
  fan_gpio_pin: number;
  fan_polarity: "high" | "low";
  fan_run_on_minutes: number;
  fan_temp_c: number | "" | null;
};

const MONITOR_KEYS = [
  "monitor_enabled",
  "monitor_name",
  "monitor_input_device",
  "monitor_squelch",
  "monitor_vox_threshold_db",
  "monitor_gpio_pin",
  "monitor_gpio_polarity",
  "monitor_gain_db",
  "monitor_record",
] as (keyof MonitorValues)[];

const MONITOR_DEFAULTS: MonitorValues = {
  monitor_enabled: false,
  monitor_name: "",
  monitor_input_device: "",
  monitor_squelch: "vox",
  monitor_vox_threshold_db: -40,
  monitor_gpio_pin: 22,
  monitor_gpio_polarity: "low",
  monitor_gain_db: 0,
  monitor_record: false,
};

const GPIO_KEYS = ["gpio_pins"] as (keyof GpioValues)[];
const GPIO_DEFAULTS: GpioValues = { gpio_pins: {} };
const SCHEDULE_KEYS = ["gpio_schedules"] as (keyof ScheduleValues)[];
const SCHEDULE_DEFAULTS: ScheduleValues = { gpio_schedules: [] };
const FAN_KEYS = [
  "fan_output",
  "fan_cm108_pin",
  "fan_gpio_pin",
  "fan_polarity",
  "fan_run_on_minutes",
  "fan_temp_c",
] as (keyof FanValues)[];
const FAN_DEFAULTS: FanValues = {
  fan_output: "none",
  fan_cm108_pin: 1,
  fan_gpio_pin: 26,
  fan_polarity: "high",
  fan_run_on_minutes: 3,
  fan_temp_c: "",
};

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const ASSET_KIND_LABELS: Record<string, string> = {
  courtesy_tone: "Courtesy tone",
  id: "Voice ID",
  timeout_tone: "Timeout tone",
  custom: "Custom",
};
const ASSET_ASSIGNMENTS = [
  "courtesy_tone_asset_id",
  "courtesy_tone_link_asset_id",
  "courtesy_tone_patch_asset_id",
  "net_courtesy_tone_asset_id",
  "id_asset_id",
  "long_id_asset_id",
  "timeout_tone_asset_id",
];

function meterPercent(db: number) {
  return Math.max(0, Math.min(100, ((db + 80) / 80) * 100));
}

function monitorState(status: MonitorStatus | null) {
  if (!status) return { label: "…", color: "gray" as const };
  if (status.running) return { label: "running", color: "green" as const };
  if (status.error) return { label: "error", color: "red" as const };
  return { label: status.enabled ? "stopped" : "off", color: "gray" as const };
}

export function MonitorReceiverCard({ devices }: { devices: AudioDevice[] }) {
  const { config } = useConfig();
  const monitor = useConfigSlice<MonitorValues>(MONITOR_KEYS, MONITOR_DEFAULTS);
  const [status, setStatus] = useState<MonitorStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await api<MonitorStatus>("/api/monitor-receiver");
        if (!cancelled) setStatus(next);
      } catch {
        // The next poll retries.
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const soundUsage = useMemo(
    () =>
      soundAssignments({
        audio_input_device: typeof config?.audio_input_device === "string" ? config.audio_input_device : "",
        audio_output_device: typeof config?.audio_output_device === "string" ? config.audio_output_device : "",
        link_radio_input_device:
          typeof config?.link_radio_input_device === "string" ? config.link_radio_input_device : "",
        link_radio_output_device:
          typeof config?.link_radio_output_device === "string" ? config.link_radio_output_device : "",
        monitor_input_device: monitor.values.monitor_input_device,
      }),
    [
      config?.audio_input_device,
      config?.audio_output_device,
      config?.link_radio_input_device,
      config?.link_radio_output_device,
      monitor.values.monitor_input_device,
    ],
  );
  const inputOptions = soundDeviceOptions(
    devices.map((device) => ({ ...device, outputs: 0 })),
    "inputs",
    monitor.values.monitor_input_device,
    soundUsage,
  );
  const state = monitorState(status);
  const health = status?.running
    ? [
        status.device_sample_rate
          ? status.device_sample_rate === status.sample_rate
            ? `${status.sample_rate / 1000} kHz`
            : `device ${status.device_sample_rate / 1000} kHz → ${status.sample_rate / 1000} kHz`
          : "",
        status.listeners ? `${status.listeners} listening` : "",
        status.dropped_input_blocks ? `${status.dropped_input_blocks} audio glitches` : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  return (
    <Card
      withBorder
      padding="lg"
      radius="md"
      component="form"
      onSubmit={(event) => {
        event.preventDefault();
        void monitor.submit();
      }}
    >
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={2}>Monitor receiver</Title>
          <Badge color={state.color} variant="light">
            {state.label}
          </Badge>
        </Group>
        <InfoNote>
          A second, listen-only receiver on its own sound input. Hear it on the dashboard and record it without ever
          transmitting it or putting it on the public listening page.
        </InfoNote>
        <Switch
          label="Turn on the monitor receiver"
          checked={monitor.values.monitor_enabled}
          onChange={(event) => monitor.setField("monitor_enabled", event.currentTarget.checked)}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <TextInput
            label={labeled("Name", "Shown on the dashboard's Listen live card.")}
            maxLength={40}
            placeholder="Aviation 119.1"
            value={monitor.values.monitor_name}
            onChange={(event) => monitor.setField("monitor_name", event.currentTarget.value)}
          />
          <Select
            label={labeled("Input", "Its own sound device, not the repeater's receiver.")}
            data={inputOptions}
            value={monitor.values.monitor_input_device || null}
            onChange={(value) => monitor.setField("monitor_input_device", value || "")}
            searchable
            clearable
            placeholder="System default"
          />
          <Select
            label="Squelch"
            data={[
              { value: "vox", label: "Audio level" },
              { value: "gpio", label: "Raspberry Pi GPIO pin (the radio's COS)" },
              { value: "open", label: "Always open" },
            ]}
            value={monitor.values.monitor_squelch}
            onChange={(value) => monitor.setField("monitor_squelch", value || "vox")}
            allowDeselect={false}
          />
          {monitor.values.monitor_squelch === "vox" ? (
            <NumberInput
              label="Squelch threshold"
              suffix=" dBFS"
              min={-90}
              max={0}
              value={monitor.values.monitor_vox_threshold_db}
              onChange={(value) => monitor.setField("monitor_vox_threshold_db", Number(value) || 0)}
              required
            />
          ) : null}
          {monitor.values.monitor_squelch === "gpio" ? (
            <>
              <NumberInput
                label={labeled("Squelch pin", "BCM number; GPIO22 is header pin 15. 3.3 V only.")}
                suffix=" GPIO"
                min={2}
                max={27}
                value={monitor.values.monitor_gpio_pin}
                onChange={(value) => monitor.setField("monitor_gpio_pin", Number(value) || 0)}
                required
              />
              <Select
                label="Pin polarity"
                data={[
                  { value: "low", label: "Active low (pulled to ground on a signal)" },
                  { value: "high", label: "Active high" },
                ]}
                value={monitor.values.monitor_gpio_polarity}
                onChange={(value) => monitor.setField("monitor_gpio_polarity", value || "low")}
                allowDeselect={false}
              />
            </>
          ) : null}
          <NumberInput
            label="Listening level"
            suffix=" dB"
            min={-20}
            max={20}
            value={monitor.values.monitor_gain_db}
            onChange={(value) => monitor.setField("monitor_gain_db", Number(value) || 0)}
            required
          />
        </SimpleGrid>
        <Switch
          label={labeled(
            "Record what it hears",
            "Shown on the Recordings page and kept as long as the repeater's recordings.",
          )}
          checked={monitor.values.monitor_record}
          onChange={(event) => monitor.setField("monitor_record", event.currentTarget.checked)}
        />
        <Box pos="relative" h={12} bg="var(--mantine-color-default-hover)" style={{ borderRadius: 6, overflow: "hidden" }}>
          <Box
            pos="absolute"
            inset={0}
            style={{
              transformOrigin: "left center",
              transform: `scaleX(${status?.running ? meterPercent(status.level_db) / 100 : 0})`,
              background: status?.squelch_open ? "var(--mantine-color-green-6)" : "var(--mantine-color-accent-5)",
            }}
          />
          {monitor.values.monitor_squelch === "vox" ? (
            <Box
              pos="absolute"
              top={0}
              bottom={0}
              left={`${meterPercent(monitor.values.monitor_vox_threshold_db)}%`}
              w={2}
              bg="var(--mantine-color-yellow-6)"
            />
          ) : null}
        </Box>
        <Group gap="sm" wrap="wrap">
          <Text size="sm">
            Level <Text span fw={700}>{status?.running ? `${status.level_db.toFixed(0)} dBFS` : "–"}</Text>
          </Text>
          <Badge color={status?.running && status.squelch_open ? "green" : "gray"} variant="light">
            signal
          </Badge>
          {health ? <Text size="sm" c="dimmed">{health}</Text> : null}
        </Group>
        {status?.error ? <Text c="red" size="sm">{status.error}</Text> : null}
        <ConfigActions dirty={monitor.dirty} saving={monitor.saving} onReset={monitor.reset} />
      </Stack>
    </Card>
  );
}

function macroOptions(macros: Macro[], selected?: string) {
  const options = [
    { value: "", label: "No macro" },
    ...macros.map((macro) => ({
      value: macro.pattern,
      label: `${macro.pattern} · ${macro.description || macro.action}`,
    })),
  ];
  if (selected && !macros.some((macro) => macro.pattern === selected)) {
    options.push({ value: selected, label: `${selected} (not a saved macro)` });
  }
  return options;
}

function emptySchedule(pin: number | undefined): GpioSchedule {
  return { pin: pin ?? 1, time: "", minutes: 60, days: [], enabled: true };
}

export type AudioHardwareParts = { pins: ReactNode; library: ReactNode };

/** The GPIO, schedule, fan and library cards. Pass a render function as
 * children to place the two groups separately (the Audio page puts them in
 * different tabs); without one, both render in order. */
export function AudioHardwareCards({
  assets,
  reloadAssets,
  canWrite,
  children,
}: {
  assets: Asset[];
  reloadAssets: () => Promise<void>;
  canWrite: boolean;
  children?: (parts: AudioHardwareParts) => ReactNode;
}) {
  const { config } = useConfig();
  const gpio = useConfigSlice<GpioValues>(GPIO_KEYS, GPIO_DEFAULTS);
  const schedules = useConfigSlice<ScheduleValues>(SCHEDULE_KEYS, SCHEDULE_DEFAULTS);
  const fan = useConfigSlice<FanValues>(FAN_KEYS, FAN_DEFAULTS);
  const [status, setStatus] = useState<GpioStatus | null>(null);
  const [macros, setMacros] = useState<Macro[]>([]);
  const [switchingPin, setSwitchingPin] = useState<number | null>(null);
  const [assetKind, setAssetKind] = useState("courtesy_tone");
  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  const refreshGpio = useCallback(async () => {
    setStatus(await api<GpioStatus>("/api/gpio"));
  }, []);

  useEffect(() => {
    refreshGpio().catch(() => {});
    api<Macro[]>("/api/macros").then(setMacros).catch(() => setMacros([]));
    const timer = window.setInterval(() => {
      if (!document.hidden) refreshGpio().catch(() => {});
    }, 1000);
    return () => window.clearInterval(timer);
  }, [refreshGpio]);

  const fanPin =
    fan.values.fan_output === "cm108" && !(String(fan.values.fan_cm108_pin) in gpio.values.gpio_pins)
      ? fan.values.fan_cm108_pin
      : null;
  const outputs = useMemo(
    () =>
      Object.entries(gpio.values.gpio_pins)
        .filter(([, settings]) => settings.mode === "output")
        .map(([pin, settings]) => ({ pin: Number(pin), name: settings.name })),
    [gpio.values.gpio_pins],
  );

  const updatePin = (pin: number, patch: GpioPinConfig | null) => {
    const next = { ...gpio.values.gpio_pins };
    if (patch == null || !patch.mode) delete next[String(pin)];
    else next[String(pin)] = patch;
    gpio.setField("gpio_pins", next);
  };

  const switchPin = async (pin: number, on: boolean) => {
    setSwitchingPin(pin);
    try {
      setStatus(await api<GpioStatus>(`/api/gpio/${pin}`, { method: "PUT", json: { on } }));
    } catch (error) {
      notifyError(error);
    } finally {
      setSwitchingPin(null);
    }
  };

  const savePins = async (event: FormEvent) => {
    event.preventDefault();
    await gpio.submit();
    refreshGpio().catch(() => {});
  };

  const saveSchedules = async (event: FormEvent) => {
    event.preventDefault();
    if (schedules.values.gpio_schedules.some((schedule) => schedule.days.length === 0)) {
      notifyError(new Error("Pick at least one day for each schedule."));
      return;
    }
    await schedules.submit();
    refreshGpio().catch(() => {});
  };

  const updateSchedule = (index: number, patch: Partial<GpioSchedule>) => {
    const next = schedules.values.gpio_schedules.map((schedule, i) =>
      i === index ? { ...schedule, ...patch } : schedule,
    );
    schedules.setField("gpio_schedules", next);
  };

  const uploadAsset = async (event: FormEvent) => {
    event.preventDefault();
    if (!assetFile) return;
    setUploading(true);
    try {
      const body = new FormData();
      body.set("kind", assetKind);
      body.set("file", assetFile);
      const uploaded = await api<Asset>("/api/assets", { method: "POST", body });
      notifySuccess(`Uploaded ${uploaded.filename}`);
      setAssetFile(null);
      await reloadAssets();
    } catch (error) {
      notifyError(error);
    } finally {
      setUploading(false);
    }
  };

  const removeAsset = async (asset: Asset, inUse: boolean) => {
    const warning = inUse ? "\n\nIt's currently assigned in your settings." : "";
    if (!window.confirm(`Delete "${asset.filename}"?${warning}`)) return;
    try {
      await api(`/api/assets/${encodeURIComponent(asset.id)}`, { method: "DELETE" });
      notifySuccess(`Deleted ${asset.filename}`);
      await reloadAssets();
    } catch (error) {
      notifyError(error);
    }
  };

  const pins = (
    <>
      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void savePins(event)}>
        <Stack gap="md">
          <Group justify="space-between">
            <Title order={2}>CM108 GPIO pins</Title>
            <Badge color={status?.available ? "green" : "gray"} variant="light">
              {status?.available ? "CM108 found" : "no CM108"}
            </Badge>
          </Group>
          <InfoNote>
            Spare interface pins for relays, alarms and similar hardware. Outputs can also be switched on the dashboard
            or by a DTMF macro. Inputs can say something or run a macro after changing state.
          </InfoNote>
          <div className="mor-gpio" role="group" aria-label="CM108 GPIO pins">
            <div className="mor-gpio-head" aria-hidden="true">
              <span>Pin</span>
              <span>Use</span>
              <span>Name</span>
              <span>State</span>
            </div>
            {SPARE_PINS.map((pin) => {
              const settings = gpio.values.gpio_pins[String(pin)] ?? {};
              const live = status?.pins.find((row) => row.pin === pin);
              const isFan = pin === fanPin;
              return (
                <div key={pin} className="mor-gpio-row">
                  <div className="mor-gpio-cols">
                    <span className="mor-gpio-pin mor-num">GPIO{pin}</span>
                    {isFan ? (
                      <Text size="sm" c="dimmed" className="mor-gpio-fan">
                        Transmitter fan (set up below)
                      </Text>
                    ) : (
                      <>
                        <Select
                          aria-label={`GPIO${pin} use`}
                          data={[
                            { value: "", label: "Unused" },
                            { value: "output", label: "Output" },
                            { value: "input", label: "Input" },
                          ]}
                          value={settings.mode ?? ""}
                          onChange={(value) =>
                            updatePin(pin, value ? { ...settings, mode: value as "output" | "input" } : null)
                          }
                          allowDeselect={false}
                        />
                        <TextInput
                          aria-label={`GPIO${pin} name`}
                          placeholder={settings.mode ? "Name, such as Site light" : ""}
                          maxLength={30}
                          value={settings.name ?? ""}
                          disabled={!settings.mode}
                          onChange={(event) => updatePin(pin, { ...settings, name: event.currentTarget.value })}
                        />
                      </>
                    )}
                    <div className="mor-gpio-state">
                      {live?.mode === "output" && status?.available && settings.mode === "output" && canWrite ? (
                        <Button
                          size="compact-sm"
                          variant={live.on ? "filled" : "default"}
                          loading={switchingPin === pin}
                          onClick={() => void switchPin(pin, !live.on)}
                        >
                          {gpioStateText(live, status.available)}
                        </Button>
                      ) : (
                        <Badge color={live?.on ? "green" : "gray"} variant="light">
                          {live && status ? gpioStateText(live, status.available) : "–"}
                        </Badge>
                      )}
                    </div>
                  </div>
                  {settings.mode === "input" ? (
                    <Stack gap="sm" className="mor-gpio-more">
                      <Checkbox
                        label="On when the pin reads low"
                        checked={Boolean(settings.invert)}
                        onChange={(event) => updatePin(pin, { ...settings, invert: event.currentTarget.checked })}
                      />
                      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                        {(["on", "off"] as const).map((state) => (
                          <Stack key={state} gap="xs">
                            <Text size="sm" fw={600}>
                              When {state}
                            </Text>
                            <TextInput
                              label="Say"
                              maxLength={200}
                              value={settings[`${state}_say`] ?? ""}
                              onChange={(event) =>
                                updatePin(pin, { ...settings, [`${state}_say`]: event.currentTarget.value })
                              }
                            />
                            <Select
                              label="Run"
                              data={macroOptions(macros, settings[`${state}_macro`])}
                              value={settings[`${state}_macro`] ?? ""}
                              onChange={(value) => updatePin(pin, { ...settings, [`${state}_macro`]: value || "" })}
                              searchable
                              allowDeselect={false}
                            />
                          </Stack>
                        ))}
                      </SimpleGrid>
                    </Stack>
                  ) : null}
                </div>
              );
            })}
          </div>
          {status?.error ? <Text c="red" size="sm">{status.error}</Text> : null}
          <ConfigActions dirty={gpio.dirty} saving={gpio.saving} onReset={gpio.reset} />
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void saveSchedules(event)}>
        <Stack gap="md">
          <Title order={2}>{labeled("Output schedules", <>Switch an output on at a set time each week and off after the given number of minutes. Zero minutes leaves
            it on until it is switched off.</>)}</Title>
          {schedules.values.gpio_schedules.map((schedule, index) => {
            const live = status?.schedules[index];
            const outputOptions = outputs.map(({ pin, name }) => ({ value: String(pin), label: pinLabel(pin, name) }));
            if (!outputs.some((output) => output.pin === schedule.pin)) {
              outputOptions.push({ value: String(schedule.pin), label: `GPIO${schedule.pin} (not an output)` });
            }
            return (
              <Card key={index} withBorder padding="md" radius="sm" bg="var(--mantine-color-default)">
                <Stack gap="sm">
                  <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
                    <Select
                      label="Output"
                      data={outputOptions}
                      value={String(schedule.pin)}
                      onChange={(value) => updateSchedule(index, { pin: Number(value) })}
                      allowDeselect={false}
                      required
                    />
                    <TextInput
                      label="On at"
                      type="time"
                      value={schedule.time}
                      onChange={(event) => updateSchedule(index, { time: event.currentTarget.value })}
                      required
                    />
                    <NumberInput
                      label="Minutes on"
                      min={0}
                      max={1440}
                      value={schedule.minutes}
                      onChange={(value) => updateSchedule(index, { minutes: Number(value) || 0 })}
                      required
                    />
                  </SimpleGrid>
                  <Text size="sm" fw={600}>Days</Text>
                  <Group gap="sm">
                    {DAY_NAMES.map((day, dayIndex) => (
                      <Checkbox
                        key={day}
                        label={day}
                        checked={schedule.days.includes(dayIndex)}
                        onChange={(event) => {
                          const days = event.currentTarget.checked
                            ? [...schedule.days, dayIndex].sort((a, b) => a - b)
                            : schedule.days.filter((value) => value !== dayIndex);
                          updateSchedule(index, { days });
                        }}
                      />
                    ))}
                  </Group>
                  <Group justify="space-between" wrap="wrap">
                    <Checkbox
                      label="On"
                      checked={schedule.enabled}
                      onChange={(event) => updateSchedule(index, { enabled: event.currentTarget.checked })}
                    />
                    <Group gap="sm">
                      <Text size="sm" c="dimmed">
                        {live?.until
                          ? `On until ${shortTime(live.until)}`
                          : live?.next_start
                            ? `Next: ${shortTime(live.next_start)}`
                            : ""}
                      </Text>
                      <Button
                        size="compact-sm"
                        color="red"
                        variant="subtle"
                        onClick={() =>
                          schedules.setField(
                            "gpio_schedules",
                            schedules.values.gpio_schedules.filter((_, i) => i !== index),
                          )
                        }
                      >
                        Remove
                      </Button>
                    </Group>
                  </Group>
                </Stack>
              </Card>
            );
          })}
          {schedules.values.gpio_schedules.length === 0 ? (
            <Text c="dimmed" size="sm">No output schedules. Set a pin up as an output above first.</Text>
          ) : null}
          <Group justify="space-between">
            <Button
              type="button"
              variant="subtle"
              disabled={outputs.length === 0}
              onClick={() =>
                schedules.setField("gpio_schedules", [
                  ...schedules.values.gpio_schedules,
                  emptySchedule(outputs[0]?.pin),
                ])
              }
            >
              Add a schedule
            </Button>
            <ConfigActions dirty={schedules.dirty} saving={schedules.saving} onReset={schedules.reset} />
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
          void fan.submit();
        }}
      >
        <Stack gap="md">
          <Group justify="space-between">
            <Title order={2}>Transmitter fan</Title>
            <Badge color={status?.fan?.error ? "red" : status?.fan?.on ? "green" : "gray"} variant="light">
              {status?.fan?.error ? "error" : status?.fan?.output === "none" ? "off" : fanText(status?.fan ?? null)}
            </Badge>
          </Group>
          <InfoNote>
            Run a cooling fan while the repeater transmits and for a while afterwards. Switch it through a relay or
            transistor; a pin cannot power a fan directly.
          </InfoNote>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Fan output"
              data={[
                { value: "none", label: "No fan" },
                { value: "cm108", label: "A spare CM108 GPIO pin" },
                { value: "gpio", label: "Raspberry Pi GPIO pin" },
              ]}
              value={fan.values.fan_output}
              onChange={(value) => fan.setField("fan_output", (value || "none") as FanValues["fan_output"])}
              allowDeselect={false}
            />
            {fan.values.fan_output === "cm108" ? (
              <Select
                label={labeled("CM108 pin", "Choose one that is not set up in the CM108 GPIO pins card.")}
                data={SPARE_PINS.map((pin) => ({ value: String(pin), label: `GPIO${pin}` }))}
                value={String(fan.values.fan_cm108_pin)}
                onChange={(value) => fan.setField("fan_cm108_pin", Number(value) || 1)}
                allowDeselect={false}
              />
            ) : null}
            {fan.values.fan_output === "gpio" ? (
              <NumberInput
                label={labeled("Fan pin", "BCM number; GPIO26 is header pin 37. 3.3 V only.")}
                suffix=" GPIO"
                min={2}
                max={27}
                value={fan.values.fan_gpio_pin}
                onChange={(value) => fan.setField("fan_gpio_pin", Number(value) || 0)}
                required
              />
            ) : null}
            {fan.values.fan_output !== "none" ? (
              <>
                <Select
                  label="Polarity"
                  data={[
                    { value: "high", label: "Active high (high runs the fan)" },
                    { value: "low", label: "Active low" },
                  ]}
                  value={fan.values.fan_polarity}
                  onChange={(value) => fan.setField("fan_polarity", (value || "high") as "high" | "low")}
                  allowDeselect={false}
                />
                <NumberInput
                  label={labeled("Run on", "How long the fan keeps running after the transmitter unkeys.")}
                  suffix=" min"
                  min={0}
                  max={60}
                  step={0.5}
                  decimalScale={1}
                  value={fan.values.fan_run_on_minutes}
                  onChange={(value) => fan.setField("fan_run_on_minutes", Number(value) || 0)}
                  required
                />
                <NumberInput
                  label={labeled("Also run above", "Leave blank to run only for transmitting.")}
                  suffix=" °C"
                  min={40}
                  max={90}
                  value={fan.values.fan_temp_c == null || fan.values.fan_temp_c === "" ? undefined : fan.values.fan_temp_c}
                  onChange={(value) => fan.setField("fan_temp_c", value === "" ? "" : Number(value))}
                  placeholder="off"
                />
              </>
            ) : null}
          </SimpleGrid>
          {status?.fan?.temperature_c != null ? (
            <Text size="sm" c="dimmed">CPU temperature: {status.fan.temperature_c.toFixed(0)} °C</Text>
          ) : null}
          {status?.fan?.error ? <Text c="red" size="sm">{status.fan.error}</Text> : null}
          <ConfigActions dirty={fan.dirty} saving={fan.saving} onReset={fan.reset} />
        </Stack>
      </Card>

    </>
  );

  const library = (
      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Title order={2}>Library</Title>
          <Table.ScrollContainer minWidth={680}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Kind</Table.Th>
                  <Table.Th>Filename</Table.Th>
                  <Table.Th>Uploaded</Table.Th>
                  <Table.Th>Preview</Table.Th>
                  <ActionsHeader />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {assets.map((asset) => {
                  const inUse = ASSET_ASSIGNMENTS.some((field) => config?.[field] === asset.id);
                  return (
                    <Table.Tr key={asset.id}>
                      <Table.Td><Badge variant="light">{ASSET_KIND_LABELS[asset.kind] ?? asset.kind}</Badge></Table.Td>
                      <Table.Td>
                        <Group gap="xs" wrap="nowrap">
                          <Text size="sm">{asset.filename}</Text>
                          {inUse ? <Badge color="green" variant="light" size="sm">in use</Badge> : null}
                        </Group>
                      </Table.Td>
                      <Table.Td>{new Date(asset.uploaded_at).toLocaleString()}</Table.Td>
                      <Table.Td>
                        <audio controls preload="none" src={`/api/assets/${encodeURIComponent(asset.id)}/audio`} />
                      </Table.Td>
                      <Table.Td>
                        {canWrite ? (
                          <Button
                            size="compact-sm"
                            color="red"
                            variant="subtle"
                            onClick={() => void removeAsset(asset, inUse)}
                          >
                            Delete
                          </Button>
                        ) : null}
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {assets.length === 0 ? <Text c="dimmed" size="sm">No clips uploaded yet.</Text> : null}
          {canWrite ? (
            <Group component="form" onSubmit={(event) => void uploadAsset(event)} align="flex-end" gap="sm" wrap="wrap">
              <Select
                label="Kind"
                data={Object.entries(ASSET_KIND_LABELS).map(([value, label]) => ({ value, label }))}
                value={assetKind}
                onChange={(value) => setAssetKind(value || "courtesy_tone")}
                allowDeselect={false}
              />
              <FileInput
                label="File (.wav)"
                accept="audio/wav,.wav"
                value={assetFile}
                onChange={setAssetFile}
                required
                style={{ flex: "1 1 260px" }}
              />
              <Button type="submit" loading={uploading}>Upload</Button>
            </Group>
          ) : null}
        </Stack>
      </Card>
  );

  return children ? <>{children({ pins, library })}</> : (
    <>
      {pins}
      {library}
    </>
  );
}
