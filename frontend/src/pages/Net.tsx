import {
  Accordion,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  NumberInput,
  PasswordInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { ConfigEditDrawer } from "../components/ConfigEditDrawer";
import { ConfigSummaryCard } from "../components/ConfigSummaryCard";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useStatusLive } from "../status";
import { labeled } from "../components/FieldHint";
import { EmptyState } from "../components/EmptyState";
import { ActionsHeader } from "../components/ActionsHeader";

type Checkin = {
  id: string;
  at: number;
  callsign: string;
  notes: string;
  name?: string;
  location?: string;
  license_class?: string;
  staying_for_comments?: boolean;
};

type CurrentNet = {
  name: string;
  started_at: number;
  started_by: string;
  ends_at: number;
  linked_node?: string | null;
  link_error?: string | null;
  checkins: Checkin[];
};

type PastNet = {
  id: string;
  name: string;
  started_at: number;
  ended_at: number;
  checkin_count: number;
};

type NetStatus = {
  current: CurrentNet | null;
  past: PastNet[];
  next_scheduled?: number | null;
};

type NetSchedule = {
  time: string;
  minutes: number;
  days: number[];
  enabled: boolean;
};

type QrzSettings = {
  username: string;
  password_set: boolean;
  configured: boolean;
};

type CallsignLookup = {
  callsign: string;
  found: boolean;
  configured: boolean;
  name: string;
  location: string;
  license_class: string;
};

type NetValues = {
  net_name: string;
  net_max_minutes: number;
  net_tot_duration: number;
  net_hang_time: number;
  net_courtesy_tone_style: string;
  net_courtesy_tone_asset_id: string;
  net_hold_announcements: boolean;
  net_hold_autopatch: boolean;
  net_links: string;
  net_link_node: string;
  net_start_say: string;
  net_end_say: string;
};

const POLL_MS = 15000;
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const TONE_STYLES = [
  { value: "same", label: "Same as usual" },
  { value: "none", label: "Off" },
  { value: "beep", label: "Beep" },
  { value: "high_low", label: "High–low" },
  { value: "low_high", label: "Low–high" },
  { value: "bee_boo", label: "Bee-boo" },
  { value: "triple", label: "Triple beep" },
  { value: "chirp", label: "Chirp" },
  { value: "bumblebee", label: "Bumblebee" },
  { value: "up_run", label: "Three tones up" },
  { value: "down_run", label: "Three tones down" },
  { value: "bonk", label: "Bonk" },
  { value: "nextel", label: "Nextel chirp" },
  { value: "cw_k", label: 'CW "K"' },
  { value: "cw_r", label: 'CW "R"' },
  { value: "cw_t", label: 'CW "T"' },
  { value: "custom", label: "Custom" },
];

const NET_KEYS = [
  "net_name",
  "net_max_minutes",
  "net_tot_duration",
  "net_hang_time",
  "net_courtesy_tone_style",
  "net_courtesy_tone_asset_id",
  "net_hold_announcements",
  "net_hold_autopatch",
  "net_links",
  "net_link_node",
  "net_start_say",
  "net_end_say",
] as (keyof NetValues)[];

const NET_DEFAULTS: NetValues = {
  net_name: "Net",
  net_max_minutes: 120,
  net_tot_duration: 300,
  net_hang_time: 3,
  net_courtesy_tone_style: "same",
  net_courtesy_tone_asset_id: "",
  net_hold_announcements: true,
  net_hold_autopatch: true,
  net_links: "leave",
  net_link_node: "",
  net_start_say: "",
  net_end_say: "",
};

function clock(at: number) {
  return new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function shortTime(value: number | string) {
  const when = new Date(typeof value === "number" ? value : value);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return time;
  const farOff = when.getTime() - Date.now() > 6 * 24 * 3600 * 1000;
  const day = when.toLocaleDateString([], farOff ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "short" });
  return `${day} ${time}`;
}

function minutesLabel(seconds: number) {
  const total = Math.max(0, Math.round(seconds / 60));
  return total < 60 ? `${total} min` : `${Math.floor(total / 60)} h ${total % 60} min`;
}

