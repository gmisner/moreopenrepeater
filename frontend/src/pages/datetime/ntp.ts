/** What `GET /api/time` and `PUT /api/time/ntp` answer (src/api/models/time_sync.py). */
export type TimeContainer = {
  running: boolean;
  synced: boolean | null;
  stratum: number | null;
  /** How far the container's clock is from its server. Never signed: it says how far, not which way. */
  offset_ms: number | null;
  /** The server the container is using right now. */
  server: string | null;
  config_error: string | null;
  updated_at: number | null;
};

export type TimeState = "ok" | "off" | "unreachable" | "not_checked";

export type TimeStatus = {
  /** The host that is checked: the setting, or the default while it is empty. */
  server: string;
  /** What was set. Empty means the default. */
  configured_server: string;
  default_server: string;
  state: TimeState;
  reachable: boolean | null;
  /** The server's time minus the Pi's clock, so positive means the Pi is BEHIND. */
  offset_ms: number | null;
  round_trip_ms: number | null;
  stratum: number | null;
  /** Off by this much or more is "off". */
  limit_ms: number;
  checked_at: number | null;
  error: string | null;
  error_text: string | null;
  container: TimeContainer;
  /** A native install has no time container, so the page leaves that card out. */
  install: "native" | "container";
};

function oneDecimal(value: number): string {
  return String(Math.round(value * 10) / 10);
}

// Largest unit first; the amount is taken in that unit once it reaches one of them.
const UNITS: [name: string, ms: number][] = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
  ["second", 1000],
];

/** A length of time in the words a person would use: "8 ms", "2.4 seconds", "1 hour", "3.5 days". */
export function durationWords(ms: number): string {
  const amount = Math.abs(ms);
  const rounded = Math.round(amount * 10) / 10;
  for (const [name, size] of UNITS) {
    if (rounded >= size) {
      const value = oneDecimal(rounded / size);
      return `${value} ${name}${value === "1" ? "" : "s"}`;
    }
  }
  return `${oneDecimal(rounded)} ms`;
}

/**
 * The offset as a sentence about the Pi's clock. The server reports its time minus the Pi's clock, so a positive
 * offset means the Pi is behind and a negative one means it is ahead.
 */
export function offsetSentence(offsetMs: number, server: string): string {
  if (Math.abs(offsetMs) < 1) return `The Pi's clock matches ${server} to within a millisecond`;
  return `The Pi's clock is ${durationWords(offsetMs)} ${offsetMs > 0 ? "behind" : "ahead of"} ${server}`;
}
