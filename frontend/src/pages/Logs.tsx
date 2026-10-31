import { Anchor, Card, Group, Select, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const POLL_INTERVAL_MS = 3000;
const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LEVEL_PATTERN = /\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b/;

const LEVEL_COLOR: Record<string, string> = {
  debug: "dimmed",
  info: "var(--mantine-color-text)",
  warning: "var(--mantine-color-amber-4)",
  error: "var(--mantine-color-red-4)",
  critical: "var(--mantine-color-red-6)",
};

export function Logs() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const [lines, setLines] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [minLevel, setMinLevel] = useState("ALL");
  const [lineCount, setLineCount] = useState("200");
  const [paused, setPaused] = useState(false);
  const viewRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (paused) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const next = await api<string[]>(`/api/logs?lines=${lineCount}`);
        if (!cancelled) setLines(next);
      } catch {
        // Transient failure; the next poll retries.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [lineCount, paused]);

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

  return (
    <Page title="Logs" description="Tail of the controller log file, refreshed every few seconds.">
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
            <Select
              data={[
                { value: "200", label: "200 lines" },
                { value: "500", label: "500 lines" },
                { value: "1000", label: "1000 lines" },
              ]}
              value={lineCount}
              onChange={(value) => setLineCount(value || "200")}
              w={140}
              allowDeselect={false}
            />
            <Switch label="Pause" checked={paused} onChange={(event) => setPaused(event.currentTarget.checked)} />
            {isAdmin ? (
              <Anchor href="/api/support-bundle" download size="sm">
                Download support bundle
              </Anchor>
            ) : null}
          </Group>
          {isAdmin ? (
            <Text size="sm" c="dimmed" maw="75ch">
              A zip of recent logs, radio/audio settings, sound and serial devices, mixer levels, receiver openings,
              glitches and status for troubleshooting. No passwords, env file, users, tokens or recordings.
            </Text>
          ) : null}
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
    </Page>
  );
}
