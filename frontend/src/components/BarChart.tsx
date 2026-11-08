import { Card, Table, Text } from "@mantine/core";
import { useId, useState } from "react";

export type Bar = { key: string; label: string; value: number };

/**
 * One-series column chart for airtime. One hue, thin bars rounded at the top
 * and square on the baseline, a tooltip on hover and focus, and the same
 * numbers in a table underneath for anyone who can't hover.
 */
export function BarChart({
  title,
  hint,
  bars,
  format,
  tickLabel,
  emptyText,
}: {
  title: string;
  hint?: string;
  bars: Bar[];
  format: (value: number) => string;
  /** Label for the bar at this index, or null for no label. */
  tickLabel: (bar: Bar, index: number) => string | null;
  emptyText: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  const headingId = useId();
  const max = bars.reduce((m, b) => Math.max(m, b.value), 0);
  const n = bars.length;
  const hovered = active != null ? bars[active] : null;
  const edge = active == null ? "mid" : active < n * 0.15 ? "start" : active > n * 0.85 ? "end" : "mid";

  return (
    <Card withBorder padding="lg" radius="md" component="section" aria-labelledby={headingId}>
      <h2 id={headingId} className="mor-card-title">
        {title}
      </h2>
      {hint ? (
        <Text size="sm" c="dimmed" mb="md">
          {hint}
        </Text>
      ) : null}
      {max > 0 ? (
        <>
          <div className="mor-chart">
            <span className="mor-chart-max mor-num" aria-hidden="true">
              {format(max)}
            </span>
            <div className="mor-chart-plot" style={{ ["--n" as string]: n }} role="group" onPointerLeave={() => setActive(null)}>
              {bars.map((bar, i) => (
                <button
                  key={bar.key}
                  type="button"
                  className="mor-chart-cell"
                  aria-label={`${bar.label}: ${bar.value > 0 ? format(bar.value) : "no airtime"}`}
                  onPointerEnter={() => setActive(i)}
                  onFocus={() => setActive(i)}
                  onBlur={() => setActive(null)}
                >
                  {bar.value > 0 ? (
                    <span className="mor-chart-bar" style={{ height: `${Math.max(2, (bar.value / max) * 100)}%` }} />
                  ) : null}
                </button>
              ))}
              {hovered ? (
                <div
                  className="mor-chart-tip"
                  data-edge={edge}
                  style={{ left: `${(((active as number) + 0.5) / n) * 100}%` }}
                  aria-hidden="true"
                >
                  <strong className="mor-num">{hovered.value > 0 ? format(hovered.value) : "None"}</strong>
                  <span>{hovered.label}</span>
                </div>
              ) : null}
            </div>
            <div className="mor-chart-ticks" style={{ ["--n" as string]: n }} aria-hidden="true">
              {bars.map((bar, i) => {
                const text = tickLabel(bar, i);
                return <span key={bar.key}>{text}</span>;
              })}
            </div>
          </div>
          <details className="mor-chart-table">
            <summary>View as table</summary>
            <Table.ScrollContainer minWidth={240} mah={280}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{title}</Table.Th>
                    <Table.Th ta="right">Airtime</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {bars.map((bar) => (
                    <Table.Tr key={bar.key}>
                      <Table.Td>{bar.label}</Table.Td>
                      <Table.Td ta="right" className="mor-num">
                        {bar.value > 0 ? format(bar.value) : "–"}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </details>
        </>
      ) : (
        <Text c="dimmed" size="sm">
          {emptyText}
        </Text>
      )}
    </Card>
  );
}
