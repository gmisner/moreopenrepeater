import {
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  Group,
  PasswordInput,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const POLL_MS = 2000;

const STATE_BADGE: Record<string, { color: string; label: string }> = {
  requested: { color: "amber", label: "starting" },
  running: { color: "amber", label: "working" },
  needs_login: { color: "amber", label: "waiting for login" },
  succeeded: { color: "green", label: "done" },
  failed: { color: "red", label: "failed" },
};

type TailscaleStatus = {
  state: string;
  action?: string | null;
  started_at?: number | null;
  finished_at?: number | null;
  message?: string | null;
  auth_url?: string | null;
};

type TailscaleInfo = {
  available: boolean;
  installed: boolean;
  backend_state?: string | null;
  dns_name?: string | null;
  auth_url?: string | null;
  serve_enabled: boolean;
  serve_url?: string | null;
  port: number;
  cli_error?: string | null;
  status?: TailscaleStatus | null;
};

function formatTimestamp(value: string | number) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function isBusy(status: TailscaleStatus | null | undefined) {
  return Boolean(status && (status.state === "requested" || status.state === "running"));
}

function waitingLogin(status: TailscaleStatus | null | undefined) {
  return Boolean(status && status.state === "needs_login");
}

function isConnected(info: TailscaleInfo | null) {
  const state = info?.backend_state;
  return state === "Running" || state === "Starting";
}

function phaseBadge(info: TailscaleInfo): { color: string; label: string } {
  if (!info.available) return { color: "amber", label: "not set up" };
  if (!info.installed) return { color: "gray", label: "not installed" };
  if (info.auth_url && !isConnected(info)) return { color: "amber", label: "needs login" };
  if (isConnected(info) && info.serve_enabled) return { color: "green", label: "serving" };
  if (isConnected(info)) return { color: "green", label: "connected" };
  if (info.backend_state === "NeedsLogin") return { color: "amber", label: "needs login" };
  if (info.backend_state) return { color: "amber", label: info.backend_state };
  return { color: "gray", label: "installed" };
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    notifySuccess("Copied");
  } catch {
    notifyError(new Error("Couldn't copy"));
  }
}

