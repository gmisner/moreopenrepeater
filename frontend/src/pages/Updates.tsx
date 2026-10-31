import {
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  NumberInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const POLL_MS = 2000;
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const CHANNELS: Record<string, { label: string; note: string }> = {
  stable: { label: "Stable", note: "Tested releases. The one for repeaters on the air." },
  beta: { label: "Beta", note: "The next stable release while it's being tested. Good for a test repeater." },
  dev: { label: "Dev", note: "Every change as soon as it's pushed, before any testing. Expect breakage." },
};

const STATE_BADGE: Record<string, { color: string; label: string }> = {
  requested: { color: "amber", label: "starting" },
  running: { color: "amber", label: "updating" },
  succeeded: { color: "green", label: "updated" },
  rolled_back: { color: "red", label: "rolled back" },
  failed: { color: "red", label: "failed" },
};

type Commit = { sha: string; date: string; subject: string };
type UpdateStatus = {
  state: string;
  channel?: string | null;
  started_at?: number | null;
  to_sha?: string | null;
  message?: string | null;
};
type AutoUpdate = {
  enabled: boolean;
  days: number[];
  start: string;
  end: string;
  idle_minutes: number;
  last_check_at?: number | null;
  last_result?: string | null;
  next_window?: string | null;
};
type UpdatesInfo = {
  available: boolean;
  channel: string;
  version: Commit | null;
  status: UpdateStatus | null;
  auto: AutoUpdate;
};
type CheckResult = {
  channel?: string;
  latest?: Commit | null;
  relation?: "identical" | "ahead" | "behind" | "diverged" | null;
  new_commits?: Commit[];
  new_commit_count?: number;
  error?: string | null;
};

function short(sha: string | null | undefined) {
  return (sha || "").slice(0, 7);
}

function plural(count: number, word: string) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function formatTimestamp(value: string | number) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function shortTime(value: string) {
  const when = new Date(value);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return time;
  const farOff = when.getTime() - Date.now() > 6 * 24 * 3600 * 1000;
  const day = when.toLocaleDateString([], farOff ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "short" });
  return `${day} ${time}`;
}

function isBusy(status: UpdateStatus | null | undefined) {
  return Boolean(status && (status.state === "requested" || status.state === "running"));
}

