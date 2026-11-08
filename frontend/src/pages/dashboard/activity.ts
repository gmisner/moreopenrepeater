import { formatAirtime } from "../../format";

export { formatAirtime };

// Formatting helpers for the dashboard's recent transmissions list.

export type Transmission = { started_at: string; duration: number; timed_out: boolean };

export const KERCHUNK_SECONDS = 1.5;

function dayLabel(when: Date, now = new Date()) {
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(when)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return when.toLocaleDateString([], { weekday: "long" });
  return when.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

type Row = Transmission & { when: Date; kind: "normal" | "kerchunk" | "timeout" };

export function groupByDay(rows: Transmission[], now = new Date()) {
  const groups: { label: string; rows: Row[] }[] = [];
  for (const row of rows) {
    const when = new Date(row.started_at);
    const kind = row.timed_out ? "timeout" : row.duration < KERCHUNK_SECONDS ? "kerchunk" : "normal";
    const label = dayLabel(when, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.rows.push({ ...row, when, kind });
    else groups.push({ label, rows: [{ ...row, when, kind }] });
  }
  return groups;
}
