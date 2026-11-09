import { Badge, Card, Group, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import type { RepeaterStatus } from "../../status";
import { formatAirtime } from "./activity";

export type History24h = { since: string; until: string; bins: { started_at: string; rx_seconds: number; tx_seconds: number; rx_count: number }[] };
\ntype Props = {
  status: RepeaterStatus | null;
  connected: boolean;
  links: number | null;
  history: History24h | null;
};

function Metric({ label, value, tone }: { label: string; value: string; tone?: "rx" | "tx" }) {
  return <Card withBorder className="mor-overview-metric" data-tone={tone}>
    <Text size="xs" c="dimmed" fw={700} tt="uppercase">{label}</Text>
    <Text fw={800} className="mor-overview-value">{value}</Text>
  </Card>;
}

/** Only charts fetched transmissions, never fabricated 24h traffic. */
export function ControlOverview({ status, connected, links, transmissions, history }: Props) {
  const bins = history?.bins ?? [];
  const max = Math.max(1, ...bins.flatMap((bin) => [bin.rx_seconds, bin.tx_seconds]));
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
        <Metric label="24h RX airtime" value={history ? formatAirtime(bins.reduce((sum, bin) => sum + bin.rx_seconds, 0)) : "Unavailable"} />
      </SimpleGrid>
      <Card withBorder radius="lg" padding="lg" component="section" aria-labelledby="mor-airtime-title" className="mor-airtime-card">
        <Group justify="space-between" mb="md" align="center">
          <div>
            <Title order={3} id="mor-airtime-title" size="md">24-hour RF activity</Title>
            <Text size="xs" c="dimmed">Rolling 24 hours · recorded RX and TX airtime per hour</Text>
          </div>
          <Text component={Link} to="/activity" size="sm">View activity</Text>
        </Group>
        {history && bins.length === 24 ? (
          <>
            <Group gap="md" mb="sm">
              <Text size="xs"><span className="mor-history-key mor-history-rx" /> RX airtime</Text>
              <Text size="xs"><span className="mor-history-key mor-history-tx" /> TX airtime</Text>
            </Group>
            <div className="mor-history-chart" role="img" aria-label="Hourly recorded receiver and transmitter airtime for the last 24 hours">
              {bins.map((bin, index) => (
                <div className="mor-history-hour" key={bin.started_at}
                  title={`${new Date(bin.started_at).toLocaleString()}: RX ${formatAirtime(bin.rx_seconds)}, TX ${formatAirtime(bin.tx_seconds)}`}>
                  <div className="mor-history-pair">
                    <span className="mor-history-rx" style={{ height: `${bin.rx_seconds > 0 ? Math.max(2, bin.rx_seconds / max * 100) : 0}%` }} />
                    <span className="mor-history-tx" style={{ height: `${bin.tx_seconds > 0 ? Math.max(2, bin.tx_seconds / max * 100) : 0}%` }} />
                  </div>
                  {index % 4 === 0 ? <Text size="xs" c="dimmed">{new Date(bin.started_at).toLocaleTimeString([], { hour: "numeric" })}</Text> : <span aria-hidden="true" />}
                </div>
              ))}
            </div>
          </>
        ) : <Text c="dimmed" size="sm">24-hour history is not available yet.</Text>}
        <Text size="xs" c="dimmed" mt="sm">Historical RX/TX airtime is recorded on completed events; it is not a continuous signal-strength measurement.</Text>
      </Card>
    </Stack>
  );
}
