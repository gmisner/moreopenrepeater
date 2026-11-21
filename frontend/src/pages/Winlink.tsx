import { Badge, Card, Code, Group, List, Stack, Table, Text } from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { labeled } from "../components/FieldHint";
import { ErrorCard, LoadingBlock } from "../components/StateCards";
import { formatAirtime, formatBytes } from "../format";
import { Page } from "../layout/AppShell";

/** Slow on purpose: the API caches the probes for about 15 s, and on-Pi Chromium has little to spare. */
const REFRESH_MS = 30_000;

type CheckId = "gateway_admin" | "gateway_telnet" | "modem_kiss";

type Session = {
  id: string;
  callsign: string | null;
  started_at: string;
  duration_s: number;
  messages_in: number;
  messages_out: number;
  bytes_in: number;
  bytes_out: number;
  outcome: "completed" | "ended_early" | "unknown";
};

type GatewayCounters = {
  sessions_today: number;
  sessions_today_is_minimum: boolean;
  sessions_total: number;
  incomplete: number;
  messages_in: number;
  messages_out: number;
  bytes_in: number;
  bytes_out: number;
  connects: number;
  failed_connects: number;
  last_session_at: string | null;
  since: string | null;
};

type ModemCounters = {
  frames_heard: number;
  frames_sent: number;
  last_heard_at: string | null;
  last_sent_at: string | null;
  since: string | null;
};

type Activity = {
  gateway: { counters: GatewayCounters; sessions: Session[] } | null;
  modem: { counters: ModemCounters } | null;
  notes: string[];
};

type WinlinkStatus = {
  enabled: boolean;
  callsign: string | null;
  checks: { id: CheckId; ok: boolean; reason: string }[];
  problems: string[];
  activity: Activity | null;
};

const OUTCOME: Record<Session["outcome"], { label: string; color: string }> = {
  completed: { label: "Completed", color: "green" },
  ended_early: { label: "Ended early", color: "amber" },
  unknown: { label: "Unknown", color: "gray" },
};

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : "none yet";
}

function Stat({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>
        <Text size="sm" c="dimmed" component="span">
          {labeled(label, hint)}
        </Text>
      </dt>
      <dd style={{ margin: 0 }}>
        <Text fw={600} size="lg" component="span">
          {children}
        </Text>
      </dd>
    </div>
  );
}

function Stats({ children }: { children: React.ReactNode }) {
  return (
    <dl
      style={{ margin: 0, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 14rem), 1fr))", gap: "var(--mantine-spacing-md)" }}
    >
      {children}
    </dl>
  );
}

const CHECK_LABELS: Record<CheckId, { label: string; hint: string }> = {
  gateway_admin: {
    label: "Gateway admin page",
    hint: "LinBPQ's web page on the private network. A login prompt counts as answering.",
  },
  gateway_telnet: {
    label: "Gateway telnet port",
    hint: "The gateway's own login port on the private network. This only checks that it accepts a connection.",
  },
  modem_kiss: {
    label: "Packet modem",
    hint: "Dire Wolf's KISS port. The gateway talks to the radio through it.",
  },
};

const ENABLE_COMMANDS = `cd docker
cp winlink.env.example winlink.env && chmod 600 winlink.env   # set the callsign, locator, an admin password
docker compose --profile winlink up -d --build
docker compose ps direwolf winlink`;

function summary(status: WinlinkStatus): { badge: string; color: string; text: string } {
  if (!status.enabled) {
    return { badge: "Not running", color: "gray", text: "The Winlink gateway is switched off. Nothing here is wrong." };
  }
  const down = status.checks.filter((check) => !check.ok).length;
  if (down === 0) {
    return { badge: "Running", color: "green", text: "The gateway and the packet modem are both answering." };
  }
  return {
    badge: "Needs attention",
    color: "amber",
    text: `${down} of ${status.checks.length} checks are not answering. The repeater is not affected.`,
  };
}

