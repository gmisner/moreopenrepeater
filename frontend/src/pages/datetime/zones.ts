// What the Date and time page needs to know about zones, apart from drawing it.

/** What GET /api/timezone says. */
export type TimezoneInfo = {
  /** The saved setting: a zone name, or "" for the default. */
  setting: string;
  /** The zone in force ("" if the system can't name it). */
  effective: string;
  /** What an empty setting means here, and where that came from. */
  default: string;
  default_source: "container" | "system" | "unknown" | string;
  /** The dashboard process's own clock, with its UTC offset. */
  now: string;
  zones: string[];
};

/** "America/Phoenix" -> group "America". Names with no region (UTC) go under "Other". */
export function groupZones(zones: string[]) {
  const groups = new Map<string, string[]>();
  for (const name of zones) {
    const region = name.includes("/") ? name.slice(0, name.indexOf("/")) : "Other";
    groups.set(region, [...(groups.get(region) ?? []), name]);
  }
  return [...groups].map(([group, items]) => ({ group, items }));
}

/** The time in a zone as a person reads it, or null if this browser doesn't know the zone. */
export function timeIn(zone: string, at: Date): string | null {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: zone,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    }).format(at);
  } catch {
    return null;
  }
}

/** "17:30 (UTC+05:30)" from the repeater's ISO time, read as written: its own clock, not this browser's. */
export function stationClock(iso: string): string {
  const clock = iso.slice(11, 16);
  const offset = iso.endsWith("Z") ? "+00:00" : iso.slice(-6);
  return `${clock} (UTC${offset})`;
}
