import { Button, Card, Group, Text } from "@mantine/core";
import { Fragment } from "react";
import { Link } from "react-router-dom";
import "./dashboard.css";
import { KERCHUNK_SECONDS, formatAirtime, groupByDay, type Transmission } from "./activity";

export type { Transmission };

// Bars use a square-root scale so a 3 s kerchunk and a 2 min ragchew both stay readable.
function barLength(duration: number, longest: number) {
  if (longest <= 0) return 0;
  return Math.max(0.03, Math.sqrt(duration) / Math.sqrt(longest));
}

export function RecentActivity({ rows, showLink = true }: { rows: Transmission[]; showLink?: boolean }) {
  const groups = groupByDay(rows);
  const longest = rows.reduce((max, row) => Math.max(max, row.duration), 0);
  const total = rows.reduce((sum, row) => sum + row.duration, 0);
  const kerchunks = rows.filter((row) => !row.timed_out && row.duration < KERCHUNK_SECONDS).length;

  return (
    <Card withBorder padding="lg" radius="md" component="section" className="mor-dash" aria-labelledby="dash-activity-title">
      <Group justify="space-between" mb="sm" wrap="nowrap">
        <h2 id="dash-activity-title" className="mor-card-title">
          Recent transmissions
        </h2>
        {showLink ? (
          <Button component={Link} to="/activity" size="xs" variant="subtle">
            All activity
          </Button>
        ) : null}
      </Group>
      {rows.length ? (
        <>
          <Text className="mor-act-summary" mb="md">
            Last {rows.length}: {formatAirtime(total)} on the air
            {kerchunks ? `, ${kerchunks} kerchunk${kerchunks === 1 ? "" : "s"}` : ""}.
          </Text>
          {groups.map((group) => (
            <Fragment key={group.label}>
              <h3 className="mor-act-day">{group.label}</h3>
              <ul className="mor-act-list">
                {group.rows.map((row) => (
                  <li key={row.started_at} className="mor-act-row" data-kind={row.kind}>
                    <span className="mor-act-time">
                      {row.when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                    </span>
                    <span className="mor-act-track">
                      <span
                        className="mor-act-bar"
                        aria-hidden="true"
                        style={{ ["--len" as string]: `${(barLength(row.duration, longest) * 100).toFixed(1)}%` }}
                      />
                      {row.kind !== "normal" ? (
                        <span className="mor-act-note">{row.kind === "timeout" ? "Timed out" : "Kerchunk"}</span>
                      ) : null}
                    </span>
                    <span className="mor-act-dur">{formatAirtime(row.duration)}</span>
                  </li>
                ))}
              </ul>
            </Fragment>
          ))}
        </>
      ) : (
        <Text c="dimmed" size="sm">
          Nothing on the air yet. Key up a radio, or try the <Link to="/simulator">Simulator</Link>.
        </Text>
      )}
    </Card>
  );
}
