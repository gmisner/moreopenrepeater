export type NavItem = { id: string; label: string; icon: string; adminOnly?: boolean };
export type NavGroup = { label?: string; items: NavItem[] };

export const NAV: NavGroup[] = [
  {
    items: [
      { id: "dashboard", label: "Dashboard", icon: "dashboard" },
      { id: "activity", label: "Activity", icon: "activity" },
    ],
  },
  {
    label: "Repeater",
    items: [
      { id: "timing", label: "Timing", icon: "timing" },
      { id: "identification", label: "Identification", icon: "id" },
      { id: "audio", label: "Audio & tones", icon: "audio" },
    ],
  },
  {
    label: "Features",
    items: [
      { id: "macros", label: "DTMF macros", icon: "macros" },
      { id: "net", label: "Net", icon: "net" },
      { id: "mailbox", label: "Mailbox", icon: "mail" },
      { id: "announcements", label: "Announcements", icon: "announce" },
      { id: "weather", label: "Weather", icon: "weather" },
      { id: "autopatch", label: "Autopatch", icon: "phone" },
      { id: "allstar", label: "AllStarLink", icon: "links" },
      { id: "aprs", label: "APRS", icon: "radio" },
      { id: "map", label: "APRS map", icon: "pin" },
      { id: "public", label: "Public listening", icon: "headphones" },
    ],
  },
  {
    label: "Tools",
    items: [
      { id: "simulator", label: "Simulator", icon: "simulator" },
      { id: "logs", label: "Logs", icon: "logs" },
      { id: "backup", label: "Backup & restore", icon: "backup" },
      { id: "users", label: "Users", icon: "users", adminOnly: true },
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