export function Updates() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const [info, setInfo] = useState<UpdatesInfo | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [savingAuto, setSavingAuto] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [autoForm, setAutoForm] = useState({
    enabled: false,
    days: [] as string[],
    start: "03:00",
    end: "05:00",
    idle_minutes: 30,
  });
  const logRef = useRef<HTMLPreElement>(null);
  const pollingRef = useRef<number | null>(null);
  const channel = selected || info?.channel || "stable";

  const stopPolling = () => {
    if (pollingRef.current != null) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  const renderProgressLog = async () => {
    const log = await api<string[]>("/api/updates/log").catch(() => null);
    if (log) setLogLines(log);
  };

  const runCheck = async (refresh = false, forChannel = channel) => {
    setChecking(true);
    setCheck(null);
    try {
      const result = await api<CheckResult>(
        `/api/updates/check?channel=${forChannel}${refresh ? "&refresh=true" : ""}`,
      );
      setCheck(result);
    } catch (error) {
      setCheck({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      setChecking(false);
    }
  };

  const startPolling = () => {
    if (pollingRef.current != null) return;
    pollingRef.current = window.setInterval(() => {
      void (async () => {
        let next: UpdatesInfo;
        try {
          next = await api<UpdatesInfo>("/api/updates");
        } catch {
          return; // the service is restarting
        }
        setInfo(next);
        if (next.status) void renderProgressLog();
        if (isBusy(next.status)) return;
        stopPolling();
        if (next.status?.state === "succeeded") {
          notifySuccess("Update installed; reloading the dashboard");
          window.setTimeout(() => location.reload(), 1500);
        } else {
          notifyError(new Error(next.status?.message || "The update didn't finish"));
          setSelected(next.channel);
          void runCheck(true, next.channel);
        }
      })();
    }, POLL_MS);
  };

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    (async () => {
      try {
        const next = await api<UpdatesInfo>("/api/updates");
        if (cancelled) return;
        setInfo(next);
        setSelected((prev) => prev ?? next.channel);
        setAutoForm({
          enabled: next.auto.enabled,
          days: next.auto.days.map(String),
          start: next.auto.start,
          end: next.auto.end,
          idle_minutes: next.auto.idle_minutes,
        });
        if (next.status) await renderProgressLog();
        if (isBusy(next.status)) startPolling();
        await runCheck(false, next.channel);
      } catch (error) {
        if (!cancelled) notifyError(error);
      }
    })();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [isAdmin]); // eslint-disable-line react-hooks/exhaustive-deps -- mount load for admins

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [logLines]);

  if (!isAdmin) {
    return (
      <Page title="Updates" description="Admins only.">
        <Card withBorder padding="lg" radius="md">
          <Text c="dimmed">Only admins can manage updates.</Text>
        </Card>
      </Page>
    );
  }

  const switching = selected != null && info != null && selected !== info.channel;
  const label = CHANNELS[channel]?.label || channel;
  let checkTitle = checking || !check ? "Checking…" : "Up to date";
  let checkDetail = "";
  let action: string | null = null;
  const commits = check?.new_commits || [];

  if (check?.error) {
    checkTitle = "Couldn't check for updates";
    checkDetail = check.error;
    action = switching ? `Switch to ${label}` : "Update anyway";
  } else if (check) {
    const latest = check.latest
      ? `Latest on ${label}: ${short(check.latest.sha)}, ${formatTimestamp(check.latest.date)}.`
      : "";
    if (check.relation === "identical") {
      checkTitle = switching ? `${label} has the version that's installed` : "Up to date";
      checkDetail = latest;
      action = switching ? `Switch to ${label}` : null;
    } else if (check.relation === "behind") {
      checkTitle = `${label} is older than what's installed`;
      checkDetail = `${latest} Switching goes back to it.`;
      action = `Switch to ${label}`;
    } else {
      const count = check.new_commit_count || commits.length;
      checkTitle = check.relation === "diverged" ? `${label} has a different version` : `${plural(count, "new change")} on ${label}`;
      checkDetail =
        check.relation === "diverged" ? `${latest} It lacks some changes that are installed now.` : latest;
      action = switching ? `Switch to ${label} and update` : "Update";
    }
  }

  const installDisabled = !action || !info?.available || isBusy(info.status) || installing;

  const saveAuto = async (event: FormEvent) => {
    event.preventDefault();
    const days = autoForm.days.map(Number).filter((d) => d >= 0 && d <= 6);
    if (days.length === 0) {
      notifyError(new Error("Pick at least one day."));
      return;
    }
    setSavingAuto(true);
    try {
      const next = await api<UpdatesInfo>("/api/updates/auto", {
        method: "PUT",
        json: {
          enabled: autoForm.enabled,
          days,
          start: autoForm.start,
          end: autoForm.end,
          idle_minutes: Number(autoForm.idle_minutes),
        },
      });
      setInfo(next);
      setAutoForm({
        enabled: next.auto.enabled,
        days: next.auto.days.map(String),
        start: next.auto.start,
        end: next.auto.end,
        idle_minutes: next.auto.idle_minutes,
      });
      notifySuccess("Automatic updates saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setSavingAuto(false);
    }
  };

  const install = async () => {
    if (!window.confirm(`Install the latest ${label} version now? The repeater goes off the air for a minute or two.`)) {
      return;
    }
    setInstalling(true);
    try {
      const next = await api<UpdatesInfo>("/api/updates", { method: "POST", json: { channel } });
      setInfo(next);
      await renderProgressLog();
      startPolling();
    } catch (error) {
      notifyError(error);
    } finally {
      setInstalling(false);
    }
  };

  const autoParts: string[] = [];
  if (info) {
    if (!info.available) autoParts.push("Needs an install made with install-pi.sh.");
    else if (!info.auto.enabled) autoParts.push("Off. Updates only happen when you press Update.");
    else if (info.auto.next_window) autoParts.push(`Next window: ${shortTime(info.auto.next_window)}.`);
    if (info.auto.last_result) {
      const when = info.auto.last_check_at ? ` (checked ${formatTimestamp(info.auto.last_check_at * 1000)})` : "";
      autoParts.push(`Last: ${info.auto.last_result}${when}`);
    }
  }

  const status = info?.status;
  const statusBadge = status ? STATE_BADGE[status.state] : null;
  const statusDefaults: Record<string, string> = {
    requested: "Waiting for the updater to start…",
    running: `Updating to ${CHANNELS[status?.channel || ""]?.label ?? status?.channel ?? ""}, started ${status?.started_at ? formatTimestamp(status.started_at * 1000) : ""}.`,
    succeeded: `Installed ${short(status?.to_sha)} from ${CHANNELS[status?.channel || ""]?.label ?? status?.channel ?? ""} (${status?.started_at ? formatTimestamp(status.started_at * 1000) : ""}).`,
  };

  const channelNote =
    (CHANNELS[channel]?.note || "") + (switching ? ` Updating moves this repeater to ${CHANNELS[channel]?.label}.` : "");

  return (
    <Page
      title="Updates"
      description="New versions go to the dev channel first, then to beta for testing, and only then to stable. Production repeaters should stay on stable."
    >
      {info && !info.available ? (
        <Card withBorder padding="md" radius="md">
          <Text c="dimmed">
            Updating from the dashboard needs an install made with <Code>scripts/install-pi.sh</Code>. You can still see
            what each channel has.
          </Text>
        </Card>
      ) : null}

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <Card withBorder padding="lg" radius="md">
          <Title order={2} c="dimmed" tt="uppercase" mb="md" style={{ letterSpacing: "0.06em" }}>
            Installed
          </Title>
          <Stack gap="xs">
            <Group gap="sm">
              <Text size="sm" c="dimmed" w={80}>
                Version
              </Text>
              <Text size="sm">
                {info?.version ? (
                  <>
                    <Code>{short(info.version.sha)}</Code> {info.version.subject}
                  </>
                ) : (
                  "unknown (not a git checkout)"
                )}
              </Text>
            </Group>
            <Group gap="sm">
              <Text size="sm" c="dimmed" w={80}>
                Made
              </Text>
              <Text size="sm">{info?.version ? formatTimestamp(info.version.date) : "--"}</Text>
            </Group>
            <Group gap="sm">
              <Text size="sm" c="dimmed" w={80}>
                Channel
              </Text>
              <Text size="sm">{info ? CHANNELS[info.channel]?.label : "—"}</Text>
            </Group>
          </Stack>
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Title order={2} c="dimmed" tt="uppercase" mb="md" style={{ letterSpacing: "0.06em" }}>
            Release channel
          </Title>
          <SegmentedControl
            fullWidth
            value={channel}
            onChange={(value) => {
              setSelected(value);
              void runCheck(false, value);
            }}
            data={[
              { value: "stable", label: "Stable" },
              { value: "beta", label: "Beta" },
              { value: "dev", label: "Dev" },
            ]}
          />
          <Text size="sm" c="dimmed" mt="sm">
            {channelNote}
          </Text>
        </Card>
      </SimpleGrid>

      <Card withBorder padding="lg" radius="md">
        <Group justify="space-between" mb="sm">
          <Title order={2} style={{ margin: 0, fontSize: "1.1rem" }}>
            {checkTitle}
          </Title>
          <Button size="compact-sm" variant="subtle" loading={checking} onClick={() => void runCheck(true)}>
            Check again
          </Button>
        </Group>
        {checkDetail ? (
          <Text size="sm" c="dimmed" mb="sm">
            {checkDetail}
          </Text>
        ) : null}
        {commits.length > 0 ? (
          <Stack gap={6} mb="md">
            {commits.map((commit) => (
              <Group key={commit.sha} gap="sm" wrap="nowrap">
                <Code>{short(commit.sha)}</Code>
                <Text size="sm" style={{ flex: 1 }}>
                  {commit.subject}
                </Text>
                <Text size="xs" c="dimmed">
                  {formatTimestamp(commit.date)}
                </Text>
              </Group>
            ))}
          </Stack>
        ) : null}
        <Group justify="space-between" align="center">
          <Text size="sm" c="dimmed" maw="50ch">
            The repeater goes off the air for a minute or two, and you may have to sign in again.
          </Text>
          <Button disabled={installDisabled} loading={installing || isBusy(info?.status)} onClick={() => void install()}>
            {action || "Up to date"}
          </Button>
        </Group>
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={saveAuto}>
        <Group justify="space-between" mb="sm">
          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em", margin: 0 }}>
            Automatic updates
          </Title>
          <Switch
            label="Update by itself"
            checked={autoForm.enabled}
            onChange={(event) => setAutoForm((prev) => ({ ...prev, enabled: event.currentTarget.checked }))}
          />
        </Group>
        <Text size="sm" c="dimmed" mb="md" maw="75ch">
          Installs new versions from this repeater&apos;s channel during the hours below, once nobody has used the
          repeater (or a linked station, call or net) for a while. A version that doesn&apos;t start is rolled back and
          not tried again.
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md" mb="md">
          <TextInput
            label="From"
            type="time"
            required
            value={autoForm.start}
            onChange={(event) => setAutoForm((prev) => ({ ...prev, start: event.currentTarget.value }))}
          />
          <TextInput
            label="Until"
            description="Earlier than From runs past midnight."
            type="time"
            required
            value={autoForm.end}
            onChange={(event) => setAutoForm((prev) => ({ ...prev, end: event.currentTarget.value }))}
          />
          <NumberInput
            label="Only after it's been quiet for"
            suffix=" minutes"
            min={1}
            max={240}
            step={1}
            required
            value={autoForm.idle_minutes}
            onChange={(value) => setAutoForm((prev) => ({ ...prev, idle_minutes: Number(value) || 1 }))}
          />
        </SimpleGrid>
        <Checkbox.Group
          label="Days"
          value={autoForm.days}
          onChange={(days) => setAutoForm((prev) => ({ ...prev, days }))}
          mb="md"
        >
          <Group mt="xs">
            {DAY_NAMES.map((name, index) => (
              <Checkbox key={name} value={String(index)} label={name} />
            ))}
          </Group>
        </Checkbox.Group>
        {autoParts.length ? (
          <Text size="sm" c="dimmed" mb="md">
            {autoParts.join(" ")}
          </Text>
        ) : null}
        <Group justify="flex-end">
          <Button type="submit" loading={savingAuto}>
            Save
          </Button>
        </Group>
      </Card>

      {status ? (
        <Card withBorder padding="lg" radius="md">
          <Group justify="space-between" mb="sm">
            <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em", margin: 0 }}>
              Last update
            </Title>
            {statusBadge ? (
              <Badge color={statusBadge.color} variant="light">
                {statusBadge.label}
              </Badge>
            ) : (
              <Badge variant="light">{status.state}</Badge>
            )}
          </Group>
          <Text size="sm" c="dimmed" mb="sm">
            {status.message || statusDefaults[status.state] || ""}
          </Text>
          <Text
            component="pre"
            ref={logRef}
            ff="monospace"
            size="xs"
            p="md"
            m={0}
            style={{
              maxHeight: "40vh",
              overflow: "auto",
              background: "var(--mantine-color-default)",
              border: "1px solid var(--mantine-color-default-border)",
              borderRadius: "var(--mantine-radius-md)",
              whiteSpace: "pre-wrap",
            }}
          >
            {logLines.join("\n")}
          </Text>
        </Card>
      ) : null}
    </Page>
  );
}
