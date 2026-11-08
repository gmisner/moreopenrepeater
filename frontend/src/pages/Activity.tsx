import { Button, Card, Group, SegmentedControl, SimpleGrid, Text } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { BarChart, type Bar } from "../components/BarChart";
import { notifyError } from "../config";
import { formatAirtime } from "../format";
import { Page } from "../layout/AppShell";
import { RecentActivity, type Transmission } from "./dashboard/RecentActivity";

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

const RANGES = [
  { label: "24 hours", value: "1", phrase: "in the last 24 hours" },
  { label: "7 days", value: "7", phrase: "in the last 7 days" },
  { label: "30 days", value: "30", phrase: "in the last 30 days" },
  { label: "90 days", value: "90", phrase: "in the last 90 days" },
];

function hourLabel(hour: number) {
  const h = hour % 12 === 0 ? 12 : hour % 12;
  return `${h} ${hour < 12 ? "AM" : "PM"}`;
}

function dayText(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function Activity() {
  const [days, setDays] = useState("7");
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [recent, setRecent] = useState<Transmission[]>([]);

  const loadActivity = useCallback(async () => {
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
    void loadActivity();
    const timer = window.setInterval(() => {
      if (!document.hidden) void loadActivity();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [loadActivity]);

  const s = summary;
  const range = RANGES.find((r) => r.value === days) ?? RANGES[1];
  const kerchunkNote =
    s == null
      ? ""
      : `${s.rx_count ? `${Math.round((100 * s.kerchunks) / s.rx_count)}% of transmissions` : "Under 1.5 s"}${
          s.kerchunks_filtered ? `, ${s.kerchunks_filtered} more filtered out` : ""
        }`;

  const hourBars: Bar[] = (s?.by_hour ?? []).map((value, hour) => ({
    key: String(hour),
    label: hourLabel(hour),
    value,
  }));
  const dayBars: Bar[] = (s?.by_day ?? []).map((row) => ({
    key: row.date,
    label: dayText(row.date),
    value: row.rx_seconds,
  }));
  const dayStep = Math.max(1, Math.ceil(dayBars.length / 6));

  return (
    <Page title="Activity">
      <Group justify="space-between" wrap="wrap">
        <SegmentedControl
          value={days}
          onChange={setDays}
          aria-label="Time range"
          data={RANGES.map(({ label, value }) => ({ label, value }))}
        />
        <Button component={Link} to="/recordings" variant="default" size="sm">
          Open recordings
        </Button>
      </Group>

      {s ? (
        <>
          <Card withBorder padding="lg" radius="md" component="section" aria-label="Summary">
            <div className="mor-sum">
              <div>
                <Text size="sm" c="dimmed">
                  User airtime
                </Text>
                <span className="mor-sum-hero-value mor-num">{formatAirtime(s.rx_seconds)}</span>
                <Text size="sm" c="dimmed">
                  {s.rx_count} {s.rx_count === 1 ? "transmission" : "transmissions"} {range.phrase}
                </Text>
              </div>
              <dl className="mor-sum-list">
                <div>
                  <dt>Transmitter on</dt>
                  <dd className="mor-num">
                    {formatAirtime(s.tx_seconds)}
                    <small>Includes tails, IDs and announcements</small>
                  </dd>
                </div>
                <div>
                  <dt>Longest</dt>
                  <dd className="mor-num">
                    {formatAirtime(s.longest_rx_seconds)}
                    <small>Single transmission</small>
                  </dd>
                </div>
                <div>
                  <dt>Kerchunks</dt>
                  <dd className="mor-num">
                    {s.kerchunks}
                    <small>{kerchunkNote}</small>
                  </dd>
                </div>
                <div>
                  <dt>Time-outs</dt>
                  <dd className="mor-num">
                    {s.timeouts}
                    <small>{s.lockouts ? `${s.lockouts} stuck-carrier lockouts` : "Time-out timer tripped"}</small>
                  </dd>
                </div>
                <div>
                  <dt>IDs and announcements</dt>
                  <dd className="mor-num">
                    {s.ids} and {s.announcements}
                  </dd>
                </div>
              </dl>
            </div>
          </Card>

          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
            <BarChart
              title="Airtime by hour"
              hint="When people use the repeater, by hour of day on the controller's clock."
              bars={hourBars}
              format={formatAirtime}
              tickLabel={(_, i) => (i % 6 === 0 ? hourLabel(i) : null)}
              emptyText="No user airtime in this range."
            />
            <BarChart
              title="Airtime by day"
              hint="User airtime on each day."
              bars={dayBars}
              format={formatAirtime}
              tickLabel={(bar, i) => (i % dayStep === 0 ? bar.label : null)}
              emptyText="No user airtime in this range."
            />
          </SimpleGrid>
        </>
      ) : null}

      <RecentActivity rows={recent} showLink={false} />
    </Page>
  );
}
