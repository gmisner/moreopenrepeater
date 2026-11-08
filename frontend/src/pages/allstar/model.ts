// Pure helpers for the AllStar page: where nodes sit in the diagram, where
// schedules land on the week, what setup still needs doing. No React in here.

export type LinkRow = {
  node: string;
  name?: string;
  callsign?: string;
  keyed?: boolean;
  mode?: string;
  until?: string;
};

export type LinkEvent = {
  at: string;
  kind: "connected" | "disconnected" | "keyed" | "unkeyed";
  node: string;
  label: string;
};

export type ScheduleLike = {
  node: string;
  name: string;
  days: number[];
  time: string;
  minutes: number;
  monitor: boolean;
  enabled: boolean;
};

export type StatusLike = {
  available: boolean;
  manual: boolean;
  restart_needed: boolean;
  error: string | null;
  node: string | null;
  identity?: { has_password: boolean } | null;
};

/** Where each of n linked nodes sits around the hub, as percentages of the diagram box. */
export function ringPositions(count: number, rx = 31, ry = 28): { x: number; y: number }[] {
  if (count <= 0) return [];
  if (count === 1) return [{ x: 50 + rx, y: 50 }];
  return Array.from({ length: count }, (_, i) => {
    // Start at the upper right and go clockwise so the first link is always in the same place.
    const angle = -Math.PI / 3 + (2 * Math.PI * i) / count;
    return { x: 50 + rx * Math.cos(angle), y: 50 + ry * Math.sin(angle) };
  });
}

/**
 * A gentle curve from the hub (50, 50) to a node, in the 0-100 box the diagram's
 * SVG uses. The control point is pushed sideways so wires fan out instead of
 * running dead straight, and every wire bows the same way.
 */
export function curvePath(x: number, y: number, bow = 0.14): string {
  const dx = x - 50;
  const dy = y - 50;
  const cx = 50 + dx / 2 - dy * bow;
  const cy = 50 + dy / 2 + dx * bow;
  return `M 50 50 Q ${cx.toFixed(2)} ${cy.toFixed(2)} ${x.toFixed(2)} ${y.toFixed(2)}`;
}

/** Labels go on the side of the dot facing away from the hub so a wire never crosses text. */
export function labelSide(y: number): "up" | "down" {
  return y < 46 ? "up" : "down";
}

export type WeekBlock = {
  /** Index into the schedules array. */
  index: number;
  /** 0 = Monday ... 6 = Sunday, same as the schedule's own days. */
  day: number;
  startMin: number;
  endMin: number;
  /** Runs until someone disconnects: drawn to the end of the day, fading out. */
  openEnded: boolean;
  /** True for the part that spilled past midnight into the next day. */
  continued: boolean;
};

function parseTime(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const h = Number(match[1]);
  const m = Number(match[2]);
  if (h > 23 || m > 59) return null;
  return h * 60 + m;
}

/** Turn schedules into per-day blocks, splitting any that run past midnight. */
export function weekBlocks(schedules: ScheduleLike[]): WeekBlock[] {
  const blocks: WeekBlock[] = [];
  schedules.forEach((schedule, index) => {
    const start = parseTime(schedule.time);
    if (start == null) return;
    for (const day of schedule.days) {
      if (schedule.minutes <= 0) {
        blocks.push({ index, day, startMin: start, endMin: 1440, openEnded: true, continued: false });
        continue;
      }
      const end = start + schedule.minutes;
      if (end <= 1440) {
        blocks.push({ index, day, startMin: start, endMin: end, openEnded: false, continued: false });
      } else {
        blocks.push({ index, day, startMin: start, endMin: 1440, openEnded: false, continued: false });
        blocks.push({ index, day: (day + 1) % 7, startMin: 0, endMin: Math.min(1440, end - 1440), openEnded: false, continued: true });
      }
    }
  });
  return blocks;
}

/** JS getDay() has Sunday = 0; the schedules use Monday = 0. */
export function mondayFirstDay(date: Date): number {
  return (date.getDay() + 6) % 7;
}

export type SetupStep = { id: string; title: string; detail: string; done: boolean; action?: "node" | "favorites" };

/** True once the repeater is the radio for a real node and nothing is in the way. */
export function isReady(status: StatusLike | null): boolean {
  if (!status) return false;
  if (status.manual) return true;
  return status.available && !status.error && Boolean(status.node) && !status.restart_needed;
}

export function setupSteps(status: StatusLike | null, favoriteCount: number): SetupStep[] {
  const installed = Boolean(status && (status.available || status.manual));
  const hasNode = Boolean(status && (status.manual || status.node));
  const node = status?.node ?? "";
  const needsPassword = hasNode && !status?.manual && Number(node) >= 2000 && !status?.identity?.has_password;
  const steps: SetupStep[] = [
    {
      id: "install",
      title: "Install AllStarLink on this machine",
      detail: "Run install-pi.sh --allstar on the repeater, or point it at a node running elsewhere (see the setup guide).",
      done: installed,
    },
    {
      id: "node",
      title: "Register your node",
      detail: needsPassword
        ? "Enter your node number, callsign and the node password from the AllStarLink portal."
        : "Choose the node number this repeater becomes the radio for, and enter your callsign.",
      done: hasNode && !needsPassword,
      action: "node",
    },
  ];
  if (status?.restart_needed) {
    steps.push({
      id: "restart",
      title: "Restart Asterisk",
      detail: "Your node is set up but Asterisk hasn't picked it up yet. Run sudo systemctl restart asterisk.",
      done: false,
    });
  }
  steps.push({
    id: "favorite",
    title: "Add a favorite node",
    detail: "Favorites become one-click buttons on this page and the dashboard.",
    done: favoriteCount > 0,
    action: "favorites",
  });
  return steps;
}

/** Nodes worth offering again: recently connected, not a favorite, not linked right now. */
export function recentNodes(events: LinkEvent[], favorites: string[], linked: string[], limit = 5): { node: string; label: string }[] {
  const skip = new Set([...favorites, ...linked]);
  const seen = new Set<string>();
  const out: { node: string; label: string }[] = [];
  for (const event of [...events].sort((a, b) => b.at.localeCompare(a.at))) {
    if (event.kind !== "connected" || skip.has(event.node) || seen.has(event.node)) continue;
    seen.add(event.node);
    out.push({ node: event.node, label: event.label });
    if (out.length >= limit) break;
  }
  return out;
}

export function minutesLabel(total: number): string {
  const h = Math.floor(total / 60) % 24;
  const m = total % 60;
  const suffix = total >= 1440 ? "12 AM" : `${h % 12 === 0 ? 12 : h % 12}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h < 12 ? "AM" : "PM"}`;
  return suffix;
}
