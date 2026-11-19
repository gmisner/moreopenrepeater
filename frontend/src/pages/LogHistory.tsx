import { Alert, Badge, Button, Card, Group, Select, Stack, Text, TextInput } from "@mantine/core";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { InfoNote } from "../components/FieldHint";
import { LoadingBlock } from "../components/StateCards";
import { notifyError } from "../config";

type HistoryEvent = {
  time: string;
  severity: number;
  event: string;
  module: string;
  message: string | null;
  context: Record<string, unknown>;
  correlation_id?: string | null;
};

type EventsPage = { events: HistoryEvent[]; next_before: number | null };
type Gap = { time: string; kind: string; module: string; count: number; detail: string | null };

type Status = {
  configured: boolean;
  available: boolean;
  reason?: string;
  stats?: {
    events: number;
    errors: number;
    oldest: string | null;
    newest: string | null;
    modules: Record<string, unknown>;
    retention: { routine_days: number; critical_days: number };
  };
};

const SEVERITY_NAMES: Record<number, string> = { 10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL" };
const SEVERITY_COLOR: Record<number, string> = { 10: "gray", 20: "gray", 30: "amber", 40: "red", 50: "red" };

const GAP_WORDS: Record<string, string> = {
  sequence_gap: "events lost on the way",
  restart: "restarted",
  reported_drop: "dropped events before sending",
  collector_overflow: "the collector was overloaded",
  quota_pruned: "oldest events removed to stay under the size limit",
};

const SPANS = [
  { value: "3600", label: "Last hour" },
  { value: "86400", label: "Last 24 hours" },
  { value: "604800", label: "Last 7 days" },
  { value: "all", label: "All time" },
];

const PAGE = 100;

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function facts(context: Record<string, unknown>): string {
  return Object.entries(context)
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join(" ");
}

function offsetFrom(start: string, iso: string): string {
  const seconds = (new Date(iso).getTime() - new Date(start).getTime()) / 1000;
  return Number.isFinite(seconds) ? `+${seconds.toFixed(1)} s` : "";
}

function EventRow({ event, first, offsetStart, onShowActivity }: { event: HistoryEvent; first: boolean; offsetStart?: string; onShowActivity?: (id: string) => void }) {
  const detail = facts(event.context);
  return (
    <div role="listitem" style={{ padding: "6px 0", borderTop: first ? undefined : "1px solid var(--mantine-color-default-border)" }}>
      <Group gap="xs" wrap="wrap" align="baseline">
        <Text size="xs" c="dimmed" title={event.time}>
          {offsetStart ? offsetFrom(offsetStart, event.time) : when(event.time)}
        </Text>
        <Badge size="sm" variant="light" color={SEVERITY_COLOR[event.severity] ?? "gray"}>
          {SEVERITY_NAMES[event.severity] ?? event.severity}
        </Badge>
        <Text size="xs" c="dimmed">
          {event.module}
        </Text>
        <Text size="xs" ff="monospace">
          {event.event}
        </Text>
        {onShowActivity && event.correlation_id ? (
          <Button size="compact-xs" variant="subtle" onClick={() => onShowActivity(event.correlation_id as string)}>
            Whole activity
          </Button>
        ) : null}
      </Group>
      {event.message ? <Text size="sm">{event.message}</Text> : null}
      {detail ? (
        <Text size="xs" c="dimmed" ff="monospace" style={{ wordBreak: "break-word" }}>
          {detail}
        </Text>
      ) : null}
    </div>
  );
}

/** Searchable event history from the log collector. Loads when asked, never on a timer. */
export function LogHistory() {
  const [status, setStatus] = useState<Status | null>(null);
  const [text, setText] = useState("");
  const [level, setLevel] = useState("");
  const [module, setModule] = useState("");
  const [span, setSpan] = useState("86400");
  const [events, setEvents] = useState<HistoryEvent[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);
  const [activity, setActivity] = useState<HistoryEvent[] | null>(null);

  const search = useCallback(
    async (before?: number) => {
      setBusy(true);
      try {
        const params = new URLSearchParams({ limit: String(PAGE) });
        if (text.trim()) params.set("q", text.trim());
        if (level) params.set("min_severity", level);
        if (module) params.set("module", module);
        if (span !== "all") params.set("since", String(Math.floor(Date.now() / 1000) - Number(span)));
        if (before !== undefined) params.set("before", String(before));
        const page = await api<EventsPage>(`/api/log-history/events?${params}`);
        setEvents((previous) => (before === undefined ? page.events : [...previous, ...page.events]));
        setNext(page.next_before);
        setSearched(true);
      } catch (error) {
        notifyError(error);
      } finally {
        setBusy(false);
      }
    },
    [text, level, module, span],
  );

  const loadGaps = useCallback(async () => {
    try {
      setGaps((await api<{ gaps: Gap[] }>("/api/log-history/gaps?limit=20")).gaps);
    } catch {
      setGaps([]);
    }
  }, []);

  const showActivity = useCallback(async (id: string) => {
    setBusy(true);
    try {
      const page = await api<EventsPage>(`/api/log-history/events?${new URLSearchParams({ correlation_id: id, limit: "500" })}`);
      // The collector answers newest first; an activity reads top to bottom, in the order it happened.
      setActivity([...page.events].reverse());
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const loaded = await api<Status>("/api/log-history/status");
        if (cancelled) return;
        setStatus(loaded);
        if (loaded.available) {
          void search();
          void loadGaps();
        }
      } catch (error) {
        if (!cancelled) setStatus({ configured: true, available: false, reason: error instanceof Error ? error.message : "unknown error" });
      }
    })();
    return () => {
      cancelled = true;
    };
    // Once, on opening: later searches are the operator's to ask for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setActivity(null);
    void search();
  };

  if (!status) return <LoadingBlock label="Loading the event history" />;
  if (!status.available) {
    return (
      <EmptyState>
        The event history is not available{status.reason ? `: ${status.reason}` : ""}. The live log and the log file still
        work. The history comes from the log collector, which runs as its own container.
      </EmptyState>
    );
  }

  const stats = status.stats;
  const modules = Object.keys(stats?.modules ?? {}).sort();

  return (
    <Stack gap="md">
      {gaps.length ? (
        <Alert color="amber" variant="light" title="Some events are known to be missing" role="note">
          <Stack gap={2}>
            {gaps.slice(0, 5).map((gap, index) => (
              <Text key={`${gap.time}-${index}`} size="sm">
                {when(gap.time)}: {gap.module} {GAP_WORDS[gap.kind] ?? gap.kind}
                {gap.count ? ` (${gap.count})` : ""}
              </Text>
            ))}
            {gaps.length > 5 ? (
              <Text size="sm" c="dimmed">
                and {gaps.length - 5} more
              </Text>
            ) : null}
          </Stack>
        </Alert>
      ) : null}
      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <form onSubmit={submit}>
            <Group gap="sm" wrap="wrap" align="flex-end">
              <TextInput
                label="Search"
                placeholder="words in the message or details"
                value={text}
                onChange={(event) => setText(event.currentTarget.value)}
                style={{ flex: "1 1 14rem" }}
              />
              <Select
                label="Level"
                data={[
                  { value: "", label: "All levels" },
                  { value: "warning", label: "WARNING+" },
                  { value: "error", label: "ERROR+" },
                ]}
                value={level}
                onChange={(value) => setLevel(value ?? "")}
                allowDeselect={false}
                w={140}
              />
              <Select
                label="Part"
                data={[{ value: "", label: "All" }, ...modules.map((name) => ({ value: name, label: name }))]}
                value={module}
                onChange={(value) => setModule(value ?? "")}
                allowDeselect={false}
                w={120}
              />
              <Select label="When" data={SPANS} value={span} onChange={(value) => setSpan(value ?? "86400")} allowDeselect={false} w={160} />
              <Button type="submit" loading={busy}>
                Search
              </Button>
            </Group>
          </form>
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              {stats
                ? `Keeping ${stats.events.toLocaleString()} events${stats.oldest ? ` since ${when(stats.oldest)}` : ""}: ${stats.retention.routine_days} days of routine events, ${stats.retention.critical_days} days of warnings and errors.`
                : ""}
            </Text>
            <InfoNote label="About this history">
              The log collector keeps events for a while after the live log scrolls past them, so you can look back at what
              happened overnight. Passwords, PINs and message content are never recorded. Search matches whole words. "Whole activity" shows everything that happened during one transmission or call, in order.
            </InfoNote>
          </Group>
          {activity ? (
            <Stack gap="xs">
              <Group justify="space-between" wrap="wrap">
                <Text size="sm" fw={500}>
                  Everything from one activity ({activity.length} {activity.length === 1 ? "event" : "events"}
                  {activity.length > 1 ? `, ${offsetFrom(activity[0].time, activity[activity.length - 1].time).slice(1)}` : ""})
                </Text>
                <Button variant="default" size="xs" onClick={() => setActivity(null)}>
                  Back to results
                </Button>
              </Group>
              <Stack gap={0} role="list" aria-label="One activity, in order">
                {activity.map((event, index) => (
                  <EventRow key={`${event.time}-${index}`} event={event} first={index === 0} offsetStart={activity[0].time} />
                ))}
              </Stack>
            </Stack>
          ) : events.length === 0 ? (
            <EmptyState>{searched ? "No events match." : "Searching..."}</EmptyState>
          ) : (
            <Stack gap={0} role="list" aria-label="Events, newest first">
              {events.map((event, index) => (
                <EventRow key={`${event.time}-${index}`} event={event} first={index === 0} onShowActivity={(id) => void showActivity(id)} />
              ))}
            </Stack>
          )}
          {next !== null && !activity ? (
            <Button variant="default" loading={busy} onClick={() => void search(next)}>
              Show older
            </Button>
          ) : null}
        </Stack>
      </Card>
    </Stack>
  );
}
