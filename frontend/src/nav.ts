export type NavItem = { id: string; label: string; adminOnly?: boolean };
export type NavGroup = { label?: string; items: NavItem[] };

export const NAV: NavGroup[] = [
  {
    items: [
      { id: "dashboard", label: "Dashboard" },
      { id: "activity", label: "Activity" },
    ],
  },
  {
    label: "Repeater",
    items: [
      { id: "timing", label: "Timing" },
      { id: "identification", label: "Identification" },
      { id: "audio", label: "Audio & tones" },
    ],
  },
  {
    label: "Features",
    items: [
      { id: "macros", label: "DTMF macros" },
      { id: "net", label: "Net" },
      { id: "mailbox", label: "Mailbox" },
      { id: "announcements", label: "Announcements" },
      { id: "weather", label: "Weather" },
      { id: "autopatch", label: "Autopatch" },
      { id: "allstar", label: "AllStarLink" },
      { id: "aprs", label: "APRS" },
      { id: "map", label: "APRS map" },
      { id: "public", label: "Public listening" },
    ],
  },
  {
    label: "Tools",
    items: [
      { id: "simulator", label: "Simulator" },
      { id: "logs", label: "Logs" },
      { id: "backup", label: "Backup & restore" },
      { id: "users", label: "Users", adminOnly: true },
      { id: "audit", label: "Audit log", adminOnly: true },
      { id: "alerts", label: "Alerts & health", adminOnly: true },
      { id: "updates", label: "Updates", adminOnly: true },
      { id: "remote", label: "Remote access", adminOnly: true },
    ],
  },
];

export const TABBAR: NavItem[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "activity", label: "Activity" },
  { id: "allstar", label: "Links" },
  { id: "map", label: "Map" },
];

export const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  NAV.flatMap((g) => g.items).map((item) => [item.id, item.label]),
);
