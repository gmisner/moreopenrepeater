import { Badge, Card, Group, NumberInput, SegmentedControl, SimpleGrid, Stack, Switch, Table, Text } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { labeled } from "../components/FieldHint";

type UsageSummary = {
  rx_seconds: number;
  tx_seconds: number;
  rx_count: number;
  longest_rx_seconds: number;
  kerchunks: number;
  kerchunks_filtered: number;
  timeouts: number;
  lockouts: number;
  ids: number;
  announcements: number;
  by_hour: number[];
  by_day: { date: string; rx_seconds: number; tx_seconds: number; rx_count?: number }[];
};

type Transmission = {
  started_at: string;
  duration: number;
  timed_out: boolean;
};

type RecValues = { record_transmissions: boolean; recording_retention_days: number };

function formatAirtime(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Card withBorder padding="md" radius="md">
      <Text size="xs" c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
        {label}
      </Text>
      <Text fw={700} style={{ fontSize: "1.5rem", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Text>
      {note ? (
        <Text size="xs" c="dimmed">
          {note}
        </Text>
      ) : null}
    </Card>
  );
}

export function Activity() {
  const [days, setDays] = useState("7");
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [recent, setRecent] = useState<Transmission[]>([]);
  const rec = useConfigSlice<RecValues>(["record_transmissions", "recording_retention_days"], {
    record_transmissions: false,
    recording_retention_days: 30,
  });

  const load = useCallback(async () => {
    try {
      const [nextSummary, nextRecent] = await Promise.all([
        api<UsageSummary>(`/api/activity/summary?days=${days}`),
        api<Transmission[]>("/api/activity/transmissions?limit=25"),
      ]);
      setSummary(nextSummary);
      setRecent(nextRecent);
    } catch (error) {
      notifyError(error);
    }
  }, [days]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const s = summary;
  const kerchunkNote =
    s == null
      ? ""
      : `under 1.5 s${s.rx_count ? ` · ${Math.round((100 * s.kerchunks) / s.rx_count)}%` : ""}${
          s.kerchunks_filtered ? ` · ${s.kerchunks_filtered} more filtered out` : ""
        }`;

  return (
    <Page title="Activity" description="How much the repeater gets used. Recorded automatically and kept for about a year.">
      <Group justify="flex-end">
        <SegmentedControl
          value={days}
          onChange={setDays}
          data={[
            { label: "24 h", value: "1" },
            { label: "7 days", value: "7" },
            { label: "30 days", value: "30" },
            { label: "90 days", value: "90" },
          ]}
        />
      </Group>

      {s ? (
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm">
          <Stat label="User airtime" value={formatAirtime(s.rx_seconds)} note={`${s.rx_count} transmissions`} />
          <Stat label="Transmitter on" value={formatAirtime(s.tx_seconds)} note="incl. tails, IDs, announcements" />
          <Stat label="Longest" value={formatAirtime(s.longest_rx_seconds)} note="single transmission" />
          <Stat label="Kerchunks" value={String(s.kerchunks)} note={kerchunkNote} />
          <Stat label="Timeouts" value={String(s.timeouts)} note={s.lockouts ? `${s.lockouts} stuck-carrier lockouts` : "timeout timer tripped"} />
          <Stat label="IDs / announcements" value={`${s.ids} / ${s.announcements}`} />
        </SimpleGrid>
      ) : null}

      <Card withBorder padding="lg" radius="md">
        <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} mb="sm" style={{ letterSpacing: "0.06em" }}>
          Recent transmissions
        </Text>
        {recent.length ? (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>When</Table.Th>
                <Table.Th>Length</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {recent.map((row) => (
                <Table.Tr key={row.started_at}>
                  <Table.Td>{new Date(row.started_at).toLocaleString()}</Table.Td>
                  <Table.Td>{formatAirtime(row.duration)}</Table.Td>
                  <Table.Td>
                    {row.timed_out ? (
                      <Badge color="red" variant="light">
                        timed out
                      </Badge>
                    ) : row.duration < 1.5 ? (
                      <Badge variant="outline">kerchunk</Badge>
                    ) : null}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text c="dimmed" size="sm">
            No transmissions recorded yet. Try the Simulator.
          </Text>
        )}
      </Card>

      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void rec.submit();
        }}
      >
        <Stack gap="md">
          <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: "0.06em", margin: 0 }}>
            Recordings
          </Text>
          <Switch
            label={labeled("Record transmissions", "Saves what the repeater hears (needs live audio)")}
            checked={rec.values.record_transmissions}
            onChange={(event) => rec.setField("record_transmissions", event.currentTarget.checked)}
          />
          <NumberInput
            label="Keep recordings for"
            suffix=" days"
            min={1}
            max={365}
            maw={280}
            value={rec.values.recording_retention_days}
            onChange={(value) => rec.setField("recording_retention_days", Number(value) || 1)}
            required
          />
          <ConfigActions dirty={rec.dirty} saving={rec.saving} onReset={rec.reset} />
        </Stack>
      </Card>
    </Page>
  );
}
