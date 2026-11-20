import { Badge, Card, Code, Group, List, Stack, Text } from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { labeled } from "../components/FieldHint";
import { ErrorCard, LoadingBlock } from "../components/StateCards";
import { Page } from "../layout/AppShell";

/** Slow on purpose: the API caches the probes for about 15 s, and on-Pi Chromium has little to spare. */
const REFRESH_MS = 30_000;

type CheckId = "gateway_admin" | "gateway_telnet" | "modem_kiss";

type WinlinkStatus = {
  enabled: boolean;
  callsign: string | null;
  checks: { id: CheckId; ok: boolean; reason: string }[];
  problems: string[];
};

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
          This page only shows whether the gateway answers. Sessions and message counters are not shown yet, and there are no start or
          stop buttons: run the containers from the repeater's computer, as above.
        </Text>
      </Stack>
    </Page>
  );
}
