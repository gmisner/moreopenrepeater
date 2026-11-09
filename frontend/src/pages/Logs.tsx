import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { labeled, InfoNote } from "../components/FieldHint";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LEVEL_PATTERN = /\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b/;
const MAX_LIVE_LINES = 1000;

const LEVEL_COLOR: Record<string, string> = {
  debug: "dimmed",
  info: "var(--mantine-color-text)",
  warning: "var(--mor-amber)",
  error: "var(--mor-red)",
  critical: "var(--mor-red)",
};

type ShippingSettings = {
  enabled: boolean;
  min_level: string;
  batch_seconds: number;
  batch_lines: number;
};

type ShippingInfo = {
  settings: ShippingSettings;
  secrets_set: Record<string, boolean>;
  active: boolean;
  last_error: string;
  last_sent_at: string | null;
  lines_sent: number;
};

type ShippingForm = ShippingSettings & { url: string; token: string };

const EMPTY_SHIPPING: ShippingForm = {
  enabled: false,
  url: "",
  token: "",
  min_level: "INFO",
  batch_seconds: 2,
  batch_lines: 50,
};

function shippingFromInfo(info: ShippingInfo): ShippingForm {
  return {
    ...EMPTY_SHIPPING,
    ...info.settings,
    url: "",
    token: "",
  };
}