export function Remote() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const [info, setInfo] = useState<TailscaleInfo | null>(null);
  const [authKey, setAuthKey] = useState("");
  const [logLines, setLogLines] = useState<string[]>([]);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  const pollingRef = useRef<number | null>(null);
  const infoRef = useRef<TailscaleInfo | null>(null);

  const stopPolling = () => {
    if (pollingRef.current != null) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  const loadLog = async () => {
    const log = await api<string[]>("/api/tailscale/log").catch(() => null);
    if (log) setLogLines(log);
  };

  const shouldPoll = (next: TailscaleInfo | null) =>
    isBusy(next?.status) || waitingLogin(next?.status) || Boolean(next?.auth_url && !isConnected(next));

  const startPolling = useCallback(() => {
    if (pollingRef.current != null) return;
    pollingRef.current = window.setInterval(() => {
      void (async () => {
        let next: TailscaleInfo;
        try {
          next = await api<TailscaleInfo>("/api/tailscale");
        } catch {
          return;
        }
        infoRef.current = next;
        setInfo(next);
        if (next.status) void loadLog();
        if (shouldPoll(next)) return;
        stopPolling();
        if (next.status?.state === "failed") {
          notifyError(new Error(next.status.message || "The Tailscale change failed"));
        } else if (next.status?.state === "succeeded") {
          notifySuccess(next.status.message || "Done");
        }
      })();
    }, POLL_MS);
  }, []);

  const load = useCallback(async () => {
    const next = await api<TailscaleInfo>("/api/tailscale");
    infoRef.current = next;
    setInfo(next);
    if (next.status) await loadLog();
    if (shouldPoll(next)) startPolling();
  }, [startPolling]);

  useEffect(() => {
    if (!isAdmin) return;
    load().catch(notifyError);
    return () => stopPolling();
  }, [isAdmin, load]);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [logLines]);

  const act = async (name: string, path: string, json?: Record<string, unknown>) => {
    setBusyAction(name);
    try {
      const next = await api<TailscaleInfo>(path, { method: "POST", json: json ?? {} });
      infoRef.current = next;
      setInfo(next);
      if (next.status) await loadLog();
      startPolling();
    } catch (error) {
      notifyError(error);
      await load().catch(notifyError);
    } finally {
      setBusyAction(null);
    }
  };

  if (!isAdmin) {
    return (
      <Page title="Remote access" description="Admins only.">
        <Card withBorder padding="lg" radius="md">
          <Text c="dimmed">Only admins can manage remote access.</Text>
        </Card>
      </Page>
    );
  }

  const connected = isConnected(info);
  const canAct = Boolean(info?.available && !isBusy(info.status) && !busyAction);
  const phase = info ? phaseBadge(info) : null;
  const authUrl = info?.auth_url || info?.status?.auth_url || null;
  const serveUrl = info?.serve_url || (info?.serve_enabled && info.dns_name ? `https://${info.dns_name}` : null);
  const status = info?.status;
  const progressBadge = status ? STATE_BADGE[status.state] : null;
  const progressDefaults: Record<string, string> = {
    requested: "Waiting for the helper to start…",
    running: `Working${status?.started_at ? `, started ${formatTimestamp(status.started_at * 1000)}` : ""}…`,
    needs_login: "Open the login link above to finish joining.",
    succeeded: status?.action ? `Finished ${status.action}.` : "Done.",
  };

  return (
    <Page
      title="Remote access"
      description="Reach this dashboard from your other devices over a private Tailscale network (tailnet), with HTTPS. Only devices on your tailnet can connect — nothing is opened to the public internet."
    >
      {info && !info.available ? (
        <Card withBorder padding="md" radius="md">
          <Text c="dimmed" size="sm">
            Remote access from the dashboard needs an install made with <Code>scripts/install-pi.sh</Code>.
          </Text>
        </Card>
      ) : null}

      <Card withBorder padding="lg" radius="md">
        <Group justify="space-between" mb="md">
          <Title order={2}>Status</Title>
          {phase ? (
            <Badge color={phase.color} variant="light">
              {phase.label}
            </Badge>
          ) : null}
        </Group>
        <SimpleGrid cols={1} spacing={6}>
          {[
            ["Tailscale", info?.installed ? "installed" : "not installed"],
            ["Tailnet", connected ? "connected" : info?.backend_state || "—"],
            ["Name", info?.dns_name ? <Code key="dns">{info.dns_name}</Code> : "—"],
            ["Dashboard port", info ? String(info.port) : "—"],
            ["Serve", info?.serve_enabled ? "on" : "off"],
          ].map(([label, value]) => (
            <Group key={String(label)} justify="space-between" wrap="nowrap">
              <Text size="sm" c="dimmed">
                {label}
              </Text>
              <Text size="sm">{value}</Text>
            </Group>
          ))}
        </SimpleGrid>
        {info?.cli_error ? (
          <Text c="red" size="sm" mt="md">
            {info.cli_error}
          </Text>
        ) : null}
      </Card>

      {info?.available && !info.installed ? (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Title order={2}>Install Tailscale</Title>
            <Text size="sm" c="dimmed">
              Adds Tailscale&apos;s package to this controller. You join your tailnet in the next step.
            </Text>
            <Group justify="flex-end">
              <Button
                loading={busyAction === "install"}
                disabled={!canAct}
                onClick={() => void act("install", "/api/tailscale/install")}
              >
                Install Tailscale
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : null}

      {info?.available && info.installed && !connected ? (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Title order={2}>Join your tailnet</Title>
            <Text size="sm" c="dimmed">
              Create a reusable auth key in the Tailscale admin console (Settings → Keys), or use a one-time login link.
              Either way, this controller becomes a device on your tailnet.
            </Text>
            <PasswordInput
              label="Auth key"
              description="Optional if you use the login link instead."
              placeholder="tskey-auth-…"
              value={authKey}
              onChange={(event) => setAuthKey(event.currentTarget.value)}
              autoComplete="off"
              spellCheck={false}
            />
            {authUrl ? (
              <Group gap="sm" wrap="wrap">
                <Text size="sm" c="dimmed">
                  Login link:{" "}
                  <Anchor href={authUrl} target="_blank" rel="noopener">
                    {authUrl}
                  </Anchor>
                </Text>
                <Button variant="subtle" size="compact-sm" onClick={() => void copyText(authUrl)}>
                  Copy
                </Button>
              </Group>
            ) : null}
            <Group justify="flex-end">
              <Button
                loading={busyAction === "join"}
                disabled={!canAct}
                onClick={() =>
                  void act("join", "/api/tailscale/up", { auth_key: authKey.trim() || null })
                }
              >
                Join tailnet
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : null}

      {info?.available && connected ? (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Group justify="space-between">
              <Title order={2}>Dashboard over Tailscale</Title>
              <Switch
                label="Serve dashboard"
                checked={Boolean(info.serve_enabled)}
                disabled={!canAct || busyAction === "serve"}
                onChange={(event) =>
                  void act("serve", "/api/tailscale/serve", { enabled: event.currentTarget.checked })
                }
              />
            </Group>
            <Text size="sm" c="dimmed">
              When Serve is on, open the HTTPS address below from any device signed into the same tailnet. The controller
              keeps listening on loopback; Tailscale terminates TLS.
            </Text>
            {serveUrl ? (
              <Group gap="sm" wrap="wrap">
                <Anchor href={serveUrl} target="_blank" rel="noopener">
                  {serveUrl}
                </Anchor>
                <Button variant="subtle" size="compact-sm" onClick={() => void copyText(serveUrl)}>
                  Copy
                </Button>
              </Group>
            ) : null}
          </Stack>
        </Card>
      ) : null}

      {info?.available && info.installed && (connected || info.auth_url) ? (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Title order={2}>Leave the tailnet</Title>
            <Text size="sm" c="dimmed">
              Turns Serve off and logs this controller out of Tailscale. Install stays; you can join again later.
            </Text>
            <Group justify="flex-end">
              <Button
                color="red"
                variant="light"
                loading={busyAction === "leave"}
                disabled={!canAct}
                onClick={() => {
                  if (!window.confirm("Leave the tailnet? Serve turns off and this controller logs out of Tailscale.")) {
                    return;
                  }
                  void act("leave", "/api/tailscale/down");
                }}
              >
                Leave tailnet
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : null}

      {status ? (
        <Card withBorder padding="lg" radius="md">
          <Group justify="space-between" mb="sm">
            <Title order={2}>Last change</Title>
            {progressBadge ? (
              <Badge color={progressBadge.color} variant="light">
                {progressBadge.label}
              </Badge>
            ) : (
              <Badge variant="light">{status.state}</Badge>
            )}
          </Group>
          <Text size="sm" c="dimmed" mb="sm">
            {status.message || progressDefaults[status.state] || ""}
          </Text>
          <Text
            component="pre"
            ref={logRef}
            ff="monospace"
            size="xs"
            p="sm"
            style={{
              maxHeight: 240,
              overflow: "auto",
              whiteSpace: "pre-wrap",
              border: "1px solid var(--mantine-color-default-border)",
              borderRadius: "var(--mantine-radius-md)",
              background: "var(--mantine-color-default)",
              margin: 0,
            }}
          >
            {logLines.join("\n") || " "}
          </Text>
        </Card>
      ) : null}
    </Page>
  );
}
