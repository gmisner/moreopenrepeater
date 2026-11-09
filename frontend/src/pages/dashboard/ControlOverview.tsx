import { Badge, Card, Group, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import type { Config } from "../../config";
import type { RepeaterStatus } from "../../status";
import { formatAirtime } from "./activity";

export type History24h = {
  since: string;
  until: string;
  bins: { started_at: string; rx_seconds: number; tx_seconds: number; rx_count: number }[];
};

type HealthItem = { label: string; value: string; ok: boolean };

type Props = {
  status: RepeaterStatus | null;
  connected: boolean;
  links: number | null;
  history: History24h | null;
  config: Config | null;
  linkRadioOnline: boolean | null;
  health: HealthItem[];
};

function num(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function mhz(value: number) {
  return `${value.toFixed(4)} MHz`;
}

function Kpi({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "rx" | "tx" | "ok";
}) {
  return (
    <Card withBorder className="mor-glass-card mor-kpi" data-tone={tone}>
      <Text size="xs" c="dimmed" fw={700} tt="uppercase" className="mor-kpi-label">
        {label}
      </Text>
      <Text fw={800} className="mor-kpi-value">
        {value}
      </Text>
      <Text size="xs" c="dimmed">
        {detail}
      </Text>
    </Card>
  );
}

/** Live controller overview styled to the MOR dashboard mockup; metrics stay real. */
export function ControlOverview({ status, connected, links, history, config, linkRadioOnline, health }: Props) {
  const bins = history?.bins ?? [];
  const max = Math.max(1, ...bins.flatMap((bin) => [bin.rx_seconds, bin.tx_seconds]));
  const ptt = Boolean(status?.ptt_active);
  const cos = Boolean(status?.cos_active);
  const online = connected && !status?.locked_out && status?.transmitter_enabled !== false;
  const stateLabel = !connected
    ? "Disconnected"
    : !status
      ? "Waiting"
      : status.locked_out
        ? "Locked out"
        : ptt
          ? "Transmitting"
          : cos
            ? "Receiving"
            : "Standby";

  const txFreq = num(config?.aprs_frequency_mhz);
  const offset = num(config?.aprs_offset_mhz);
  const rxFreq = txFreq != null && offset != null ? txFreq + offset : null;
  const rxAir = history ? bins.reduce((sum, bin) => sum + bin.rx_seconds, 0) : null;
  const txAir = history ? bins.reduce((sum, bin) => sum + bin.tx_seconds, 0) : null;

  return (
    <div className="mor-control-overview">
      <Card withBorder className="mor-glass-card mor-status-strip" component="section" aria-label="Repeater status">
        <Group justify="space-between" align="center" wrap="wrap" gap="md">
          <Group gap="sm" wrap="nowrap">
            <span className="mor-pulse" data-ok={online || undefined} aria-hidden="true" />
            <div>
              <Text fw={800}>{online ? "Repeater Online" : connected ? "Repeater Degraded" : "Repeater Offline"}</Text>
              <Text size="xs" c="dimmed">
                {stateLabel}
                {connected ? " · live status" : " · reconnecting"}
              </Text>
            </div>
          </Group>
          <div className="mor-status-metrics">
            <div className="mor-status-metric">
              <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
                TX Frequency
              </Text>
              <Text fw={700} className="mor-dash-num">
                {txFreq != null ? mhz(txFreq) : "Not set"}
              </Text>
            </div>
            <div className="mor-status-metric">
              <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
                RX Frequency
              </Text>
              <Text fw={700} className="mor-dash-num">
                {rxFreq != null ? mhz(rxFreq) : "Not set"}
              </Text>
            </div>
            <div className="mor-status-metric">
              <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
                Offset
              </Text>
              <Text fw={700} className="mor-dash-num">
                {offset != null ? `${offset > 0 ? "+" : ""}${offset.toFixed(4)} MHz` : "Not set"}
              </Text>
            </div>
          </div>
        </Group>
      </Card>

      <div className="mor-kpi-grid">
        <Kpi
          label="Receiving (RX)"
          value={!connected ? "Unknown" : cos ? "Receiving" : "Quiet"}
          detail={cos ? "Carrier detected" : "No carrier"}
          tone={connected && cos ? "rx" : undefined}
        />
        <Kpi
          label="Transmitter (TX)"
          value={!connected ? "Unknown" : ptt ? "Keyed" : "Standby"}
          detail={ptt ? "On the air" : "Not transmitting"}
          tone={connected && ptt ? "tx" : undefined}
        />
        <Kpi
          label="Linked Nodes"
          value={links === null ? "—" : String(links)}
          detail={links === null ? "Unavailable" : links === 1 ? "1 active" : `${links} active`}
          tone={links != null && links > 0 ? "ok" : undefined}
        />
        <Kpi label="24h RX airtime" value={rxAir == null ? "—" : formatAirtime(rxAir)} detail="Recorded receiver airtime" />
        <Kpi
          label="Linked radio"
          value={linkRadioOnline == null ? "—" : linkRadioOnline ? "Online" : "Off"}
          detail={linkRadioOnline ? "Audio path ready" : "Not running"}
          tone={linkRadioOnline ? "ok" : undefined}
        />
      </div>

      <div className="mor-overview-split">
        <Card
          withBorder
          radius="lg"
          padding="lg"
          component="section"
          aria-labelledby="mor-airtime-title"
          className="mor-glass-card mor-airtime-card"
        >
          <Group justify="space-between" mb="md" align="center">
            <div>
              <Title order={3} id="mor-airtime-title" size="md">
                RF Activity
              </Title>
              <Text size="xs" c="dimmed">
                Last 24 hours · recorded RX and TX airtime
              </Text>
            </div>
            <Text component={Link} to="/activity" size="sm">
              View activity
            </Text>
          </Group>
          {history && bins.length === 24 ? (
            <>
              <Group gap="md" mb="sm">
                <Text size="xs">
                  <span className="mor-history-key mor-history-rx" /> RX
                  {rxAir == null ? "" : ` · ${formatAirtime(rxAir)}`}
                </Text>
                <Text size="xs">
                  <span className="mor-history-key mor-history-tx" /> TX
                  {txAir == null ? "" : ` · ${formatAirtime(txAir)}`}
                </Text>
              </Group>
              <div
                className="mor-history-chart"
                role="img"
                aria-label="Hourly recorded receiver and transmitter airtime for the last 24 hours"
              >
                {bins.map((bin, index) => (
                  <div
                    className="mor-history-hour"
                    key={bin.started_at}
                    title={`${new Date(bin.started_at).toLocaleString()}: RX ${formatAirtime(bin.rx_seconds)}, TX ${formatAirtime(bin.tx_seconds)}`}
                  >
                    <div className="mor-history-pair">
                      <span
                        className="mor-history-rx"
                        style={{ height: `${bin.rx_seconds > 0 ? Math.max(2, (bin.rx_seconds / max) * 100) : 0}%` }}
                      />
                      <span
                        className="mor-history-tx"
                        style={{ height: `${bin.tx_seconds > 0 ? Math.max(2, (bin.tx_seconds / max) * 100) : 0}%` }}
                      />
                    </div>
                    {index % 4 === 0 ? (
                      <Text size="xs" c="dimmed">
                        {new Date(bin.started_at).toLocaleTimeString([], { hour: "numeric" })}
                      </Text>
                    ) : (
                      <span aria-hidden="true" />
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <Text c="dimmed" size="sm">
              24-hour history is not available yet.
            </Text>
          )}
          <Text size="xs" c="dimmed" mt="sm">
            Historical RX/TX airtime is recorded on completed events; it is not a continuous signal-strength measurement.
          </Text>
        </Card>

        <Card withBorder radius="lg" padding="lg" component="section" aria-labelledby="mor-health-title" className="mor-glass-card">
          <Group justify="space-between" mb="md">
            <Title order={3} id="mor-health-title" size="md">
              System Health
            </Title>
            <Badge color={health.every((item) => item.ok) ? "green" : "amber"} variant="light">
              {health.every((item) => item.ok) ? "Healthy" : "Attention"}
            </Badge>
          </Group>
          <ul className="mor-health-list">
            {health.map((item) => (
              <li key={item.label} data-ok={item.ok || undefined}>
                <span className="mor-health-mark" aria-hidden="true">
                  {item.ok ? "✓" : "!"}
                </span>
                <span className="mor-health-label">{item.label}</span>
                <span className="mor-health-value">{item.value}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="mor-quick-row">
        <Card component={Link} to="/simulator" withBorder className="mor-glass-card mor-quick-link">
          <Text fw={800}>Quick Actions</Text>
          <Text size="sm" c="dimmed">
            Drive the simulator without a radio attached
          </Text>
        </Card>
        <Card component={Link} to="/timing" withBorder className="mor-glass-card mor-quick-link">
          <Text fw={800}>Configuration</Text>
          <Text size="sm" c="dimmed">
            Timing, identification, audio and tones
          </Text>
        </Card>
        <Card component={Link} to="/activity" withBorder className="mor-glass-card mor-quick-link">
          <Text fw={800}>Activity & Logs</Text>
          <Text size="sm" c="dimmed">
            Airtime history and controller log stream
          </Text>
        </Card>
      </div>
    </div>
  );
}