function runningText(current: CurrentNet) {
  const parts = [`Started ${clock(current.started_at)} by ${current.started_by}`, `ends by itself at ${clock(current.ends_at)}`];
  if (current.linked_node) parts.push(`linked to node ${current.linked_node} for the net`);
  return `${parts.join(", ")}.`;
}

function emptySchedule(): NetSchedule {
  return { days: [], time: "", minutes: 60, enabled: true };
}

function toneLabel(style: string) {
  return TONE_STYLES.find((item) => item.value === style)?.label ?? style;
}

function linksLabel(value: string) {
  if (value === "disconnect") return "Disconnect all links";
  if (value === "connect") return "Link a node";
  return "Leave links as they are";
}

function scheduleSummary(schedules: NetSchedule[]) {
  const enabled = schedules.filter((s) => s.enabled);
  if (schedules.length === 0) return "None";
  if (enabled.length === 0) return `${schedules.length} · all off`;
  return `${enabled.length} on`;
}

function daysLabel(days: number[]) {
  if (days.length === 7) return "Every day";
  if (days.length === 0) return "No days";
  return days.map((d) => DAY_NAMES[d]).join(", ");
}

export function Net() {
  const { config, save: saveConfig } = useConfig();
  const status = useStatusLive();
  const settings = useConfigSlice<NetValues>(NET_KEYS, NET_DEFAULTS);

  const [net, setNet] = useState<NetStatus | null>(null);
  const [startName, setStartName] = useState("");
  const [checkinCallsign, setCheckinCallsign] = useState("");
  const [checkinName, setCheckinName] = useState("");
  const [checkinLocation, setCheckinLocation] = useState("");
  const [checkinLicenseClass, setCheckinLicenseClass] = useState("");
  const [checkinStaying, setCheckinStaying] = useState(false);
  const [checkinNotes, setCheckinNotes] = useState("");
  const [editingCheckin, setEditingCheckin] = useState<string | null>(null);
  const [editCallsign, setEditCallsign] = useState("");
  const [editName, setEditName] = useState("");
  const [editLocation, setEditLocation] = useState("");
  const [editLicenseClass, setEditLicenseClass] = useState("");
  const [editStaying, setEditStaying] = useState(false);
  const [editNotes, setEditNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [schedules, setSchedules] = useState<NetSchedule[]>([]);
  const [schedulesSaving, setSchedulesSaving] = useState(false);
  const [editingSettings, setEditingSettings] = useState(false);
  const [editingSchedules, setEditingSchedules] = useState(false);
  const [qrz, setQrz] = useState<QrzSettings | null>(null);
  const [editingQrz, setEditingQrz] = useState(false);
  const [qrzUsername, setQrzUsername] = useState("");
  const [qrzPassword, setQrzPassword] = useState("");
  const [qrzSaving, setQrzSaving] = useState(false);
  const [lookingUp, setLookingUp] = useState(false);
  const wasActive = useRef<boolean | null>(null);
  const callsignRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setNet(await api<NetStatus>("/api/net"));
  }, []);

  const refreshQrz = useCallback(async () => {
    const status = await api<QrzSettings>("/api/net/qrz");
    setQrz(status);
    setQrzUsername(status.username);
  }, []);

  useEffect(() => {
    refresh().catch(notifyError);
    refreshQrz().catch(notifyError);
  }, [refresh, refreshQrz]);

  useEffect(() => {
    const schedulesFromConfig = (config?.net_schedules as NetSchedule[] | undefined) ?? [];
    setSchedules(schedulesFromConfig.map((s) => ({ ...s, days: [...(s.days ?? [])] })));
  }, [config]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (!document.hidden) refresh().catch(() => {});
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const active = Boolean(status?.net_active);
    if (wasActive.current !== null && wasActive.current !== active) {
      refresh().catch(() => {});
    }
    wasActive.current = active;
  }, [status?.net_active, refresh]);

  const call = async (path: string, options: Parameters<typeof api>[1], message?: string) => {
    try {
      setNet(await api<NetStatus>(path, options));
      if (message) notifySuccess(message);
      return true;
    } catch (error) {
      notifyError(error);
      return false;
    }
  };

  const endNet = async () => {
    if (!window.confirm(`End ${net?.current?.name ?? "the net"}?`)) return;
    setBusy(true);
    try {
      await call("/api/net/end", { method: "POST" }, "Net ended");
    } finally {
      setBusy(false);
    }
  };

  const startNet = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (await call("/api/net/start", { method: "POST", json: { name: startName } }, "Net started")) {
        setStartName("");
        callsignRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  };

  const lookupCallsign = async () => {
    const callsign = checkinCallsign.trim();
    if (!callsign || !qrz?.configured) return;
    setLookingUp(true);
    try {
      const result = await api<CallsignLookup>(`/api/net/lookup/${encodeURIComponent(callsign)}`);
      if (!result.found) return;
      if (!checkinName.trim() && result.name) setCheckinName(result.name);
      if (!checkinLocation.trim() && result.location) setCheckinLocation(result.location);
      if (!checkinLicenseClass.trim() && result.license_class) setCheckinLicenseClass(result.license_class);
    } catch (error) {
      notifyError(error);
    } finally {
      setLookingUp(false);
    }
  };

  const saveQrz = async (event: FormEvent) => {
    event.preventDefault();
    setQrzSaving(true);
    try {
      const body: { username: string; password?: string } = { username: qrzUsername.trim() };
      if (qrzPassword) body.password = qrzPassword;
      const status = await api<QrzSettings>("/api/net/qrz", { method: "PUT", json: body });
      setQrz(status);
      setQrzUsername(status.username);
      setQrzPassword("");
      setEditingQrz(false);
      notifySuccess(status.configured ? "QRZ lookup saved" : "QRZ username saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setQrzSaving(false);
    }
  };

  const addCheckin = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (
        await call("/api/net/checkins", {
          method: "POST",
          json: {
            callsign: checkinCallsign.trim(),
            name: checkinName.trim(),
            location: checkinLocation.trim(),
            license_class: checkinLicenseClass.trim(),
            staying_for_comments: checkinStaying,
            notes: checkinNotes.trim(),
          },
        })
      ) {
        setCheckinCallsign("");
        setCheckinName("");
        setCheckinLocation("");
        setCheckinLicenseClass("");
        setCheckinStaying(false);
        setCheckinNotes("");
        callsignRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  };

  const beginEditCheckin = (checkin: Checkin) => {
    setEditingCheckin(checkin.id);
    setEditCallsign(checkin.callsign);
    setEditName(checkin.name ?? "");
    setEditLocation(checkin.location ?? "");
    setEditLicenseClass(checkin.license_class ?? "");
    setEditStaying(Boolean(checkin.staying_for_comments));
    setEditNotes(checkin.notes);
  };

  const saveCheckin = async (id: string) => {
    if (!editCallsign.trim()) return;
    setEditingCheckin(null);
    await call(`/api/net/checkins/${encodeURIComponent(id)}`, {
      method: "PUT",
      json: {
        callsign: editCallsign.trim(),
        name: editName.trim(),
        location: editLocation.trim(),
        license_class: editLicenseClass.trim(),
        staying_for_comments: editStaying,
        notes: editNotes.trim(),
      },
    });
  };

  const deleteCheckin = async (checkin: Checkin) => {
    if (!window.confirm(`Remove ${checkin.callsign}?`)) return;
    await call(`/api/net/checkins/${encodeURIComponent(checkin.id)}`, { method: "DELETE" });
  };

  const deletePast = async (past: PastNet) => {
    if (!window.confirm("Delete this net and its check-ins?")) return;
    await call(`/api/nets/${encodeURIComponent(past.id)}`, { method: "DELETE" }, "Net deleted");
  };

  const saveSchedules = async (event: FormEvent) => {
    event.preventDefault();
    if (schedules.some((schedule) => schedule.days.length === 0)) {
      notifyError(new Error("Pick at least one day for each schedule."));
      return;
    }
    setSchedulesSaving(true);
    try {
      await saveConfig({ net_schedules: schedules });
      await refresh();
      notifySuccess("Schedules saved");
      setEditingSchedules(false);
    } catch (error) {
      notifyError(error);
    } finally {
      setSchedulesSaving(false);
    }
  };

  const current = net?.current ?? null;
  const past = net?.past ?? [];
  let detail = "";
  if (current) {
    detail = runningText(current);
    if (current.link_error) detail += ` Links: ${current.link_error}`;
  } else if (net?.next_scheduled) {
    detail = `Next scheduled net: ${shortTime(net.next_scheduled * 1000)}.`;
  }

  const linkNodeNeeded = settings.values.net_links === "connect";

  return (
    <Page title="Net">
      <Card withBorder padding="lg" radius="md">
        <Group justify="space-between" align="flex-start">
          <div>
            <Group gap="sm">
              <Title order={2}>{current ? current.name : "No net running"}</Title>
              {current ? (
                <Badge color="green" variant="light">
                  running
                </Badge>
              ) : null}
            </Group>
            {detail ? (
              <Text c="dimmed" size="sm" mt={4}>
                {detail}
              </Text>
            ) : null}
          </div>
        </Group>
        {current ? (
          <Group justify="flex-end" mt="md">
            <Button color="red" loading={busy} onClick={() => void endNet()}>
              End net
            </Button>
          </Group>
        ) : (
          <form onSubmit={(event) => void startNet(event)}>
            <Group align="flex-end" mt="md" wrap="wrap">
              <TextInput
                label={labeled("Name", "Optional · blank uses the default name")}
                style={{ flex: 1, minWidth: 180 }}
                maxLength={60}
                placeholder=""
                value={startName}
                onChange={(event) => setStartName(event.currentTarget.value)}
              />
              <Button type="submit" loading={busy}>
                Start net
              </Button>
            </Group>
          </form>
        )}
      </Card>

      {current ? (
        <Card withBorder padding="lg" radius="md">
          <Group justify="space-between" mb="md">
            <Title order={2}>Check-ins</Title>
            <Text c="dimmed" size="sm">
              {current.checkins.length
                ? `${current.checkins.length} station${current.checkins.length === 1 ? "" : "s"}`
                : ""}
            </Text>
          </Group>
          <form onSubmit={(event) => void addCheckin(event)}>
            <Stack gap="sm" mb="md">
              <Group align="flex-end" wrap="wrap">
                <TextInput
                  ref={callsignRef}
                  label="Callsign"
                  required
                  pattern="[A-Za-z0-9/\-]{1,15}"
                  autoCapitalize="characters"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="e.g. W1AW"
                  value={checkinCallsign}
                  onChange={(event) => setCheckinCallsign(event.currentTarget.value)}
                  onBlur={() => void lookupCallsign()}
                />
                {qrz?.configured ? (
                  <Button type="button" variant="subtle" loading={lookingUp} onClick={() => void lookupCallsign()}>
                    Look up
                  </Button>
                ) : null}
                <TextInput
                  label={labeled("Name", "Optional · filled from QRZ when configured")}
                  style={{ flex: 1, minWidth: 140 }}
                  maxLength={80}
                  value={checkinName}
                  onChange={(event) => setCheckinName(event.currentTarget.value)}
                />
                <TextInput
                  label={labeled("Location", "Optional · city, state")}
                  style={{ flex: 1, minWidth: 140 }}
                  maxLength={80}
                  value={checkinLocation}
                  onChange={(event) => setCheckinLocation(event.currentTarget.value)}
                />
                <TextInput
                  label={labeled("Class", "Optional · license class")}
                  style={{ width: 120 }}
                  maxLength={40}
                  value={checkinLicenseClass}
                  onChange={(event) => setCheckinLicenseClass(event.currentTarget.value)}
                />
              </Group>
              <Group align="flex-end" wrap="wrap">
                <TextInput
                  label={labeled("Notes", "Optional")}
                  style={{ flex: 1, minWidth: 200 }}
                  maxLength={300}
                  value={checkinNotes}
                  onChange={(event) => setCheckinNotes(event.currentTarget.value)}
                />
                <Checkbox
                  label={labeled("Staying for comments", "Mark stations that stay after the roll call")}
                  checked={checkinStaying}
                  onChange={(event) => setCheckinStaying(event.currentTarget.checked)}
                  mb={6}
                />
                <Button type="submit" loading={busy}>
                  Check in
                </Button>
              </Group>
            </Stack>
          </form>
          <Table.ScrollContainer minWidth={780}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>#</Table.Th>
                  <Table.Th>Time</Table.Th>
                  <Table.Th>Callsign</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Location</Table.Th>
                  <Table.Th>Class</Table.Th>
                  <Table.Th>Comments</Table.Th>
                  <Table.Th>Notes</Table.Th>
                  <ActionsHeader />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {current.checkins.map((checkin, index) => (
                  <Table.Tr key={checkin.id}>
                    <Table.Td>{index + 1}</Table.Td>
                    <Table.Td>{clock(checkin.at)}</Table.Td>
                    {editingCheckin === checkin.id ? (
                      <>
                        <Table.Td>
                          <TextInput
                            required
                            pattern="[A-Za-z0-9/\-]{1,15}"
                            value={editCallsign}
                            onChange={(event) => setEditCallsign(event.currentTarget.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                void saveCheckin(checkin.id);
                              } else if (event.key === "Escape") {
                                setEditingCheckin(null);
                              }
                            }}
                          />
                        </Table.Td>
                        <Table.Td>
                          <TextInput
                            maxLength={80}
                            value={editName}
                            onChange={(event) => setEditName(event.currentTarget.value)}
                          />
                        </Table.Td>
                        <Table.Td>
                          <TextInput
                            maxLength={80}
                            value={editLocation}
                            onChange={(event) => setEditLocation(event.currentTarget.value)}
                          />
                        </Table.Td>
                        <Table.Td>
                          <TextInput
                            maxLength={40}
                            value={editLicenseClass}
                            onChange={(event) => setEditLicenseClass(event.currentTarget.value)}
                          />
                        </Table.Td>
                        <Table.Td>
                          <Checkbox
                            checked={editStaying}
                            onChange={(event) => setEditStaying(event.currentTarget.checked)}
                            aria-label="Staying for comments"
                          />
                        </Table.Td>
                        <Table.Td>
                          <TextInput
                            maxLength={300}
                            value={editNotes}
                            onChange={(event) => setEditNotes(event.currentTarget.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                void saveCheckin(checkin.id);
                              } else if (event.key === "Escape") {
                                setEditingCheckin(null);
                              }
                            }}
                          />
                        </Table.Td>
                        <Table.Td>
                          <Group gap="xs" justify="flex-end" wrap="nowrap">
                            <Button size="compact-sm" onClick={() => void saveCheckin(checkin.id)}>
                              Save
                            </Button>
                            <Button size="compact-sm" variant="subtle" onClick={() => setEditingCheckin(null)}>
                              Cancel
                            </Button>
                          </Group>
                        </Table.Td>
                      </>
                    ) : (
                      <>
                        <Table.Td>
                          <Text fw={700}>{checkin.callsign}</Text>
                        </Table.Td>
                        <Table.Td>{checkin.name || "—"}</Table.Td>
                        <Table.Td>{checkin.location || "—"}</Table.Td>
                        <Table.Td>{checkin.license_class || "—"}</Table.Td>
                        <Table.Td>{checkin.staying_for_comments ? "Yes" : "—"}</Table.Td>
                        <Table.Td>{checkin.notes || "—"}</Table.Td>
                        <Table.Td>
                          <Group gap="xs" justify="flex-end" wrap="nowrap">
                            <Button size="compact-sm" variant="subtle" onClick={() => beginEditCheckin(checkin)}>
                              Edit
                            </Button>
                            <Button size="compact-sm" color="red" variant="subtle" onClick={() => void deleteCheckin(checkin)}>
                              Remove
                            </Button>
                          </Group>
                        </Table.Td>
                      </>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {current.checkins.length === 0 ? (
            <Text c="dimmed" size="sm" mt="sm">
              Nobody has checked in yet.
            </Text>
          ) : null}
        </Card>
      ) : null}

      <Card withBorder padding="lg" radius="md">
        <Title order={2} mb="md">
          Past nets
        </Title>
        <Table.ScrollContainer minWidth={560}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Net</Table.Th>
                <Table.Th>Started</Table.Th>
                <Table.Th>Length</Table.Th>
                <Table.Th>Check-ins</Table.Th>
                <ActionsHeader />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {past.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td>{row.name}</Table.Td>
                  <Table.Td>{shortTime(row.started_at * 1000)}</Table.Td>
                  <Table.Td>{minutesLabel(row.ended_at - row.started_at)}</Table.Td>
                  <Table.Td>{row.checkin_count}</Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="flex-end" wrap="nowrap">
                      <Button
                        component="a"
                        size="compact-sm"
                        variant="subtle"
                        href={`/api/nets/${encodeURIComponent(row.id)}/checkins.csv`}
                        download
                      >
                        CSV
                      </Button>
                      <Button size="compact-sm" color="red" variant="subtle" onClick={() => void deletePast(row)}>
                        Delete
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {past.length === 0 ? (
          <EmptyState>No nets yet.</EmptyState>
        ) : null}
      </Card>

      <ConfigSummaryCard
        title="Callsign lookup"
        dirty={false}
        hint="Optional QRZ.com XML credentials. Kept on this controller, out of backups."
        onEdit={() => {
          setQrzUsername(qrz?.username ?? "");
          setQrzPassword("");
          setEditingQrz(true);
        }}
        rows={[
          { label: "Username", value: qrz?.username || "—" },
          {
            label: "Password",
            value: qrz?.password_set ? "Saved" : "Not set",
          },
          { label: "Lookup", value: qrz?.configured ? "Ready" : "Off" },
        ]}
      />

      <ConfigEditDrawer
        opened={editingQrz}
        onClose={() => setEditingQrz(false)}
        title="Callsign lookup"
        subtitle="QRZ XML Data subscription credentials. Leave the password blank to keep the saved one."
      >
        <form onSubmit={(event) => void saveQrz(event)}>
          <Stack gap="md">
            <TextInput
              label={labeled("QRZ username", "Usually your callsign on QRZ.com")}
              maxLength={40}
              autoComplete="username"
              value={qrzUsername}
              onChange={(event) => setQrzUsername(event.currentTarget.value)}
            />
            <PasswordInput
              label={labeled(
                "QRZ password",
                qrz?.password_set ? "Leave blank to keep the saved password" : "Required for lookup",
              )}
              autoComplete="new-password"
              value={qrzPassword}
              onChange={(event) => setQrzPassword(event.currentTarget.value)}
            />
            <Group justify="flex-end">
              <Button variant="subtle" onClick={() => setEditingQrz(false)}>
                Cancel
              </Button>
              <Button type="submit" loading={qrzSaving}>
                Save
              </Button>
            </Group>
          </Stack>
        </form>
      </ConfigEditDrawer>

      <ConfigSummaryCard
        title="Net settings"
        dirty={settings.dirty}
        hint="Defaults when a net starts: timeouts, holds, and what to say."
        onEdit={() => setEditingSettings(true)}
        rows={[
          { label: "Default name", value: settings.values.net_name || "—" },
          { label: "Ends after", value: `${settings.values.net_max_minutes} min` },
          { label: "TOT", value: `${settings.values.net_tot_duration} s` },
          { label: "Courtesy tone", value: toneLabel(settings.values.net_courtesy_tone_style) },
          { label: "Links", value: linksLabel(settings.values.net_links) },
          {
            label: "Holds",
            value: [
              settings.values.net_hold_announcements ? "announcements" : null,
              settings.values.net_hold_autopatch ? "phone patch" : null,
            ]
              .filter(Boolean)
              .join(", ") || "None",
          },
        ]}
      />

      <ConfigEditDrawer
        opened={editingSettings}
        onClose={() => setEditingSettings(false)}
        title="Net settings"
        subtitle="Applied when a net starts from here, DTMF, or a schedule."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void settings.submit().then((ok) => {
              if (ok) setEditingSettings(false);
            });
          }}
        >
          <Stack gap="md">
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <TextInput
                label="Default name"
                maxLength={60}
                required
                value={settings.values.net_name}
                onChange={(event) => settings.setField("net_name", event.currentTarget.value)}
              />
              <NumberInput
                label={labeled("End by itself after", "In case nobody ends it")}
                suffix=" minutes"
                min={10}
                max={1440}
                value={settings.values.net_max_minutes}
                onChange={(value) => settings.setField("net_max_minutes", Number(value) || 0)}
                required
              />
              <NumberInput
                label={labeled("Timeout timer during a net", "Net control often talks longer than the usual timeout allows.")}
                suffix=" s"
                min={30}
                max={3600}
                value={settings.values.net_tot_duration}
                onChange={(value) => settings.setField("net_tot_duration", Number(value) || 0)}
                required
              />
              <NumberInput
                label="Hang time during a net"
                suffix=" s"
                min={0.1}
                max={30}
                step={0.1}
                decimalScale={1}
                value={settings.values.net_hang_time}
                onChange={(value) => settings.setField("net_hang_time", Number(value) || 0)}
                required
              />
              <Select
                label={labeled("Built-in courtesy tone", "Replaces every courtesy tone while the net runs")}
                data={TONE_STYLES}
                value={settings.values.net_courtesy_tone_style}
                onChange={(value) => settings.setField("net_courtesy_tone_style", value || "same")}
                allowDeselect={false}
              />
              <TextInput
                label={labeled("Courtesy tone clip", "Asset id · blank = built-in tone")}
                placeholder=""
                value={settings.values.net_courtesy_tone_asset_id}
                onChange={(event) => settings.setField("net_courtesy_tone_asset_id", event.currentTarget.value)}
              />
            </SimpleGrid>
            <Switch
              label={labeled("Hold announcements", "Ones that come due play after the net (weather alerts still go out)")}
              checked={settings.values.net_hold_announcements}
              onChange={(event) => settings.setField("net_hold_announcements", event.currentTarget.checked)}
            />
            <Switch
              label={labeled("Hold phone patch", "A call in progress is hung up when the net starts")}
              checked={settings.values.net_hold_autopatch}
              onChange={(event) => settings.setField("net_hold_autopatch", event.currentTarget.checked)}
            />

            <Accordion variant="separated" radius="md">
              <Accordion.Item value="advanced">
                <Accordion.Control>Advanced · links &amp; announcements</Accordion.Control>
                <Accordion.Panel>
                  <Stack gap="md">
                    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                      <Select
                        label="When the net starts (AllStarLink)"
                        data={[
                          { value: "leave", label: "Leave links as they are" },
                          { value: "disconnect", label: "Disconnect all links" },
                          { value: "connect", label: "Link a node for the net" },
                        ]}
                        value={settings.values.net_links}
                        onChange={(value) => settings.setField("net_links", value || "leave")}
                        allowDeselect={false}
                      />
                      {linkNodeNeeded ? (
                        <TextInput
                          label={labeled("Node", "Dropped again when the net ends")}
                          inputMode="numeric"
                          pattern="[0-9]{0,10}"
                          placeholder="e.g. 2000"
                          value={settings.values.net_link_node}
                          onChange={(event) => settings.setField("net_link_node", event.currentTarget.value)}
                        />
                      ) : null}
                      <TextInput
                        label={labeled("When the net starts", "Spoken · blank = none")}
                        placeholder=""
                        maxLength={500}
                        value={settings.values.net_start_say}
                        onChange={(event) => settings.setField("net_start_say", event.currentTarget.value)}
                      />
                      <TextInput
                        label={labeled("When it ends", "Spoken · {name} is the net's name · blank = none")}
                        placeholder=""
                        maxLength={500}
                        value={settings.values.net_end_say}
                        onChange={(event) => settings.setField("net_end_say", event.currentTarget.value)}
                      />
                    </SimpleGrid>
                  </Stack>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>

            <ConfigActions dirty={settings.dirty} saving={settings.saving} onReset={settings.reset} />
          </Stack>
        </form>
      </ConfigEditDrawer>

      <ConfigSummaryCard
        title="Scheduled nets"
        hint="Start net mode at a set time each week."
        onEdit={() => setEditingSchedules(true)}
        rows={[
          { label: "Schedules", value: scheduleSummary(schedules) },
          ...(net?.next_scheduled
            ? [{ label: "Next", value: shortTime(net.next_scheduled * 1000) }]
            : []),
          ...schedules.slice(0, 3).map((schedule, index) => ({
            label: schedule.enabled ? `Entry ${index + 1}` : `Entry ${index + 1} (off)`,
            value: `${schedule.time || "—"} · ${daysLabel(schedule.days)} · ${schedule.minutes || "until ended"} min`,
          })),
        ]}
      />

      <ConfigEditDrawer
        opened={editingSchedules}
        onClose={() => setEditingSchedules(false)}
        title="Scheduled nets"
        subtitle="0 minutes runs until someone ends it, or the default limit."
      >
        <form onSubmit={(event) => void saveSchedules(event)}>
          <Stack gap="md">
            {schedules.map((schedule, index) => (
              <Card key={index} withBorder padding="md" radius="sm" bg="var(--mantine-color-default)">
                <Stack gap="sm">
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <TextInput
                      label="Start"
                      type="time"
                      required
                      value={schedule.time}
                      onChange={(event) => {
                        const next = [...schedules];
                        next[index] = { ...schedule, time: event.currentTarget.value };
                        setSchedules(next);
                      }}
                    />
                    <NumberInput
                      label={labeled("Minutes", "0 = until ended manually")}
                      min={0}
                      max={720}
                      required
                      value={schedule.minutes}
                      onChange={(value) => {
                        const next = [...schedules];
                        next[index] = { ...schedule, minutes: Number(value) || 0 };
                        setSchedules(next);
                      }}
                    />
                  </SimpleGrid>
                  <Text size="sm" fw={600}>
                    Days
                  </Text>
                  <Group gap="sm">
                    {DAY_NAMES.map((day, dayIndex) => (
                      <Checkbox
                        key={day}
                        label={day}
                        checked={schedule.days.includes(dayIndex)}
                        onChange={(event) => {
                          const days = event.currentTarget.checked
                            ? [...schedule.days, dayIndex].sort((a, b) => a - b)
                            : schedule.days.filter((d) => d !== dayIndex);
                          const next = [...schedules];
                          next[index] = { ...schedule, days };
                          setSchedules(next);
                        }}
                      />
                    ))}
                  </Group>
                  <Group justify="space-between">
                    <Checkbox
                      label="On"
                      checked={schedule.enabled}
                      onChange={(event) => {
                        const next = [...schedules];
                        next[index] = { ...schedule, enabled: event.currentTarget.checked };
                        setSchedules(next);
                      }}
                    />
                    <Button
                      size="compact-sm"
                      color="red"
                      variant="subtle"
                      onClick={() => setSchedules(schedules.filter((_, i) => i !== index))}
                    >
                      Remove
                    </Button>
                  </Group>
                </Stack>
              </Card>
            ))}
            {schedules.length === 0 ? (
              <Text c="dimmed" size="sm">
                No scheduled nets.
              </Text>
            ) : null}
            <Group justify="space-between">
              <Button type="button" variant="default" onClick={() => setSchedules([...schedules, emptySchedule()])}>
                Add a schedule
              </Button>
              <Button type="submit" loading={schedulesSaving}>
                Save schedules
              </Button>
            </Group>
          </Stack>
        </form>
      </ConfigEditDrawer>
    </Page>
  );
}
