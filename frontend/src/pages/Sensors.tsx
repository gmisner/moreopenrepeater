import {
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { labeled } from "../components/FieldHint";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";

type SensorType = "ds18b20" | "ads1115";

type SiteSensor = {
  id: string;
  name: string;
  type: SensorType;
  units: string;
  scale: number;
  offset: number;
  device: string;
  bus: number;
  address: number;
  channel: number;
  high: number | null;
  low: number | null;
  hysteresis: number;
  alert: boolean;
  speak_on_alert: boolean;
};

type SensorReading = SiteSensor & {
  value: number | null;
  at: number | null;
  alert_state: "high" | "low" | null;
  error: string | null;
  history?: { at: number; value: number }[];
};

type SensorsStatus = {
  enabled: boolean;
  poll_interval: number;
  sensors: SensorReading[];
};

type SensorSettings = {
  sensors_enabled: boolean;
  sensors_poll_interval: number;
};

const SETTINGS_KEYS = ["sensors_enabled", "sensors_poll_interval"] as (keyof SensorSettings)[];
const SETTINGS_DEFAULTS: SensorSettings = {
  sensors_enabled: false,
  sensors_poll_interval: 30,
};

const EMPTY_SENSOR: SiteSensor = {
  id: "",
  name: "",
  type: "ds18b20",
  units: "C",
  scale: 1,
  offset: 0,
  device: "",
  bus: 1,
  address: 0x48,
  channel: 0,
  high: null,
  low: null,
  hysteresis: 0.5,
  alert: true,
  speak_on_alert: false,
};

function formatValue(value: number | null, units: string, error: string | null): string {
  if (value == null) return error || "—";
  const text = Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, "");
  return units ? `${text} ${units}` : text;
}