function Counters({ activity }: { activity: Activity }) {
  const { gateway, modem, notes } = activity;
  const c = gateway?.counters;
  const m = modem?.counters;
  const since = c?.since ?? m?.since ?? null;
  return (
    <Card withBorder padding="lg" radius="md" component="section" aria-label="Winlink counters">
      <Stack gap="md">
        <h2 className="mor-card-title">
          {labeled(
            "Counters",
            <>
              Counted from the gateway's own session record and the modem's output, and kept in memory only. They start again at zero when
              a container restarts. No message text is read.
            </>,
          )}
        </h2>
        {c ? (
          <Stats>
            <Stat label="Sessions today" hint="Sessions that reached Winlink, on the station's own calendar day.">
              {c.sessions_today_is_minimum ? `at least ${c.sessions_today}` : c.sessions_today}
            </Stat>
            <Stat label="Sessions since it started">{c.sessions_total}</Stat>
            <Stat label="Messages from stations" hint="Messages a station sent up to Winlink through this gateway.">
              {c.messages_in}
            </Stat>
            <Stat label="Messages to stations" hint="Messages Winlink delivered to a station through this gateway.">
              {c.messages_out}
            </Stat>
            <Stat label="Message data from stations" hint="The size of those messages before compression, not the bytes sent on the air.">
              {formatBytes(c.bytes_in)}
            </Stat>
            <Stat label="Message data to stations" hint="The size of those messages before compression, not the bytes sent on the air.">
              {formatBytes(c.bytes_out)}
            </Stat>
            <Stat label="Connections accepted" hint="Stations that connected to the gateway on the radio.">
              {c.connects}
            </Stat>
            <Stat
              label="Connections that never reached Winlink"
              hint="A station connected but no Winlink session followed within half an hour: no Winlink password, Winlink not reachable, or the station gave up first."
            >
              {c.failed_connects}
            </Stat>
            <Stat label="Sessions that ended early" hint="The link dropped or a side stopped before the exchange finished.">
              {c.incomplete}
            </Stat>
            <Stat label="Last session">{when(c.last_session_at)}</Stat>
          </Stats>
        ) : null}
        {m ? (
          <Stats>
            <Stat label="Frames heard" hint="Packets the modem decoded off the air, from any station, not only ones for this gateway.">
              {m.frames_heard}
            </Stat>
            <Stat
              label="Frames sent"
              hint="Packets the modem sent. Dire Wolf does not log each time it keys the transmitter, so there is no count of that: this is the nearest thing it reports."
            >
              {m.frames_sent}
            </Stat>
            <Stat label="Last frame heard">{when(m.last_heard_at)}</Stat>
          </Stats>
        ) : null}
        {since ? (
          <Text size="sm" c="dimmed">
            Counting since {when(since)}.
          </Text>
        ) : null}
        {notes.map((note) => (
          <Text key={note} size="sm" c="dimmed" role="status">
            {note}
          </Text>
        ))}
      </Stack>
    </Card>
  );
}

