import {
  Accordion,
  Badge,
  Button,
  Card,
  Code,
  Group,
  NumberInput,
  PasswordInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { labeled } from "../components/FieldHint";

const CHANNEL_LABELS: Record<string, string> = {
  ntfy: "ntfy",
  telegram: "Telegram",
  email: "Email",
  webhook: "Webhook",
};

const SEVERITY_COLOR: Record<string, string> = {
  info: "green",
  warning: "amber",
  critical: "red",
};

const SECRET_FIELDS = ["ntfy_token", "telegram_token", "smtp_password", "webhook_url"] as const;

type AlertSettings = {
  enabled: boolean;
  ntfy_url: string;
  telegram_chat_id: string;
  email_to: string;
  email_from: string;
  smtp_host: string;
  smtp_port: number;
  smtp_security: "starttls" | "ssl" | "none";
  smtp_username: string;
  temperature_limit_c: number;
};

type SentAlert = {
  at: string;
  key: string;
  title: string;
  message: string;
  severity: "info" | "warning" | "critical";
  sent: string[];
  errors: Record<string, string>;
  note: string;
};

type AlertsInfo = {
  settings: AlertSettings;
  secrets_set: Record<string, boolean>;
  channels: string[];
  recent: SentAlert[];
  system: {
    temperature_c?: number | null;
    throttled?: {
      under_voltage_now?: boolean;
      under_voltage_occurred?: boolean;
      throttled_now?: boolean;
      throttled_occurred?: boolean;
    } | null;
    boot_time?: number | null;
    watchdog?: boolean;
    hardware_watchdog?: boolean | null;
    disk?: {
      device?: string | null;
      written_since_boot?: number | null;
      written_lifetime?: number | null;
      free?: number | null;
      total?: number | null;
    } | null;
    sd_protection?: boolean;
  };
  last_unexpected_stop: {
    power?: boolean;
    watchdog?: string | null;
    last_seen: number;
    restarted_at: number;
  } | null;
};

type FormValues = AlertSettings & {
  ntfy_token: string;
  telegram_token: string;
  smtp_password: string;
  webhook_url: string;
};

const EMPTY_FORM: FormValues = {
  enabled: false,
  ntfy_url: "",
  ntfy_token: "",
  telegram_token: "",
  telegram_chat_id: "",
  email_to: "",
  email_from: "",
  smtp_host: "",
  smtp_port: 587,
  smtp_security: "starttls",
  smtp_username: "",
  smtp_password: "",
  webhook_url: "",
  temperature_limit_c: 80,
};

function bytes(value: number | null | undefined) {
  if (value == null) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1000 && unit < units.length - 1) {
    size /= 1000;
    unit += 1;
  }
  return `${size >= 100 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function formatTimestamp(value: string | number) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function Kv({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <SimpleGrid cols={1} spacing={6}>
      {rows.map(([label, value]) => (
        <Group key={label} justify="space-between" align="flex-start" gap="md" wrap="nowrap">
          <Text size="sm" c="dimmed" style={{ minWidth: "8rem" }}>
            {label}
          </Text>
          <Text size="sm" style={{ textAlign: "right", flex: 1 }}>
            {value}
          </Text>
        </Group>
      ))}
    </SimpleGrid>
  );
}

function channelStatus(ready: boolean, enabled: boolean) {
  if (!ready) {
    return (
      <Text size="xs" c="dimmed">
        not set up
      </Text>
    );
  }
  return (
    <Badge size="sm" color={enabled ? "green" : "gray"} variant="light">
      set up
    </Badge>
  );
}

export function Alerts() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const [info, setInfo] = useState<AlertsInfo | null>(null);
  const [form, setForm] = useState<FormValues>(EMPTY_FORM);
  const [cleared, setCleared] = useState<Set<string>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, string | null> | null>(null);

  const applyInfo = useCallback((next: AlertsInfo) => {
    setInfo(next);
    setForm({
      ...EMPTY_FORM,
      ...next.settings,
      ntfy_token: "",
      telegram_token: "",
      smtp_password: "",
      webhook_url: "",
    });
    setCleared(new Set());
    setDirty(false);
  }, []);

  const load = useCallback(async () => {
    applyInfo(await api<AlertsInfo>("/api/alerts"));
  }, [applyInfo]);

  useEffect(() => {
    if (!isAdmin) return;
    load().catch(notifyError);
  }, [isAdmin, load]);

  const setField = <K extends keyof FormValues>(name: K, value: FormValues[K]) => {
    setForm((prev) => ({ ...prev, [name]: value }));
    setDirty(true);
  };

  const clearSecret = (name: (typeof SECRET_FIELDS)[number]) => {
    setCleared((prev) => new Set(prev).add(name));
    setForm((prev) => ({ ...prev, [name]: "" }));
    setDirty(true);
  };

  const secretPlaceholder = (name: (typeof SECRET_FIELDS)[number]) => {
    if (cleared.has(name)) return "Removed when you save";
    if (info?.secrets_set[name]) return "Saved — type to replace";
    return undefined;
  };

  const collect = (): Record<string, unknown> => {
    const body: Record<string, unknown> = {
      enabled: form.enabled,
      ntfy_url: form.ntfy_url.trim(),
      telegram_chat_id: form.telegram_chat_id.trim(),
      email_to: form.email_to.trim(),
      email_from: form.email_from.trim(),
      smtp_host: form.smtp_host.trim(),
      smtp_port: form.smtp_port,
      smtp_security: form.smtp_security,
      smtp_username: form.smtp_username.trim(),
      temperature_limit_c: form.temperature_limit_c,
    };
    for (const name of SECRET_FIELDS) {
      if (form[name]) body[name] = form[name];
      else if (cleared.has(name)) body[name] = "";
    }
    return body;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      applyInfo(await api<AlertsInfo>("/api/alerts", { method: "PUT", json: collect() }));
      notifySuccess("Alert settings saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    if (info) applyInfo(info);
  };

  const sendTest = async () => {
    if (dirty) {
      notifyError(new Error("Save your changes first"));
      return;
    }
    setTesting(true);
    try {
      const { results } = await api<{ results: Record<string, string | null> }>("/api/alerts/test", {
        method: "POST",
      });
      setTestResults(results);
      const failures = Object.values(results).filter(Boolean).length;
      if (failures) notifyError(new Error(`${failures} of ${Object.keys(results).length} didn't go out`));
      else notifySuccess("Test alert sent");
    } catch (error) {
      notifyError(error);
    } finally {
      setTesting(false);
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    try {
      await load();
    } catch (error) {
      notifyError(error);
    } finally {
      setRefreshing(false);
    }
  };

  if (!isAdmin) {
    return (
      <Page title="Alerts & health" description="Admins only.">
        <Card withBorder padding="lg" radius="md">
          <Text c="dimmed">Only admins can manage alerts and view health.</Text>
        </Card>
      </Page>
    );
  }

  const system = info?.system;
  const settings = info?.settings;
  const temperature = system?.temperature_c;
  const hot = temperature != null && settings != null && temperature >= settings.temperature_limit_c;
  const throttled = system?.throttled;
  const stop = info?.last_unexpected_stop;
  const disk = system?.disk;
  const showSdNote = Boolean(disk?.device?.startsWith("mmcblk") && !system?.sd_protection);

  let power: ReactNode = (
    <Text span c="dimmed">
      not a Raspberry Pi
    </Text>
  );
  let throttling: ReactNode = power;
  if (throttled) {
    power = throttled.under_voltage_now ? (
      <Badge color="red" variant="light">
        under-voltage now
      </Badge>
    ) : throttled.under_voltage_occurred ? (
      <Badge color="amber" variant="light">
        under-voltage since boot
      </Badge>
    ) : (
      <Badge color="green" variant="light">
        OK
      </Badge>
    );
    throttling = throttled.throttled_now ? (
      <Badge color="red" variant="light">
        throttled now
      </Badge>
    ) : throttled.throttled_occurred ? (
      <Badge color="amber" variant="light">
        throttled since boot
      </Badge>
    ) : (
      <Badge color="green" variant="light">
        none
      </Badge>
    );
  }

  const stopCause = stop?.power
    ? "lost power or restarted without shutting down"
    : stop?.watchdog
      ? `been restarted by the watchdog (the ${stop.watchdog} stopped responding)`
      : "stopped unexpectedly";

  return (
    <Page
      title="Alerts & health"
      description="A message on your phone when the repeater needs attention: a crash or power cut, a stuck-carrier lockout, an update that didn't work, overheating or low voltage, or live audio stopping."
    >
      <Group justify="flex-end">
        <Button variant="subtle" loading={refreshing} onClick={() => void refresh()}>
          Refresh
        </Button>
      </Group>

      {stop ? (
        <Card withBorder padding="md" radius="md">
          <Text c="dimmed" size="sm">
            When it last started, the repeater had {stopCause}. It was last known to be running at{" "}
            {formatTimestamp(stop.last_seen * 1000)} and came back at {formatTimestamp(stop.restarted_at * 1000)}.
          </Text>
        </Card>
      ) : null}

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <Card withBorder padding="lg" radius="md">
          <Title order={2} mb="md">
            The Pi
          </Title>
          <Kv
            rows={[
              [
                "CPU temperature",
                temperature == null ? (
                  <Text key="temp-na" span c="dimmed">
                    not available
                  </Text>
                ) : (
                  <Group key="temp" gap="xs" justify="flex-end">
                    {hot ? (
                      <Badge color="red" variant="light">
                        {temperature.toFixed(0)} °C
                      </Badge>
                    ) : (
                      <Text span>{temperature.toFixed(0)} °C</Text>
                    )}
                    <Text span size="xs" c="dimmed">
                      alert at {settings?.temperature_limit_c} °C
                    </Text>
                  </Group>
                ),
              ],
              ["Power supply", power],
              ["Throttling", throttling],
              ["Running since", system?.boot_time ? formatTimestamp(system.boot_time * 1000) : "--"],
              [
                "Watchdog",
                <Group key="wd" gap="xs" justify="flex-end">
                  <Badge color={system?.watchdog ? "green" : "gray"} variant="light">
                    {system?.watchdog ? "service" : "service off"}
                  </Badge>
                  {system?.hardware_watchdog == null ? null : (
                    <Badge color={system.hardware_watchdog ? "green" : "gray"} variant="light">
                      {system.hardware_watchdog ? "board" : "board off"}
                    </Badge>
                  )}
                </Group>,
              ],
            ]}
          />
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Title order={2} mb="md">
            Storage
          </Title>
          {disk ? (
            <Kv
              rows={[
                ["Device", disk.device ? <Code key="dev">{disk.device}</Code> : "--"],
                ["Written since boot", bytes(disk.written_since_boot)],
                ["Written over its life", bytes(disk.written_lifetime)],
                ["Free space", `${bytes(disk.free)} of ${bytes(disk.total)}`],
                [
                  "SD card protection",
                  <Badge key="sd" color={system?.sd_protection ? "green" : "gray"} variant="light">
                    {system?.sd_protection ? "on" : "off"}
                  </Badge>,
                ],
              ]}
            />
          ) : (
            <Text c="dimmed" size="sm">
              not available
            </Text>
          )}
          {showSdNote ? (
            <Text c="dimmed" size="sm" mt="md">
              SD cards wear out from constant small writes. Reinstalling with <Code>--protect-sd</Code> keeps the logs in
              memory; see &quot;SD card&quot; in <Code>docs/raspberry-pi.md</Code>.
            </Text>
          ) : null}
        </Card>
      </SimpleGrid>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void submit(event)}>
        <Stack gap="md">
          <Title order={2}>Where to send alerts</Title>
          <Switch
            label={labeled("Send alerts", "Each problem once when it starts (and again when it\'s over), at most a dozen an hour")}
            checked={form.enabled}
            onChange={(event) => setField("enabled", event.currentTarget.checked)}
          />

          <Accordion key={(info?.channels || []).join(",")} multiple defaultValue={info?.channels ?? []}>
            <Accordion.Item value="ntfy">
              <Accordion.Control>
                <Group justify="space-between" pr="md" wrap="nowrap">
                  <div>
                    <Text fw={600}>ntfy</Text>
                    <Text size="xs" c="dimmed">
                      Push notifications to a phone app
                    </Text>
                  </div>
                  {channelStatus(Boolean(info?.channels.includes("ntfy")), form.enabled)}
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap="sm">
                  <Text size="sm" c="dimmed">
                    Free. Install the{" "}
                    <Text span component="a" href="https://ntfy.sh" target="_blank" rel="noopener">
                      ntfy
                    </Text>{" "}
                    app, subscribe to a topic nobody could guess, and put its address here. Anyone who knows the topic can
                    read the alerts.
                  </Text>
                  <SimpleGrid cols={{ base: 1, sm: 2 }}>
                    <TextInput
                      label="Topic address"
                      placeholder="https://ntfy.sh/w1aw-repeater-x7k2"
                      value={form.ntfy_url}
                      onChange={(event) => setField("ntfy_url", event.currentTarget.value)}
                    />
                    <Stack gap={4}>
                      <PasswordInput
                        label={labeled("Access token", "Only for a server with access control.")}
                        placeholder={secretPlaceholder("ntfy_token")}
                        value={form.ntfy_token}
                        onChange={(event) => setField("ntfy_token", event.currentTarget.value)}
                        autoComplete="new-password"
                      />
                      {info?.secrets_set.ntfy_token && !cleared.has("ntfy_token") ? (
                        <Button type="button" variant="subtle" size="compact-xs" onClick={() => clearSecret("ntfy_token")}>
                          Remove
                        </Button>
                      ) : null}
                    </Stack>
                  </SimpleGrid>
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>

            <Accordion.Item value="telegram">
              <Accordion.Control>
                <Group justify="space-between" pr="md" wrap="nowrap">
                  <div>
                    <Text fw={600}>Telegram</Text>
                    <Text size="xs" c="dimmed">
                      Messages from a bot
                    </Text>
                  </div>
                  {channelStatus(Boolean(info?.channels.includes("telegram")), form.enabled)}
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap="sm">
                  <Text size="sm" c="dimmed">
                    Make a bot with @BotFather, send it a message, then put its token and your chat ID here.
                  </Text>
                  <SimpleGrid cols={{ base: 1, sm: 2 }}>
                    <Stack gap={4}>
                      <PasswordInput
                        label="Bot token"
                        placeholder={secretPlaceholder("telegram_token")}
                        value={form.telegram_token}
                        onChange={(event) => setField("telegram_token", event.currentTarget.value)}
                        autoComplete="new-password"
                      />
                      {info?.secrets_set.telegram_token && !cleared.has("telegram_token") ? (
                        <Button
                          type="button"
                          variant="subtle"
                          size="compact-xs"
                          onClick={() => clearSecret("telegram_token")}
                        >
                          Remove
                        </Button>
                      ) : null}
                    </Stack>
                    <TextInput
                      label="Chat ID"
                      placeholder="123456789"
                      inputMode="numeric"
                      value={form.telegram_chat_id}
                      onChange={(event) => setField("telegram_chat_id", event.currentTarget.value)}
                    />
                  </SimpleGrid>
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>

            <Accordion.Item value="email">
              <Accordion.Control>
                <Group justify="space-between" pr="md" wrap="nowrap">
                  <div>
                    <Text fw={600}>Email</Text>
                    <Text size="xs" c="dimmed">
                      Through your mail provider&apos;s SMTP server
                    </Text>
                  </div>
                  {channelStatus(Boolean(info?.channels.includes("email")), form.enabled)}
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <TextInput
                    label={labeled("Send to", "Separate several addresses with commas.")}
                    placeholder="you@example.com"
                    value={form.email_to}
                    onChange={(event) => setField("email_to", event.currentTarget.value)}
                  />
                  <TextInput
                    label="From"
                    placeholder="the username below"
                    value={form.email_from}
                    onChange={(event) => setField("email_from", event.currentTarget.value)}
                  />
                  <TextInput
                    label="SMTP server"
                    placeholder="smtp.gmail.com"
                    value={form.smtp_host}
                    onChange={(event) => setField("smtp_host", event.currentTarget.value)}
                  />
                  <NumberInput
                    label="Port"
                    min={1}
                    max={65535}
                    value={form.smtp_port}
                    onChange={(value) => setField("smtp_port", Number(value) || 587)}
                    required
                  />
                  <Select
                    label="Security"
                    data={[
                      { value: "starttls", label: "STARTTLS (usually port 587)" },
                      { value: "ssl", label: "SSL/TLS (usually port 465)" },
                      { value: "none", label: "None" },
                    ]}
                    value={form.smtp_security}
                    onChange={(value) => setField("smtp_security", (value as FormValues["smtp_security"]) || "starttls")}
                  />
                  <TextInput
                    label="Username"
                    value={form.smtp_username}
                    onChange={(event) => setField("smtp_username", event.currentTarget.value)}
                  />
                  <Stack gap={4}>
                    <PasswordInput
                      label={labeled("Password", "For Gmail, an app password.")}
                      placeholder={secretPlaceholder("smtp_password")}
                      value={form.smtp_password}
                      onChange={(event) => setField("smtp_password", event.currentTarget.value)}
                      autoComplete="new-password"
                    />
                    {info?.secrets_set.smtp_password && !cleared.has("smtp_password") ? (
                      <Button
                        type="button"
                        variant="subtle"
                        size="compact-xs"
                        onClick={() => clearSecret("smtp_password")}
                      >
                        Remove
                      </Button>
                    ) : null}
                  </Stack>
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>

            <Accordion.Item value="webhook">
              <Accordion.Control>
                <Group justify="space-between" pr="md" wrap="nowrap">
                  <div>
                    <Text fw={600}>Webhook</Text>
                    <Text size="xs" c="dimmed">
                      Slack, Discord or anything that takes a JSON POST
                    </Text>
                  </div>
                  {channelStatus(Boolean(info?.channels.includes("webhook")), form.enabled)}
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap={4}>
                  <PasswordInput
                    label={labeled("URL", "Gets a JSON POST. Slack and Discord webhook URLs work as they are.")}
                    placeholder={secretPlaceholder("webhook_url") || "https://hooks.slack.com/services/…"}
                    value={form.webhook_url}
                    onChange={(event) => setField("webhook_url", event.currentTarget.value)}
                    autoComplete="new-password"
                  />
                  {info?.secrets_set.webhook_url && !cleared.has("webhook_url") ? (
                    <Button type="button" variant="subtle" size="compact-xs" onClick={() => clearSecret("webhook_url")}>
                      Remove
                    </Button>
                  ) : null}
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            Limits
          </Title>
          <NumberInput
            label={labeled("CPU temperature alert", "A Pi slows itself down at 80–85 °C.")}
            suffix=" °C"
            min={40}
            max={100}
            maw={240}
            value={form.temperature_limit_c}
            onChange={(value) => setField("temperature_limit_c", Number(value) || 80)}
            required
          />

          <Group justify="space-between" wrap="wrap">
            <Button type="button" variant="default" loading={testing} onClick={() => void sendTest()}>
              Send a test alert
            </Button>
            <ConfigActions dirty={dirty} saving={saving} onReset={reset} />
          </Group>

          {testResults ? (
            <Stack gap={4}>
              {Object.entries(testResults).map(([channel, error]) => (
                <Group key={channel} gap="xs">
                  {error ? (
                    <Badge color="red" variant="light">
                      failed
                    </Badge>
                  ) : (
                    <Badge color="green" variant="light">
                      sent
                    </Badge>
                  )}
                  <Text size="sm">
                    {CHANNEL_LABELS[channel] || channel}
                    {error ? `: ${error}` : ""}
                  </Text>
                </Group>
              ))}
            </Stack>
          ) : null}
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md">
        <Title order={2} mb="md">
          Recent alerts
        </Title>
        <Table.ScrollContainer minWidth={560}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>When</Table.Th>
                <Table.Th>Alert</Table.Th>
                <Table.Th>Sent</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(info?.recent || []).map((alert) => {
                const failed = Object.entries(alert.errors).map(
                  ([c, e]) => `${CHANNEL_LABELS[c] || c}: ${e}`,
                );
                return (
                  <Table.Tr key={`${alert.at}-${alert.key}`}>
                    <Table.Td style={{ whiteSpace: "nowrap" }}>{formatTimestamp(alert.at)}</Table.Td>
                    <Table.Td>
                      <Group gap="xs" align="flex-start" wrap="nowrap">
                        <Badge size="sm" color={SEVERITY_COLOR[alert.severity] || "gray"} variant="light">
                          {alert.severity}
                        </Badge>
                        <div>
                          <Text fw={600}>{alert.title}</Text>
                          <Text size="sm" c="dimmed">
                            {alert.message}
                          </Text>
                        </div>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {alert.sent.length ? (
                        alert.sent.map((c) => CHANNEL_LABELS[c] || c).join(", ")
                      ) : (
                        <Text span c="dimmed">
                          {alert.note || "not sent"}
                        </Text>
                      )}
                      {failed.length ? (
                        <Text size="sm" c="red">
                          {failed.join("; ")}
                        </Text>
                      ) : null}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {!info?.recent.length ? (
          <Text c="dimmed" size="sm" mt="sm">
            None since the service started.
          </Text>
        ) : null}
      </Card>
    </Page>
  );
}
