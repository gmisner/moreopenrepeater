import {
  Badge,
  Box,
  Button,
  Card,
  Group,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { notifyError, notifySuccess } from "../config";
import { fanText, gpioStateText, shortTime, type GpioPinStatus, type GpioStatus } from "../gpio";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { useStatus } from "../status";
import { useConfig } from "../config";
import { OnAir } from "./dashboard/OnAir";
import { ControlOverview, type History24h } from "./dashboard/ControlOverview";
import { RecentActivity, type Transmission } from "./dashboard/RecentActivity";
import "./dashboard/dashboard.css";

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
  const { config } = useConfig();

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
  const [history24h, setHistory24h] = useState<History24h | null>(null);
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
      setRecentTx(await api<Transmission[]>("/api/activity/transmissions?limit=20"));
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
    const refresh = async () => {
      try { setHistory24h(await api<History24h>("/api/activity/history24h")); }
      catch { setHistory24h(null); }
    };
    void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 30_000);
    return () => window.clearInterval(timer);
  }, []);

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

  const linkRadioOn = Boolean(linkRadio?.enabled);
  const allstarReady = Boolean(links?.available || links?.links?.length);
  const unused: { label: string; to: string }[] = [];
  if (linkRadio && !linkRadioOn) unused.push({ label: "Linked radio is off", to: "/audio" });
  if (links && !allstarReady) unused.push({ label: "AllStarLink isn't connected", to: "/allstar" });

  return (
    <Page title="Dashboard">
      <Stack gap="md" className="mor-dash">
        {!connected ? (
          <Card withBorder padding="md" radius="md" role="status" style={{ borderColor: "var(--mor-amber)", background: "color-mix(in srgb, var(--mor-amber) 12%, transparent)" }}>
            <Text c="var(--mor-amber)" fw={600}>
              Lost the connection to the controller. What&apos;s shown may be out of date. Reconnecting…
            </Text>
          </Card>
        ) : null}

        {status?.locked_out ? (
          <Card withBorder padding="md" radius="md" style={{ borderColor: "var(--mor-red)", background: "color-mix(in srgb, var(--mor-red) 10%, transparent)" }}>
            <Group justify="space-between">
              <div>
                <Text fw={700} c="var(--mor-red)">
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

        <ControlOverview
          status={status}
          connected={connected}
          links={links ? links.links.length : null}
          history={history24h}
          config={config}
          linkRadioOnline={linkRadio ? Boolean(linkRadio.enabled && linkRadio.running) : null}
          health={[
            { label: "Controller", value: connected ? "Operational" : "Disconnected", ok: connected },
            {
              label: "Radio",
              value: !connected ? "Unknown" : status?.transmitter_enabled === false ? "Muted" : "Online",
              ok: connected && status?.transmitter_enabled !== false,
            },
            {
              label: "Linking",
              value: links == null ? "Unavailable" : `${links.links.length} connected`,
              ok: links != null,
            },
            {
              label: "APRS frequency",
              value: typeof config?.aprs_frequency_mhz === "number" ? "Configured" : "Not set",
              ok: true,
            },
            {
              label: "Linked radio",
              value: !linkRadio ? "Unavailable" : linkRadio.enabled && linkRadio.running ? "Online" : "Off",
              ok: linkRadio == null ? true : !linkRadio.enabled || Boolean(linkRadio.running),
            },
          ]}
        />

        <OnAir status={status} config={config} />

        <RecentActivity rows={recentTx} />

        {unused.length ? (
          <div className="mor-unused">
            {unused.map((item) => (
              <Link key={item.to} to={item.to}>
                {item.label}
              </Link>
            ))}
          </div>
        ) : null}

        {linkRadio && linkRadioOn ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-labelledby="dash-linkradio-title">
            <Group justify="space-between" align="center" mb="sm" wrap="nowrap">
              <h2 id="dash-linkradio-title" className="mor-card-title">
                {linkRadio.name || "Linked radio"}
                {linkRadio.mode === "remote_base" ? " (remote base)" : ""}
              </h2>
              <Button component={Link} to="/audio" variant="subtle" size="xs">
                Settings
              </Button>
            </Group>
            <ul className="mor-mini-path">
              <li data-lit={linkRadio.error ? "tx" : linkRadio.running ? "rx" : undefined}>
                <span className="mor-mini-dot" aria-hidden="true" />
                <b>Link</b> {linkRadio.error ? "error" : linkRadio.running ? "running" : "stopped"}
              </li>
              <li data-lit={linkRadio.receiving ? "rx" : undefined}>
                <span className="mor-mini-dot" aria-hidden="true" />
                <b>Receive</b> {linkRadio.receiving ? "open" : "quiet"}
              </li>
              <li data-lit={linkRadio.transmitting || linkRadio.timed_out ? "tx" : undefined}>
                <span className="mor-mini-dot" aria-hidden="true" />
                <b>Transmit</b> {linkRadio.timed_out ? "timed out" : linkRadio.transmitting ? "keyed" : "off"}
              </li>
              <li>
                <b>Level</b> <span className="mor-dash-num">{linkRadio.running ? `${linkRadio.rx_level_db.toFixed(0)} dB` : "–"}</span>
              </li>
            </ul>
            {linkRadio.error || linkRadio.rig_error ? (
              <Text c="var(--mor-red)" size="sm" mt="sm">
                {linkRadio.error || linkRadio.rig_error}
              </Text>
            ) : null}
          </Card>
        ) : null}

        {sensors && sensors.length > 0 ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-labelledby="dash-sensors-title">
            <Stack gap="md">
              <Group justify="space-between">
                <h2 id="dash-sensors-title" className="mor-card-title">
                  Site sensors
                </h2>
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
          <Card withBorder padding="lg" radius="md" component="section" aria-labelledby="dash-gpio-title">
            <Stack gap="md">
              <h2 id="dash-gpio-title" className="mor-card-title">
                Outputs and inputs
              </h2>
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
                {gpio.fan && gpio.fan.output !== "none" ? (
                  <Indicator name="Transmitter fan" on={Boolean(gpio.fan.on)} value={gpio.fan.error ? "error" : fanText(gpio.fan)} />
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
                <Text c={gpio.error ? "var(--mor-red)" : "dimmed"} size="sm">
                  {gpio.error || "No CM108 interface is connected, so the pins can't be read or switched."}
                </Text>
              ) : null}
            </Stack>
          </Card>
        ) : null}

        {allstarReady ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-labelledby="dash-links-title">
            <Group justify="space-between" mb="sm">
              <h2 id="dash-links-title" className="mor-card-title">
                Links
              </h2>
              {canWrite && links && links.links.length >= 2 ? (
                <Button color="red" variant="outline" size="xs" onClick={() => void disconnectAll()}>
                  Disconnect all
                </Button>
              ) : null}
            </Group>
            {links?.links?.length ? (
              <Stack gap="xs">
                {links.links.map((link) => (
                  <Group
                    key={link.node}
                    justify="space-between"
                    p="sm"
                    style={{
                      background: link.keyed ? "color-mix(in srgb, var(--mor-green) 12%, transparent)" : "var(--mor-panel-2)",
                      borderRadius: 8,
                      border: `1px solid ${link.keyed ? "var(--mor-green)" : "var(--mor-border)"}`,
                    }}
                  >
                    <Group gap="sm">
                      <Box w={10} h={10} style={{ borderRadius: "50%", background: link.keyed ? "var(--mor-green)" : "var(--mor-control-off, #3a404a)" }} />
                      <Text ff="monospace">{link.node}</Text>
                      <Text size="sm">{[link.name, link.callsign].filter(Boolean).join(", ")}</Text>
                      {link.keyed ? <Text size="sm" fw={600} c="var(--mor-green)">Talking</Text> : null}
                      {link.mode ? <Badge variant="outline" color="gray">{link.mode}</Badge> : null}
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
                      const title = [fav.description, fav.monitor ? "monitor only" : ""].filter(Boolean).join(", ");
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
                  <Button type="button" variant={monitor ? "light" : "default"} onClick={() => setMonitor((v) => !v)} aria-pressed={monitor}>
                    Monitor only
                  </Button>
                  <Button type="submit">Connect</Button>
                </Group>
              </Stack>
            ) : null}
            {links?.error ? (
              <Text c="dimmed" size="sm" mt="sm">
                {links.error}
              </Text>
            ) : null}
          </Card>
        ) : null}

        {linkEvents.length ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-labelledby="dash-linkevents-title">
            <Group justify="space-between" mb="sm">
              <h2 id="dash-linkevents-title" className="mor-card-title">
                Link activity
              </h2>
              <Button component={Link} to="/allstar" size="xs" variant="subtle">
                AllStarLink
              </Button>
            </Group>
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
                  <Text size="xs" c="dimmed" className="mor-dash-num" style={{ flex: "0 0 auto" }}>
                    {shortTime(event.at)}
                  </Text>
                </Group>
              ))}
            </Stack>
          </Card>
        ) : null}

        <Card withBorder padding="lg" radius="md" component="section" aria-labelledby="dash-listen-title">
          <Group justify="space-between" wrap="wrap" gap="sm">
            <div>
              <h2 id="dash-listen-title" className="mor-card-title">
                Listen live
              </h2>
              <Text size="sm" c="dimmed">
                {listenNote}
              </Text>
            </div>
            <Group gap="sm">
              <SegmentedControl
                size="xs"
                value={listenSource}
                onChange={setListenSource}
                aria-label="Audio to listen to"
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
        </Card>
      </Stack>
    </Page>
  );
}