function formatWhen(at: number | null): string {
  if (at == null) return "—";
  return new Date(at * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function Sensors() {
  const { config, save } = useConfig();
  const settings = useConfigSlice(SETTINGS_KEYS, SETTINGS_DEFAULTS);
  const [sensors, setSensors] = useState<SiteSensor[]>([]);
  const [status, setStatus] = useState<SensorsStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [listDirty, setListDirty] = useState(false);

  useEffect(() => {
    const list = (config?.site_sensors as SiteSensor[] | undefined) ?? [];
    setSensors(
      list.map((sensor) => ({
        ...EMPTY_SENSOR,
        ...sensor,
        high: sensor.high ?? null,
        low: sensor.low ?? null,
      })),
    );
    setListDirty(false);
  }, [config]);

  const loadStatus = useCallback(async () => {
    setStatus(await api<SensorsStatus>("/api/sensors?history_hours=6"));
  }, []);

  useEffect(() => {
    loadStatus().catch(notifyError);
    const id = window.setInterval(() => {
      if (!document.hidden) loadStatus().catch(() => {});
    }, 15000);
    return () => window.clearInterval(id);
  }, [loadStatus]);

  const updateSensor = (index: number, patch: Partial<SiteSensor>) => {
    setSensors((prev) => prev.map((sensor, i) => (i === index ? { ...sensor, ...patch } : sensor)));
    setListDirty(true);
  };

  const addSensor = () => {
    setSensors((prev) => [
      ...prev,
      {
        ...EMPTY_SENSOR,
        id: `sensor${prev.length + 1}`,
        name: `Sensor ${prev.length + 1}`,
      },
    ]);
    setListDirty(true);
  };

  const removeSensor = (index: number) => {
    setSensors((prev) => prev.filter((_, i) => i !== index));
    setListDirty(true);
  };

  const saveSensors = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const site_sensors = sensors.map((sensor) => ({
        id: sensor.id.trim().toLowerCase(),
        name: sensor.name.trim(),
        type: sensor.type,
        units: sensor.units.trim(),
        scale: sensor.scale,
        offset: sensor.offset,
        device: sensor.device.trim(),
        bus: sensor.bus,
        address: sensor.address,
        channel: sensor.channel,
        high: sensor.high,
        low: sensor.low,
        hysteresis: sensor.hysteresis,
        alert: sensor.alert,
        speak_on_alert: sensor.speak_on_alert,
      }));
      await save({ site_sensors });
      setListDirty(false);
      notifySuccess("Sensors saved");
      await loadStatus();
    } catch (error) {
      notifyError(error);
    } finally {
      setSaving(false);
    }
  };

  const readingFor = (id: string) => status?.sensors.find((sensor) => sensor.id === id);

  return (
    <Page
      title="Site sensors"
      description="Analog readings from a DS18B20 1-wire temperature probe or an ADS1115 I2C ADC channel (battery voltage, fuel, supply). Thresholds send phone alerts through Alerts & health, with hysteresis so a reading near the limit does not chatter. A DTMF macro can speak one or all readings."
    >
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void settings.submit();
        }}
      >
        <Stack gap="md">
          <Title order={3} size="h4">
            Polling
          </Title>
          <Switch
            label="Poll sensors in the background"
            checked={settings.values.sensors_enabled}
            onChange={(event) => settings.setField("sensors_enabled", event.currentTarget.checked)}
          />
          <NumberInput
            label={labeled("Poll interval", "Seconds between reads when polling is on.")}
            min={5}
            max={3600}
            value={settings.values.sensors_poll_interval}
            onChange={(value) => settings.setField("sensors_poll_interval", Number(value) || 30)}
          />
          <ConfigActions dirty={settings.dirty} saving={settings.saving} onReset={settings.reset} />
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void saveSensors(event)}>
        <Stack gap="md">
          <Group justify="space-between">
            <Title order={3} size="h4">
              Sensors
            </Title>
            <Button variant="light" onClick={addSensor} type="button">
              Add sensor
            </Button>
          </Group>

          {sensors.length === 0 ? (
            <Text c="dimmed" size="sm">
              No sensors yet. Add a DS18B20 for temperature, or an ADS1115 channel with scale and offset for a
              voltage divider.
            </Text>
          ) : (
            <Stack gap="lg">
              {sensors.map((sensor, index) => {
                const reading = readingFor(sensor.id);
                return (
                  <Stack key={`${sensor.id}-${index}`} gap="sm">
                    <Group justify="space-between" align="flex-end">
                      <Text fw={600}>
                        {sensor.name || sensor.id || `Sensor ${index + 1}`}
                        {reading ? (
                          <Text span c="dimmed" fw={400} ml="sm">
                            {formatValue(reading.value, reading.units || sensor.units, reading.error)} ·{" "}
                            {formatWhen(reading.at)}
                            {reading.alert_state ? ` · ${reading.alert_state}` : ""}
                          </Text>
                        ) : null}
                      </Text>
                      <Button color="red" variant="subtle" size="xs" type="button" onClick={() => removeSensor(index)}>
                        Remove
                      </Button>
                    </Group>
                    <Group grow align="flex-start">
                      <TextInput
                        label="Id"
                        description="Used by the sensor DTMF macro"
                        value={sensor.id}
                        onChange={(event) => updateSensor(index, { id: event.currentTarget.value })}
                        required
                      />
                      <TextInput
                        label="Name"
                        value={sensor.name}
                        onChange={(event) => updateSensor(index, { name: event.currentTarget.value })}
                        required
                      />
                      <Select
                        label="Type"
                        data={[
                          { value: "ds18b20", label: "DS18B20 (1-wire)" },
                          { value: "ads1115", label: "ADS1115 (I2C ADC)" },
                        ]}
                        value={sensor.type}
                        onChange={(value) =>
                          updateSensor(index, {
                            type: (value as SensorType) || "ds18b20",
                            units: value === "ads1115" ? "V" : "C",
                          })
                        }
                      />
                      <TextInput
                        label="Units"
                        value={sensor.units}
                        onChange={(event) => updateSensor(index, { units: event.currentTarget.value })}
                      />
                    </Group>
                    {sensor.type === "ds18b20" ? (
                      <TextInput
                        label="1-wire device"
                        description="28-… id from /sys/bus/w1/devices; blank uses the first sensor found"
                        value={sensor.device}
                        onChange={(event) => updateSensor(index, { device: event.currentTarget.value })}
                      />
                    ) : (
                      <Group grow>
                        <NumberInput
                          label="I2C bus"
                          min={0}
                          max={20}
                          value={sensor.bus}
                          onChange={(value) => updateSensor(index, { bus: Number(value) || 1 })}
                        />
                        <NumberInput
                          label="Address"
                          description="0x48–0x4B as decimal"
                          min={0x48}
                          max={0x4b}
                          value={sensor.address}
                          onChange={(value) => updateSensor(index, { address: Number(value) || 0x48 })}
                        />
                        <NumberInput
                          label="Channel"
                          min={0}
                          max={3}
                          value={sensor.channel}
                          onChange={(value) => updateSensor(index, { channel: Number(value) || 0 })}
                        />
                      </Group>
                    )}
                    <Group grow>
                      <NumberInput
                        label="Scale"
                        decimalScale={6}
                        value={sensor.scale}
                        onChange={(value) => updateSensor(index, { scale: Number(value) || 1 })}
                      />
                      <NumberInput
                        label="Offset"
                        decimalScale={6}
                        value={sensor.offset}
                        onChange={(value) => updateSensor(index, { offset: Number(value) || 0 })}
                      />
                      <NumberInput
                        label="High threshold"
                        decimalScale={3}
                        value={sensor.high ?? ""}
                        onChange={(value) =>
                          updateSensor(index, { high: value === "" || value == null ? null : Number(value) })
                        }
                      />
                      <NumberInput
                        label="Low threshold"
                        decimalScale={3}
                        value={sensor.low ?? ""}
                        onChange={(value) =>
                          updateSensor(index, { low: value === "" || value == null ? null : Number(value) })
                        }
                      />
                      <NumberInput
                        label="Hysteresis"
                        decimalScale={3}
                        min={0}
                        value={sensor.hysteresis}
                        onChange={(value) => updateSensor(index, { hysteresis: Number(value) || 0 })}
                      />
                    </Group>
                    <Group>
                      <Switch
                        label="Phone alert on threshold"
                        checked={sensor.alert}
                        onChange={(event) => updateSensor(index, { alert: event.currentTarget.checked })}
                      />
                      <Switch
                        label="Speak on air when threshold crosses"
                        checked={sensor.speak_on_alert}
                        onChange={(event) => updateSensor(index, { speak_on_alert: event.currentTarget.checked })}
                      />
                    </Group>
                    {reading?.history && reading.history.length > 0 ? (
                      <Table striped withTableBorder>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th>Recent (6 h)</Table.Th>
                            <Table.Th>Value</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {reading.history.slice(-8).map((point) => (
                            <Table.Tr key={`${point.at}-${point.value}`}>
                              <Table.Td>{formatWhen(point.at)}</Table.Td>
                              <Table.Td>{formatValue(point.value, sensor.units, null)}</Table.Td>
                            </Table.Tr>
                          ))}
                        </Table.Tbody>
                      </Table>
                    ) : null}
                  </Stack>
                );
              })}
            </Stack>
          )}

          <ConfigActions
            dirty={listDirty}
            saving={saving}
            onReset={() => {
              const list = (config?.site_sensors as SiteSensor[] | undefined) ?? [];
              setSensors(list.map((sensor) => ({ ...EMPTY_SENSOR, ...sensor })));
              setListDirty(false);
            }}
          />
        </Stack>
      </Card>
    </Page>
  );
}