function Sessions({ sessions }: { sessions: Session[] }) {
  return (
    <Card withBorder padding="lg" radius="md" component="section" aria-label="Recent Winlink sessions">
      <Stack gap="sm">
        <h2 className="mor-card-title">
          {labeled(
            "Recent sessions",
            <>
              Each time a station connected and reached Winlink, newest first. Only the station's callsign and the counts are kept: not
              the messages, and not what the station's software called itself.
            </>,
          )}
        </h2>
        {sessions.length === 0 ? (
          <EmptyState>No sessions yet since the gateway started. A station that connects and reaches Winlink will show here when it hangs up.</EmptyState>
        ) : (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Station</Table.Th>
                <Table.Th>Started</Table.Th>
                <Table.Th>Length</Table.Th>
                <Table.Th>From station</Table.Th>
                <Table.Th>To station</Table.Th>
                <Table.Th>How it ended</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sessions.map((session) => {
                const outcome = OUTCOME[session.outcome];
                return (
                  <Table.Tr key={session.id}>
                    <Table.Td>{session.callsign ?? <Text span c="dimmed">unknown</Text>}</Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed">
                        {when(session.started_at)}
                      </Text>
                    </Table.Td>
                    <Table.Td>{formatAirtime(session.duration_s)}</Table.Td>
                    <Table.Td>{session.messages_in}</Table.Td>
                    <Table.Td>{session.messages_out}</Table.Td>
                    <Table.Td>
                      <Badge color={outcome.color} variant="light">
                        {outcome.label}
                      </Badge>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Card>
  );
}

export function Winlink() {
  const [status, setStatus] = useState<WinlinkStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const latest = useRef(0);

  const refresh = useCallback(async () => {
    const version = ++latest.current;
    try {
      const next = await api<WinlinkStatus>("/api/winlink");
      if (version !== latest.current) return;
      setStatus(next);
      setError(null);
      setStale(false);
    } catch (failure) {
      if (version !== latest.current) return;
      setError(failure instanceof Error ? failure.message : String(failure));
      setStale(true);
    } finally {
      if (version === latest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      if (document.hidden) return;
      void refresh();
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const view = status ? summary(status) : null;

  return (
    <Page title="Winlink">
      <Stack gap="md">
        {loading && !status ? <LoadingBlock label="Loading Winlink status" /> : null}
        {error && !status ? (
          <ErrorCard title="Couldn't load Winlink status" onRetry={() => void refresh()}>
            {error}
          </ErrorCard>
        ) : null}

        {status && view ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-label="Winlink gateway status">
            <Stack gap="xs">
              <Group gap="sm" align="center">
                <h2 className="mor-card-title">
                  {labeled(
                    "Winlink gateway",
                    <>
                      An optional amateur-radio email gateway (LinBPQ behind Dire Wolf) that runs in its own containers. This page only
                      checks whether they answer. It cannot start or stop them.
                    </>,
                  )}
                </h2>
                <Badge color={view.color} variant="light">
                  {view.badge}
                </Badge>
              </Group>
              <Text c="dimmed" size="sm" maw="75ch">
                {view.text}
              </Text>
              {stale ? (
                <Text size="sm" c="amber" role="status">
                  Status may be out of date. Retrying…
                </Text>
              ) : null}
              <Text size="sm">
                Station callsign:{" "}
                {status.callsign ? (
                  <strong>{status.callsign}</strong>
                ) : (
                  <Text span c="dimmed">
                    not shown here. Put MOR_WINLINK_CALL in docker/.env too to show it.
                  </Text>
                )}
              </Text>
            </Stack>
          </Card>
        ) : null}

        {status && !status.enabled ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-label="Turn Winlink on">
            <Stack gap="sm">
              <h2 className="mor-card-title">Turn it on</h2>
              <EmptyState>
                The Winlink containers are not running. They are off by default and need a packet radio of their own, with its own sound
                card. From the docker folder on the repeater's computer:
              </EmptyState>
              <Code block>{ENABLE_COMMANDS}</Code>
              <Text size="sm" c="dimmed" maw="75ch">
                Give the packet radio's sound card number as <Code>MOR_PACKET_CARD</Code> in <Code>docker/.env</Code>, and the gateway
                password from Winlink's sysop team as <Code>MOR_WINLINK_CMS_PASSWORD</Code> when you have it. If you started it and it
                still shows here as not running, look at <Code>docker compose logs winlink</Code>. The full steps are in docs/winlink.md.
              </Text>
            </Stack>
          </Card>
        ) : null}

        {status && status.enabled ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-label="Winlink checks">
            <Stack gap="sm">
              <h2 className="mor-card-title">Checks</h2>
              <List listStyleType="none" spacing="sm">
                {status.checks.map((check) => {
                  const text = CHECK_LABELS[check.id];
                  return (
                    <List.Item key={check.id}>
                      <Group gap="sm" align="baseline" wrap="wrap">
                        <Badge color={check.ok ? "green" : "red"} variant="light" style={{ minWidth: "9ch" }}>
                          {check.ok ? "Answering" : "Not answering"}
                        </Badge>
                        <Text fw={600}>{labeled(text.label, text.hint)}</Text>
                        <Text size="sm" c="dimmed">
                          {check.reason}
                        </Text>
                      </Group>
                    </List.Item>
                  );
                })}
              </List>
              {status.problems.length > 0 ? (
                <Stack gap={4} role="alert">
                  <Text fw={600}>What to look at</Text>
                  <List size="sm" spacing={4}>
                    {status.problems.map((problem) => (
                      <List.Item key={problem}>{problem}</List.Item>
                    ))}
                  </List>
                </Stack>
              ) : null}
            </Stack>
          </Card>
        ) : null}

        {status && status.enabled && !status.activity ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-label="Winlink sessions and counters">
            <Stack gap="xs">
              <h2 className="mor-card-title">Sessions and counters</h2>
              <Text size="sm" c="dimmed" maw="75ch">
                Sessions and counters are not set up on this install: the dashboard has no folder to read them from. Updating the
                repeater's containers (docs/winlink.md) sets it up.
              </Text>
            </Stack>
          </Card>
        ) : null}

        {status && status.enabled && status.activity ? <Counters activity={status.activity} /> : null}
        {status && status.enabled && status.activity?.gateway ? <Sessions sessions={status.activity.gateway.sessions} /> : null}

        <Card withBorder padding="lg" radius="md" component="section" aria-label="Amateur radio only">
          <Stack gap="xs">
            <h2 className="mor-card-title">Amateur radio only</h2>
            <Text size="sm" maw="75ch">
              The Winlink gateway is for amateur radio stations. It is not for GMRS: GMRS's limited digital uses are not a licence for a
              general email gateway, and the gateway refuses a GMRS-looking callsign. That is a guard against a mistake, not a licence
              check. It is up to the operator to follow the rules for their station (in the US, Part 97). Amateur-radio email is not
              private, and nothing is encrypted over the air.
            </Text>
          </Stack>
        </Card>

        <Text size="sm" c="dimmed" maw="75ch">
          This page only shows what the gateway and the modem report. There are no start or stop buttons: run the containers from the
          repeater's computer, as above.
        </Text>
      </Stack>
    </Page>
  );
}
