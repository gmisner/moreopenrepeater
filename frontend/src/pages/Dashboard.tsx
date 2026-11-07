import {
  Badge,
  Box,
  Button,
  Card,
  Grid,
  Group,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { notifyError, notifySuccess } from "../config";
import { fanText, gpioStateText, shortTime, type GpioPinStatus, type GpioStatus } from "../gpio";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { useStatus } from "../status";

const STATE_COPY: Record<string, string> = {
  idle: "Waiting for a signal.",
  receiving: "Repeating a local signal.",
  transmitting_id: "Sending the station ID.",
  courtesy_tone: "Playing the courtesy tone.",
  hang_time: "Hang time before dropping the transmitter.",
  timeout: "Timed out.",
  lockout: "Locked out.",
  announcing: "Playing an announcement.",
  patch: "Phone call on the air.",
};

type LinkRow = {
  node: string;
  name?: string;
  callsign?: string;
  keyed?: boolean;
  mode?: string;
  until?: string;
};

type FavoriteRow = {
  node: string;
  name?: string;
  callsign?: string;
  description?: string;
  monitor?: boolean;
};

type LinksStatus = {
  available: boolean;
  links: LinkRow[];
  favorites: FavoriteRow[];
  error?: string;
  node?: string | null;
};

function favoriteTitle(fav: FavoriteRow) {
  const name = fav.name || fav.callsign;
  return name ? `${name} (${fav.node})` : fav.node;
}

type LinkRadioStatus = {
  enabled: boolean;
  running: boolean;
  error: string | null;
  name: string;
  mode: string;
  rx_level_db: number;
  receiving: boolean;
  transmitting: boolean;
  timed_out: boolean;
  rig_error?: string | null;
};

type Transmission = { started_at: string; duration: number; timed_out: boolean };

type LinkEvent = {
  at: string;
  kind: "connected" | "disconnected" | "keyed" | "unkeyed";
  node: string;
  label: string;
};

const LINK_EVENT_LABELS: Record<LinkEvent["kind"], string> = {
  connected: "connected",
  disconnected: "disconnected",
  keyed: "keyed",
  unkeyed: "unkeyed",
};

function formatAirtime(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

function eventBadgeColor(kind: LinkEvent["kind"]) {
  if (kind === "connected" || kind === "keyed") return "green";
  if (kind === "disconnected") return "gray";
  return "blue";
}

function Indicator({ name, on, value, danger }: { name: string; on: boolean; value: string; danger?: boolean }) {
  // Mantine rejects bg="green.light" (throws → blank React tree). Use the CSS var.
  const color = on ? (danger ? "red" : "green") : "gray";
  return (
    <Card
      withBorder
      padding="md"
      radius="md"
      style={{
        background: on ? `var(--mantine-color-${color}-light)` : undefined,
        borderColor: on ? `var(--mantine-color-${color}-4)` : undefined,
      }}
    >
      <Stack gap={4} align="center">
        <Box
          w={14}
          h={14}
          style={{
            borderRadius: "50%",
            background: on ? `var(--mantine-color-${color}-4)` : "var(--mantine-color-gray-5)",
            boxShadow: on ? `0 0 10px var(--mantine-color-${color}-4)` : undefined,
          }}
        />
        <Text fw={700} style={{ letterSpacing: "0.05em" }}>
          {name}
        </Text>
        <Text size="xs" c="dimmed">
          {value}
        </Text>
      </Stack>
    </Card>
  );
}

function GpioIndicator({
  pin,
  available,
  canSwitch,
  busy,
  onToggle,
}: {
  pin: GpioPinStatus;
  available: boolean;
  canSwitch: boolean;
  busy: boolean;
  onToggle: () => void;
}) {
  const content = (
    <Stack gap={4} align="center">
      <Box
        w={14}
        h={14}
        style={{
          borderRadius: "50%",
          background: pin.on ? "var(--mantine-color-green-4)" : "var(--mantine-color-gray-5)",
          boxShadow: pin.on ? "0 0 10px var(--mantine-color-green-4)" : undefined,
        }}
      />
      <Text fw={700} style={{ letterSpacing: "0.05em" }}>{pin.name || `GPIO${pin.pin}`}</Text>
      <Text size="xs" c="dimmed">
        {pin.mode} · {busy ? "switching…" : gpioStateText(pin, available)}
        {pin.until ? ` until ${shortTime(pin.until)}` : ""}
      </Text>
    </Stack>
  );
  const style = {
    background: pin.on ? "var(--mantine-color-green-light)" : undefined,
    borderColor: pin.on ? "var(--mantine-color-green-4)" : undefined,
    cursor: canSwitch ? "pointer" : undefined,
  };
  return canSwitch ? (
    <Card component="button" type="button" withBorder padding="md" radius="md" style={style} onClick={onToggle}>
      {content}
    </Card>
  ) : (
    <Card withBorder padding="md" radius="md" style={style}>{content}</Card>
  );
}

export function Dashboard() {
  const { status, connected } = useStatus();
  const { session } = useSession();
  const canWrite = session?.role !== "viewer";
  const state = status?.state ?? "unknown";
  const ptt = Boolean(status?.ptt_active);
  const cos = Boolean(status?.cos_active);
  const ctcss = status?.ctcss_hz;

  const [links, setLinks] = useState<LinksStatus | null>(null);
  const [linkRadio, setLinkRadio] = useState<LinkRadioStatus | null>(null);
  const [node, setNode] = useState("");
  const [monitor, setMonitor] = useState(false);
  const [listenSource, setListenSource] = useState("tx");
  const [listening, setListening] = useState(false);
  const [listenNote, setListenNote] = useState("Streams the repeater's audio to this browser (needs live audio running).");
  const [gpio, setGpio] = useState<GpioStatus | null>(null);
  const [gpioBusy, setGpioBusy] = useState<number | null>(null);
  const [sensors, setSensors] = useState<
    { id: string; name: string; units: string; value: number | null; error: string | null }[] | null
  >(null);
  const [recentTx, setRecentTx] = useState<Transmission[]>([]);
  const [linkEvents, setLinkEvents] = useState<LinkEvent[]>([]);

  const refreshLinks = useCallback(async () => {
    try {
      setLinks(await api<LinksStatus>("/api/links"));
    } catch {
      /* keep last */
    }
  }, []);

  const refreshLinkEvents = useCallback(async () => {
    try {
      const data = await api<{ events: LinkEvent[] }>("/api/links/events?limit=30");
      setLinkEvents(data.events);
    } catch {
      /* keep last */
    }
  }, []);

  const refreshRecentTx = useCallback(async () => {
    try {
      setRecentTx(await api<Transmission[]>("/api/activity/transmissions?limit=12"));
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    void refreshLinks();
    void refreshLinkEvents();
    const timer = window.setInterval(() => {
      void refreshLinks();
      void refreshLinkEvents();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [refreshLinks, refreshLinkEvents]);

  useEffect(() => {
    void refreshRecentTx();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshRecentTx();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refreshRecentTx]);

  const refreshLinkRadio = useCallback(async () => {
    try {
      setLinkRadio(await api<LinkRadioStatus>("/api/link-radio"));
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    void refreshLinkRadio();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshLinkRadio();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [refreshLinkRadio]);

  const refreshGpio = useCallback(async () => {
    try {
      setGpio(await api<GpioStatus>("/api/gpio"));
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    void refreshGpio();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshGpio();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [refreshGpio]);

  const refreshSensors = useCallback(async () => {
    try {
      const data = await api<{ sensors: { id: string; name: string; units: string; value: number | null; error: string | null }[] }>(
        "/api/sensors",
      );
      setSensors(data.sensors);
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    void refreshSensors();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshSensors();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refreshSensors]);

  useEffect(() => {
    if (!status?.locked_out) return;
  }, [status?.locked_out]);

  const connectNode = async (target: string, monitorOnly: boolean, clearInput = false) => {
    try {
      setLinks(await api<LinksStatus>("/api/links", { method: "POST", json: { node: target, monitor: monitorOnly } }));
      notifySuccess(`Connecting node ${target}`);
      if (clearInput) setNode("");
    } catch (error) {
      notifyError(error);
    }
  };

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    await connectNode(node, monitor, true);
  };

  const disconnect = async (n: string) => {
    try {
      setLinks(await api<LinksStatus>(`/api/links/${encodeURIComponent(n)}`, { method: "DELETE" }));
      notifySuccess(`Disconnecting node ${n}`);
    } catch (error) {
      notifyError(error);
    }
  };

  const disconnectAll = async () => {
    if (!confirm("Disconnect every link?")) return;
    try {
      setLinks(await api<LinksStatus>("/api/links", { method: "DELETE" }));
      notifySuccess("Disconnecting all links");
    } catch (error) {
      notifyError(error);
    }
  };

  const clearLockout = async () => {
    try {
      await api("/api/lockout/clear", { method: "POST" });
      notifySuccess("Lockout cleared");
    } catch (error) {
      notifyError(error);
    }
  };

  const switchGpio = async (pin: GpioPinStatus) => {
    setGpioBusy(pin.pin);
    try {
      setGpio(await api<GpioStatus>(`/api/gpio/${pin.pin}`, { method: "PUT", json: { on: !pin.on } }));
    } catch (error) {
      notifyError(error);
    } finally {
      setGpioBusy(null);
    }
  };

  // Listen uses the same websocket contract as vanilla listen.js.
  useEffect(() => {
    if (!listening) return;
    let socket: WebSocket | null = null;
    let context: AudioContext | null = null;
    let nextTime = 0;
    let sampleRate = 16000;
    let cancelled = false;

    const run = async () => {
      try {
        context = new AudioContext();
        await context.resume();
      } catch (error) {
        notifyError(error);
        setListening(false);
        return;
      }
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${location.host}/ws/audio?source=${listenSource}`);
      socket.binaryType = "arraybuffer";
      socket.onmessage = (event) => {
        if (!context) return;
        if (typeof event.data === "string") {
          sampleRate = JSON.parse(event.data).sample_rate;
          return;
        }
        const ints = new Int16Array(event.data as ArrayBuffer);
        const buffer = context.createBuffer(1, ints.length, sampleRate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < ints.length; i++) channel[i] = ints[i] / 32768;
        const nodeSource = context.createBufferSource();
        nodeSource.buffer = buffer;
        nodeSource.connect(context.destination);
        const now = context.currentTime;
        if (nextTime < now + 0.02 || nextTime > now + 1) nextTime = now + 0.25;
        nodeSource.start(nextTime);
        nextTime += buffer.duration;
        setListenNote(`Listening to ${listenSource === "rx" ? "the receiver" : "on-air audio"}.`);
      };
      socket.onclose = () => {
        if (!cancelled) {
          setListening(false);
          setListenNote("The audio stream closed.");
        }
      };
    };
    void run();
    return () => {
      cancelled = true;
      socket?.close();
      void context?.close();
    };
  }, [listening, listenSource]);

  return (
    <Page title="Dashboard" description="Live repeater state, pushed over WebSocket.">
      {!connected ? (
        <Card withBorder padding="md" radius="md" style={{ borderColor: "var(--mantine-color-amber-4)", background: "var(--mantine-color-amber-light)" }}>
          <Text c="amber">Lost the connection to the controller. What&apos;s shown may be out of date. Reconnecting…</Text>
        </Card>
      ) : null}

      {status?.locked_out ? (
        <Card withBorder padding="md" radius="md" style={{ borderColor: "var(--mantine-color-red-4)", background: "var(--mantine-color-red-light)" }}>
          <Group justify="space-between">
            <div>
              <Text fw={700} c="red">
                Locked out: stuck carrier
              </Text>
              <Text size="sm" c="dimmed">
                The receiver kept timing out, so the repeater has stopped repeating. IDs still go out.
              </Text>
            </div>
            {canWrite ? (
              <Button color="red" variant="outline" size="xs" onClick={() => void clearLockout()}>
                Clear lockout
              </Button>
            ) : null}
          </Group>
        </Card>
      ) : null}

      <Grid>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Card withBorder padding="lg" radius="md" h="100%">
            <Text size="xs" c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
              Controller state
            </Text>
            <Title order={1} c={state === "receiving" ? "green" : state === "timeout" || state === "lockout" ? "red" : undefined} style={{ fontSize: "1.9rem" }}>
              {String(state).replaceAll("_", " ")}
            </Title>
            <Text c="dimmed">{STATE_COPY[state] ?? ""}</Text>
            {status?.transmitter_enabled === false ? (
              <Badge component={Link} to="/timing" color="red" variant="light" mt="sm" style={{ textDecoration: "none" }}>
                transmitter off
              </Badge>
            ) : null}
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 7 }}>
          <SimpleGrid cols={3} spacing="sm" h="100%">
            <Indicator name="PTT" on={ptt} value={ptt ? "TX" : "off"} danger />
            <Indicator name="COS" on={cos} value={cos ? "RX" : "off"} />
            <Indicator name="CTCSS" on={ctcss != null} value={ctcss != null ? `${ctcss} Hz` : "none"} />
          </SimpleGrid>
        </Grid.Col>
      </Grid>

      {linkRadio ? (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Group justify="space-between" align="center">
              <div>
                <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
                  Linked Radio
                </Text>
                <Text size="sm" c="dimmed">
                  {linkRadio.name || "Linked Radio"}
                  {linkRadio.mode === "remote_base" ? " · remote base" : ""}
                  {!linkRadio.enabled ? " · off" : linkRadio.running ? "" : " · not running"}
                </Text>
              </div>
              <Button component={Link} to="/audio" variant="subtle" size="xs">
                Audio settings
              </Button>
            </Group>
            <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
              <Indicator
                name="Link"
                on={linkRadio.enabled && linkRadio.running}
                value={
                  linkRadio.error
                    ? "error"
                    : !linkRadio.enabled
                      ? "off"
                      : linkRadio.running
                        ? "running"
                        : "stopped"
                }
                danger={Boolean(linkRadio.error)}
              />
              <Indicator name="RX" on={linkRadio.receiving} value={linkRadio.receiving ? "open" : "idle"} />
              <Indicator
                name="TX"
                on={linkRadio.transmitting}
                value={linkRadio.timed_out ? "timeout" : linkRadio.transmitting ? "keyed" : "idle"}
                danger={linkRadio.timed_out}
              />
              <Indicator
                name="Level"
                on={linkRadio.running && linkRadio.rx_level_db > -80}
                value={linkRadio.running ? `${linkRadio.rx_level_db.toFixed(0)} dB` : "–"}
              />
            </SimpleGrid>
            {linkRadio.error || linkRadio.rig_error ? (
              <Text c="red" size="sm">
                {linkRadio.error || linkRadio.rig_error}
              </Text>
            ) : null}
          </Stack>
        </Card>
      ) : null}

      {sensors && sensors.length > 0 ? (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Group justify="space-between">
              <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
                Site sensors
              </Text>
              <Button component={Link} to="/sensors" variant="subtle" size="xs">
                Manage
              </Button>
            </Group>
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
              {sensors.map((sensor) => (
                <Indicator
                  key={sensor.id}
                  name={sensor.name || sensor.id}
                  on={sensor.value != null}
                  value={
                    sensor.value == null
                      ? sensor.error || "unavailable"
                      : `${Number.isInteger(sensor.value) ? sensor.value : sensor.value.toFixed(2)}${sensor.units ? ` ${sensor.units}` : ""}`
                  }
                />
              ))}
            </SimpleGrid>
          </Stack>
        </Card>
      ) : null}

      {gpio && (gpio.pins.some((pin) => pin.mode === "input" || pin.mode === "output") || gpio.fan?.output !== "none") ? (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
              GPIO
            </Text>
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
              {gpio.fan && gpio.fan.output !== "none" ? (
                <Indicator
                  name="Transmitter fan"
                  on={Boolean(gpio.fan.on)}
                  value={gpio.fan.error ? "error" : fanText(gpio.fan)}
                />
              ) : null}
              {gpio.pins
                .filter((pin) => pin.mode === "input" || pin.mode === "output")
                .map((pin) => (
                  <GpioIndicator
                    key={pin.pin}
                    pin={pin}
                    available={gpio.available}
                    canSwitch={canWrite && gpio.available && pin.mode === "output"}
                    busy={gpioBusy === pin.pin}
                    onToggle={() => void switchGpio(pin)}
                  />
                ))}
            </SimpleGrid>
            {gpio.error || (!gpio.available && gpio.pins.some((pin) => pin.mode === "input" || pin.mode === "output")) ? (
              <Text c={gpio.error ? "red" : "dimmed"} size="sm">
                {gpio.error || "No CM108 interface is connected, so the pins cannot be read or switched."}
              </Text>
            ) : null}
          </Stack>
        </Card>
      ) : null}

      <Grid>
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack gap="md">
            <Card withBorder padding="lg" radius="md">
              <Group justify="space-between" mb="sm">
                <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
                  Links
                </Text>
                {canWrite && links && links.links.length >= 2 ? (
                  <Button color="red" variant="outline" size="xs" onClick={() => void disconnectAll()}>
                    Disconnect all
                  </Button>
                ) : null}
              </Group>
              {links?.links?.length ? (
                <Stack gap="xs">
                  {links.links.map((link) => (
                    <Group key={link.node} justify="space-between" p="sm" style={{ background: link.keyed ? "var(--mantine-color-green-light)" : "var(--mantine-color-default)", borderRadius: 8, border: "1px solid var(--mantine-color-default-border)" }}>
                      <Group gap="sm">
                        <Box w={10} h={10} style={{ borderRadius: "50%", background: link.keyed ? "var(--mantine-color-green-4)" : "var(--mantine-color-gray-5)" }} />
                        <Text ff="monospace">{link.node}</Text>
                        <Text size="sm">{[link.name, link.callsign].filter(Boolean).join(" · ")}</Text>
                        {link.keyed ? <Badge color="green" variant="light">talking</Badge> : null}
                        {link.mode ? <Badge variant="outline">{link.mode}</Badge> : null}
                      </Group>
                      {canWrite && links.available ? (
                        <Button color="red" variant="outline" size="xs" onClick={() => void disconnect(link.node)}>
                          Disconnect
                        </Button>
                      ) : null}
                    </Group>
                  ))}
                </Stack>
              ) : (
                <Text c="dimmed" size="sm">
                  No nodes are linked.
                </Text>
              )}
              {canWrite && links?.available ? (
                <Stack gap="sm" mt="md">
                  {links.favorites.length > 0 ? (
                    <Group gap="xs">
                      {links.favorites.map((fav) => {
                        const linked = links.links.some((link) => link.node === fav.node);
                        const title = [fav.description, fav.monitor ? "monitor only" : ""].filter(Boolean).join(" · ");
                        return (
                          <Button
                            key={fav.node}
                            type="button"
                            size="xs"
                            variant={linked ? "light" : "default"}
                            disabled={linked}
                            title={title || undefined}
                            onClick={() => void connectNode(fav.node, Boolean(fav.monitor))}
                          >
                            {favoriteTitle(fav)}
                          </Button>
                        );
                      })}
                    </Group>
                  ) : null}
                  <Group component="form" onSubmit={connect} align="flex-end" gap="sm">
                    <TextInput label="Node number" value={node} onChange={(event) => setNode(event.currentTarget.value)} required maw={160} />
                    <Button type="button" variant={monitor ? "light" : "default"} onClick={() => setMonitor((v) => !v)}>
                      {monitor ? "Monitor on" : "Monitor only"}
                    </Button>
                    <Button type="submit">Connect</Button>
                  </Group>
                </Stack>
              ) : null}
              {links?.error || (!links?.available && !links?.links?.length) ? (
                <Text c="dimmed" size="sm" mt="sm">
                  {links?.error || "Connect the repeater to AllStarLink (see the AllStarLink page) to link nodes from here."}
                </Text>
              ) : null}
            </Card>

            <Card withBorder padding="lg" radius="md">
              <Group justify="space-between" mb="sm">
                <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
                  Link activity
                </Text>
                <Button component={Link} to="/allstar" size="xs" variant="subtle">
                  AllStarLink
                </Button>
              </Group>
              {linkEvents.length ? (
                <Stack gap={6}>
                  {linkEvents.slice(0, 12).map((event, index) => (
                    <Group key={`${event.at}-${event.node}-${event.kind}-${index}`} justify="space-between" gap="sm" wrap="nowrap">
                      <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
                        <Badge color={eventBadgeColor(event.kind)} variant="light" style={{ flex: "0 0 auto" }}>
                          {LINK_EVENT_LABELS[event.kind]}
                        </Badge>
                        <Text size="sm" ff="monospace" style={{ flex: "0 0 auto" }}>
                          {event.node === "link-radio" ? "RF" : event.node}
                        </Text>
                        <Text size="sm" c="dimmed" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {event.label || (event.node === "link-radio" ? "Linked radio" : "")}
                        </Text>
                      </Group>
                      <Text size="xs" c="dimmed" style={{ flex: "0 0 auto" }}>
                        {shortTime(event.at)}
                      </Text>
                    </Group>
                  ))}
                </Stack>
              ) : (
                <Text c="dimmed" size="sm">
                  Connects, disconnects, and key-ups show up here once AllStar or the linked radio is active.
                </Text>
              )}
            </Card>

            <Card withBorder padding="lg" radius="md">
              <Group justify="space-between" mb="sm" wrap="wrap">
                <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
                  Listen live
                </Text>
                <Group gap="sm">
                  <SegmentedControl
                    size="xs"
                    value={listenSource}
                    onChange={setListenSource}
                    data={[
                      { label: "On air", value: "tx" },
                      { label: "Receiver", value: "rx" },
                    ]}
                  />
                  <Button size="xs" variant={listening ? "light" : "default"} onClick={() => setListening((v) => !v)}>
                    {listening ? "Stop" : "Listen"}
                  </Button>
                </Group>
              </Group>
              <Text size="sm" c="dimmed">
                {listenNote}
              </Text>
            </Card>
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Card withBorder padding="lg" radius="md">
            <Group justify="space-between" mb="sm">
              <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
                Recent activity
              </Text>
              <Button component={Link} to="/activity" size="xs" variant="subtle">
                Activity
              </Button>
            </Group>
            {recentTx.length ? (
              <Stack gap={6}>
                {recentTx.map((row) => (
                  <Group key={row.started_at} justify="space-between" gap="sm" wrap="nowrap">
                    <Group gap="sm" wrap="nowrap">
                      <Text size="sm">{shortTime(row.started_at)}</Text>
                      <Text size="sm" ff="monospace" fw={600}>
                        {formatAirtime(row.duration)}
                      </Text>
                      {row.timed_out ? (
                        <Badge color="red" variant="light">
                          timeout
                        </Badge>
                      ) : row.duration < 1.5 ? (
                        <Badge variant="outline">kerchunk</Badge>
                      ) : null}
                    </Group>
                  </Group>
                ))}
              </Stack>
            ) : (
              <Text c="dimmed" size="sm">
                No transmissions recorded yet. Try the Simulator, or wait for on-air traffic.
              </Text>
            )}
          </Card>
        </Grid.Col>
      </Grid>
    </Page>
  );
}
