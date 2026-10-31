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

type LinksStatus = {
  available: boolean;
  links: LinkRow[];
  favorites: { node: string; description?: string; monitor?: boolean }[];
  error?: string;
  node?: string | null;
};

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

export function Dashboard() {
  const { status, connected } = useStatus();
  const { session } = useSession();
  const canWrite = session?.role !== "viewer";
  const state = status?.state ?? "unknown";
  const ptt = Boolean(status?.ptt_active);
  const cos = Boolean(status?.cos_active);
  const ctcss = status?.ctcss_hz;

  const [links, setLinks] = useState<LinksStatus | null>(null);
  const [node, setNode] = useState("");
  const [monitor, setMonitor] = useState(false);
  const [listenSource, setListenSource] = useState("tx");
  const [listening, setListening] = useState(false);
  const [listenNote, setListenNote] = useState("Streams the repeater's audio to this browser (needs live audio running).");

  const refreshLinks = useCallback(async () => {
    try {
      setLinks(await api<LinksStatus>("/api/links"));
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    void refreshLinks();
    const timer = window.setInterval(() => void refreshLinks(), 3000);
    return () => window.clearInterval(timer);
  }, [refreshLinks]);

  useEffect(() => {
    if (!status?.locked_out) return;
  }, [status?.locked_out]);

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    try {
      setLinks(await api<LinksStatus>("/api/links", { method: "POST", json: { node, monitor } }));
      notifySuccess(`Connecting node ${node}`);
      setNode("");
    } catch (error) {
      notifyError(error);
    }
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
                <Group component="form" onSubmit={connect} align="flex-end" mt="md" gap="sm">
                  <TextInput label="Node number" value={node} onChange={(event) => setNode(event.currentTarget.value)} required maw={160} />
                  <Button type="button" variant={monitor ? "light" : "default"} onClick={() => setMonitor((v) => !v)}>
                    {monitor ? "Monitor on" : "Monitor only"}
                  </Button>
                  <Button type="submit">Connect</Button>
                </Group>
              ) : null}
              {links?.error || (!links?.available && !links?.links?.length) ? (
                <Text c="dimmed" size="sm" mt="sm">
                  {links?.error || "Connect the repeater to AllStarLink (see the AllStarLink page) to link nodes from here."}
                </Text>
              ) : null}
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
                Station
              </Text>
              <Button component={Link} to="/activity" size="xs" variant="subtle">
                Activity
              </Button>
            </Group>
            <Text size="sm" c="dimmed">
              Live status is above. Settings and history are on the other pages in the sidebar.
            </Text>
          </Card>
        </Grid.Col>
      </Grid>
    </Page>
  );
}
