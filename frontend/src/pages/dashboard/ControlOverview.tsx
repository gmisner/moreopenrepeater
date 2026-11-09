import { Badge, Card, Group, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import type { RepeaterStatus } from "../../status";
import type { Transmission } from "./activity";
import { formatAirtime } from "./activity";

type Props = {
  status: RepeaterStatus | null;
  connected: boolean;
  links: number | null;
  transmissions: Transmission[];
};

function Metric({ label, value, tone }: { label: string; value: string; tone?: "rx" | "tx" }) {
  return <Card withBorder className="mor-overview-metric" data-tone={tone}>
    <Text size="xs" c="dimmed" fw={700} tt="uppercase">{label}</Text>
    <Text fw={800} className="mor-overview-value">{value}</Text>
  </Card>;
}

/** Only charts fetched transmissions, never fabricated 24h traffic. */
export function ControlOverview({ status, connected, links, transmissions }: Props) {
  const rows = transmissions.filter((row) => Number.isFinite(row.duration) && row.duration >= 0);
  const total = rows.reduce((sum, row) => sum + row.duration, 0);
  const max = Math.max(1, ...rows.map((row) => row.duration));
  const ptt = Boolean(status?.ptt_active);
  const cos = Boolean(status?.cos_active);
  const state = !connected ? "Disconnected" : !status ? "Waiting for status" : status.locked_out ? "Locked out" : ptt ? "Transmitting" : cos ? "Receiving" : "Standby";
  return (
    <Stack gap="md" className="mor-control-overview">
      <section className="mor-overview-hero" aria-label="Repeater overview">
        <Group justify="space-between" align="center" wrap="wrap" gap="md">
          <div>
            <Text className="mor-overview-eyebrow">MOR CONTROL CENTER</Text>
            <Title order={2} className="mor-overview-heading">Repeater overview</Title>
            <Text size="sm" c="dimmed">Live controller status and recent radio activity</Text>
          </div>
          <Badge color={!connected ? "orange" : status?.locked_out ? "red" : "green"} size="lg" variant="light">
            {state}
          </Badge>
        </Group>
      </section>
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Metric label="Receiver" value={!connected ? "Unknown" : cos ? "Carrier" : "Quiet"} tone={connected && cos ? "rx" : undefined} />
        <Metric label="Transmitter" value={!connected ? "Unknown" : ptt ? "Keyed" : "Off"} tone={connected && ptt ? "tx" : undefined} />
        <Metric label="Linked nodes" value={links === null ? "Unavailable" : String(links)} />
        <Metric label="Recent airtime" value={rows.length ? formatAirtime(total) : "No samples"} />
      </SimpleGrid>
      <Card withBorder radius="lg" padding="lg" component="section" aria-labelledby="mor-airtime-title" className="mor-airtime-card">
        <Group justify="space-between" mb="md" align="center">
          <div>
            <Title order={3} id="mor-airtime-title" size="md">Recent RF activity</Title>
            <Text size="xs" c="dimmed">Durations of the latest {rows.length} recorded transmissions (up to 20)</Text>
          </div>
          <Text component={Link} to="/activity" size="sm">View activity</Text>
        </Group>
        {rows.length ? (
          <div className="mor-airtime-chart" role="img" aria-label={`Durations of ${rows.length} recent transmissions, longest ${Math.round(max)} seconds`}>
            {rows.slice().reverse().map((row, index) => (
              <span key={`${row.started_at}-${index}`} className="mor-airtime-bar" data-timeout={row.timed_out || undefined}
                style={{ height: `${Math.max(4, row.duration / max * 100)}%` }}
                title={`${new Date(row.started_at).toLocaleString()}: ${row.duration.toFixed(1)} seconds${row.timed_out ? " (timeout)" : ""}`} />
            ))}
          </div>
        ) : <Text c="dimmed" size="sm">No transmission samples available yet.</Text>}
        <Text size="xs" c="dimmed" mt="sm">Showing recorded airtime, not a continuous 24-hour RF measurement.</Text>
      </Card>
    </Stack>
  );
}