export function Logs() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const [lines, setLines] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [minLevel, setMinLevel] = useState("ALL");
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const viewRef = useRef<HTMLPreElement>(null);
  const lastSeqRef = useRef(0);
  const [shipping, setShipping] = useState<ShippingInfo | null>(null);
  const [shippingForm, setShippingForm] = useState<ShippingForm>(EMPTY_SHIPPING);
  const [shippingDirty, setShippingDirty] = useState(false);
  const [shippingBusy, setShippingBusy] = useState(false);
  const [cleared, setCleared] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (paused) return;
    let cancelled = false;
    let source: EventSource | null = null;

    (async () => {
      try {
        const seed = await api<{ lines: { seq: number; line: string }[]; after: number }>("/api/logs/since?lines=200");
        if (cancelled) return;
        lastSeqRef.current = seed.after;
        if (seed.lines.length) {
          setLines(seed.lines.map((entry) => entry.line));
        } else {
          setLines(await api<string[]>("/api/logs?lines=200"));
        }
      } catch {
        try {
          if (!cancelled) setLines(await api<string[]>("/api/logs?lines=200"));
        } catch {
          // Stream may still connect.
        }
      }
      if (cancelled) return;
      source = new EventSource(`/api/logs/stream?after=${lastSeqRef.current}`);
      source.onopen = () => setConnected(true);
      source.onerror = () => setConnected(false);
      source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as { seq: number; line: string };
          if (payload.seq <= lastSeqRef.current) return;
          lastSeqRef.current = payload.seq;
          setLines((prev) => {
            const next = [...prev, payload.line];
            return next.length > MAX_LIVE_LINES ? next.slice(-MAX_LIVE_LINES) : next;
          });
        } catch {
          // Ignore malformed events.
        }
      };
    })();

    return () => {
      cancelled = true;
      source?.close();
      setConnected(false);
    };
  }, [paused]);

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    (async () => {
      try {
        const info = await api<ShippingInfo>("/api/log-shipping");
        if (cancelled) return;
        setShipping(info);
        setShippingForm(shippingFromInfo(info));
        setShippingDirty(false);
        setCleared(new Set());
      } catch (error) {
        notifyError(error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const minIdx = LEVELS.indexOf(minLevel === "ALL" ? "" : minLevel);
    return lines.filter((line) => {
      if (needle && !line.toLowerCase().includes(needle)) return false;
      if (minIdx > 0) {
        const level = line.match(LEVEL_PATTERN)?.[1];
        if (level && LEVELS.indexOf(level) < minIdx) return false;
      }
      return true;
    });
  }, [lines, filter, minLevel]);

  useEffect(() => {
    const el = viewRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 20;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [visible]);

  const setShippingField = <K extends keyof ShippingForm>(key: K, value: ShippingForm[K]) => {
    setShippingForm((prev) => ({ ...prev, [key]: value }));
    setShippingDirty(true);
    if (key === "url" || key === "token") {
      setCleared((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const clearSecret = (key: "url" | "token") => {
    setShippingForm((prev) => ({ ...prev, [key]: "" }));
    setCleared((prev) => new Set(prev).add(key));
    setShippingDirty(true);
  };

  const saveShipping = async (event: FormEvent) => {
    event.preventDefault();
    setShippingBusy(true);
    try {
      const body: Record<string, unknown> = {
        enabled: shippingForm.enabled,
        min_level: shippingForm.min_level,
        batch_seconds: shippingForm.batch_seconds,
        batch_lines: shippingForm.batch_lines,
      };
      if (shippingForm.url.trim()) body.url = shippingForm.url.trim();
      if (cleared.has("url")) body.url = "";
      if (shippingForm.token.trim()) body.token = shippingForm.token.trim();
      if (cleared.has("token")) body.token = "";
      const info = await api<ShippingInfo>("/api/log-shipping", { method: "PUT", json: body });
      setShipping(info);
      setShippingForm(shippingFromInfo(info));
      setShippingDirty(false);
      setCleared(new Set());
      notifySuccess("Remote logging saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setShippingBusy(false);
    }
  };

  const testShipping = async () => {
    setShippingBusy(true);
    try {
      const result = await api<{ error: string | null }>("/api/log-shipping/test", { method: "POST" });
      if (result.error) notifyError(result.error);
      else notifySuccess("Test batch sent");
      setShipping(await api<ShippingInfo>("/api/log-shipping"));
    } catch (error) {
      notifyError(error);
    } finally {
      setShippingBusy(false);
    }
  };

  const secretPlaceholder = (key: "url" | "token") =>
    shipping?.secrets_set[key] && !cleared.has(key) ? "saved — type to replace" : undefined;

  return (
    <Page title="Logs" description="Review controller events and troubleshoot system activity.">
      <Stack gap="lg">
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Group gap="sm" wrap="wrap">
              <TextInput
                placeholder="Filter…"
                value={filter}
                onChange={(event) => setFilter(event.currentTarget.value)}
                style={{ flex: "1 1 12rem" }}
              />
              <Select
                aria-label="Minimum log level"
                data={[
                  { value: "ALL", label: "All levels" },
                  { value: "INFO", label: "INFO+" },
                  { value: "WARNING", label: "WARNING+" },
                  { value: "ERROR", label: "ERROR+" },
                ]}
                value={minLevel}
                onChange={(value) => setMinLevel(value || "ALL")}
                w={140}
                allowDeselect={false}
              />
              <Switch label="Pause" checked={paused} onChange={(event) => setPaused(event.currentTarget.checked)} />
              <Badge variant="light" color={paused ? "amber" : connected ? "green" : "gray"}>
                {paused ? "paused" : connected ? "live" : "connecting"}
              </Badge>
              {isAdmin ? (
                <Group gap="xs" wrap="nowrap" ml="auto">
                  <Button component="a" href="/api/support-bundle" download variant="default">
                    Download support bundle
                  </Button>
                  <InfoNote label="What's in it">
                    A zip of recent logs, radio/audio settings, sound and serial devices, mixer levels, receiver
                    openings, glitches and status for troubleshooting. No passwords, env file, users, tokens or
                    recordings.
                  </InfoNote>
                </Group>
              ) : null}
            </Group>
            <Text
              component="pre"
              ref={viewRef}
              ff="monospace"
              size="xs"
              p="md"
              m={0}
              style={{
                maxHeight: "70vh",
                overflow: "auto",
                background: "var(--mantine-color-default)",
                border: "1px solid var(--mantine-color-default-border)",
                borderRadius: "var(--mantine-radius-md)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {visible.length === 0 ? (
                <Text span c="dimmed">
                  No log lines match.
                </Text>
              ) : (
                visible.map((line, index) => {
                  const level = line.match(LEVEL_PATTERN)?.[1]?.toLowerCase() ?? "";
                  const color = LEVEL_COLOR[level];
                  return (
                    <Text
                      key={`${index}-${line.slice(0, 24)}`}
                      span
                      display="block"
                      c={color === "dimmed" ? "dimmed" : undefined}
                      style={color && color !== "dimmed" ? { color } : undefined}
                    >
                      {line}
                    </Text>
                  );
                })
              )}
            </Text>
          </Stack>
        </Card>

        {isAdmin ? (
          <Card withBorder padding="lg" radius="md" component="form" onSubmit={saveShipping}>
            <Stack gap="md">
              <div>
                <Text fw={600}>Remote logging</Text>
                <InfoNote>
                  Ship live controller log lines to an HTTPS ingest URL someone helping you provides (Axiom edge:
                  https://us-east-1.aws.edge.axiom.co/v1/ingest/… plus an xaat- API token). Off by default. The URL and
                  token are stored only on this Pi — not in backups.
                </InfoNote>
              </div>
              <Switch
                label="Ship logs to a helper"
                checked={shippingForm.enabled}
                onChange={(event) => setShippingField("enabled", event.currentTarget.checked)}
              />
              <TextInput
                label={labeled("Ingest URL", "HTTPS endpoint that accepts a JSON POST of log batches.")}
                placeholder={secretPlaceholder("url") || "https://…"}
                value={shippingForm.url}
                onChange={(event) => setShippingField("url", event.currentTarget.value)}
              />
              {shipping?.secrets_set.url && !cleared.has("url") ? (
                <Button type="button" variant="subtle" size="compact-xs" onClick={() => clearSecret("url")}>
                  Clear saved URL
                </Button>
              ) : null}
              <PasswordInput
                label={labeled("Bearer token", "Optional. Sent as Authorization: Bearer …")}
                placeholder={secretPlaceholder("token") || "optional"}
                value={shippingForm.token}
                onChange={(event) => setShippingField("token", event.currentTarget.value)}
              />
              {shipping?.secrets_set.token && !cleared.has("token") ? (
                <Button type="button" variant="subtle" size="compact-xs" onClick={() => clearSecret("token")}>
                  Clear saved token
                </Button>
              ) : null}
              <Group grow align="flex-start">
                <Select
                  label="Minimum level"
                  data={LEVELS.map((value) => ({ value, label: value }))}
                  value={shippingForm.min_level}
                  onChange={(value) => setShippingField("min_level", value || "INFO")}
                  allowDeselect={false}
                />
                <NumberInput
                  label="Batch seconds"
                  min={0.5}
                  max={60}
                  step={0.5}
                  decimalScale={1}
                  value={shippingForm.batch_seconds}
                  onChange={(value) => setShippingField("batch_seconds", typeof value === "number" ? value : 2)}
                />
                <NumberInput
                  label="Batch lines"
                  min={1}
                  max={500}
                  value={shippingForm.batch_lines}
                  onChange={(value) => setShippingField("batch_lines", typeof value === "number" ? value : 50)}
                />
              </Group>
              {shipping ? (
                <Text size="sm" c="dimmed">
                  {shipping.active ? "Shipping is on." : "Shipping is off."}
                  {shipping.lines_sent ? ` Sent ${shipping.lines_sent} lines.` : ""}
                  {shipping.last_error ? ` Last error: ${shipping.last_error}` : ""}
                </Text>
              ) : null}
              <Group justify="space-between" mt="lg" wrap="wrap">
                <Button
                  type="button"
                  variant="default"
                  disabled={shippingBusy || !shipping?.secrets_set.url}
                  onClick={() => void testShipping()}
                >
                  Send test
                </Button>
                <ConfigActions
                  dirty={shippingDirty}
                  saving={shippingBusy}
                  onReset={() => {
                    if (shipping) setShippingForm(shippingFromInfo(shipping));
                    setShippingDirty(false);
                    setCleared(new Set());
                  }}
                />
              </Group>
            </Stack>
          </Card>
        ) : null}
      </Stack>
    </Page>
  );
}
