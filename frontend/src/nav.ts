export type NavItem = { id: string; label: string; icon: string; adminOnly?: boolean };
export type NavGroup = { label: string; items: NavItem[] };

// Grouped by what the operator is doing, not by how the software is built.
export const NAV: NavGroup[] = [
  {
    label: "Operate",
    items: [
      { id: "dashboard", label: "Dashboard", icon: "dashboard" },
      { id: "activity", label: "Activity", icon: "activity" },
      { id: "recordings", label: "Recordings", icon: "play" },
      { id: "net", label: "Net", icon: "net" },
      { id: "map", label: "APRS map", icon: "pin" },
    ],
  },
  {
    label: "Repeater",
    items: [
      { id: "timing", label: "Timing", icon: "timing" },
      { id: "identification", label: "Identification", icon: "id" },
      { id: "audio", label: "Audio & tones", icon: "audio" },
      { id: "macros", label: "DTMF macros", icon: "macros" },
      { id: "announcements", label: "Announcements", icon: "announce" },
      { id: "simulator", label: "Simulator", icon: "simulator" },
      { id: "configs", label: "Known configs", icon: "info" },
    ],
  },
  {
    label: "Integrations",
    items: [
      { id: "allstar", label: "AllStarLink", icon: "links" },
      { id: "dmr", label: "DMR", icon: "radio" },
      { id: "aprs", label: "APRS", icon: "radio" },
      { id: "autopatch", label: "Autopatch", icon: "phone" },
      { id: "weather", label: "Weather", icon: "weather" },
      { id: "sensors", label: "Site sensors", icon: "thermometer" },
      { id: "mailbox", label: "Mailbox", icon: "mail" },
      { id: "public", label: "Public listening", icon: "headphones" },
    ],
  },
  {
    label: "System",
    items: [
      { id: "logs", label: "Logs", icon: "logs" },
      { id: "backup", label: "Backup & restore", icon: "backup" },
      { id: "users", label: "Users", icon: "users", adminOnly: true },
      { id: "sites", label: "Sites", icon: "tower", adminOnly: true },
      { id: "audit", label: "Audit log", icon: "audit", adminOnly: true },
      { id: "alerts", label: "Alerts & health", icon: "bell", adminOnly: true },
      { id: "updates", label: "Updates", icon: "update", adminOnly: true },
      { id: "remote", label: "Remote access", icon: "remote", adminOnly: true },
    ],
  },
];

export const TABBAR: NavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard" },
  { id: "activity", label: "Activity", icon: "activity" },
  { id: "allstar", label: "Links", icon: "links" },
  { id: "map", label: "Map", icon: "pin" },
];

export const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  NAV.flatMap((g) => g.items).map((item) => [item.id, item.label]),
);
