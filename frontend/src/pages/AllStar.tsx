import {
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { notifyError, notifySuccess, useConfig } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const REFRESH_MS = 2000;
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type AllStarNode = { number: string; rxchannel: string; duplex: string; controlled: boolean };
type AllStarStatus = {
  available: boolean;
  manual: boolean;
  can_restart: boolean;
  restart_needed: boolean;
  error: string | null;
  nodes: AllStarNode[];
  node: string | null;
  audio: { node?: string | null; keyed?: boolean; error?: string | null };
};

type Favorite = { node: string; name: string; monitor: boolean };
type LinkSchedule = {
  node: string;
  name: string;
  days: number[];
  time: string;
  minutes: number;
  monitor: boolean;
  enabled: boolean;
};
type ScheduleStatus = { node: string; next_start?: string | null; until?: string | null };
type FavoriteInfo = { node: string; callsign?: string; description?: string };

type EchoLinkSettings = {
  callsign: string;
  name: string;
  location: string;
  email: string;
  node_number: string;
  has_password: boolean;
  lat: number;
  lon: number;
  frequency_mhz: number;
  tone_hz: number;
  max_stations: number;
  astnode: string;
  set_here: boolean;
};
type EchoLinkStatus = {
  available: boolean;
  enabled: boolean;
  loaded: boolean | null;
  error: string | null;
  settings: EchoLinkSettings | null;
};

type EchoLinkForm = {
  callsign: string;
  password: string;
  name: string;
  location: string;
  email: string;
  node_number: string;
  lat: number | "";
  lon: number | "";
  frequency_mhz: number | "";
  tone_hz: number | "";
  max_stations: number;
};

function radioName(rxchannel: string) {
  if (/^usrp\//i.test(rxchannel)) return "USRP";
  if (/^simpleusb\//i.test(rxchannel)) return "a USB sound card (SimpleUSB)";
  if (/^usbradio\//i.test(rxchannel)) return "a USB sound card (USBRadio)";
  if (/^local\/pseudo$/i.test(rxchannel)) return "no radio (a hub)";
  return rxchannel || "no radio set";
}

function pillColor(kind?: string) {
  if (kind === "on") return "green";
  if (kind === "warn") return "amber";
  return "gray";
}

function describeAllStar(status: AllStarStatus, isAdmin: boolean): { text: string; pill?: [string, string] } {
  const { audio } = status;
  if (status.manual) {
    return { text: `The node at ${audio.node || "?"} is set up by hand (MOREOPENREPEATER_USRP_NODE).` };
  }
  if (!status.available) {
    return {
      text: "AllStarLink wasn't found on this machine (no /etc/asterisk/rpt.conf). Install ASL3 with install-pi.sh --allstar, or see the setup guide to use a node elsewhere.",
      pill: ["Not installed", "off"],
    };
  }
  if (status.error) return { text: status.error, pill: ["Error", "warn"] };
  if (!status.nodes.length) return { text: "rpt.conf has no nodes yet. Set one up with sudo asl-menu." };
  if (!status.node) {
    const next = isAdmin ? "Choose the node below." : "An admin can choose one here.";
    return { text: `The repeater isn't the radio for a node yet. ${next}`, pill: ["Not linked", "off"] };
  }
  if (status.restart_needed) {
    return {
      text: `Set up as node ${status.node}. Restart Asterisk to apply it (sudo systemctl restart asterisk).`,
      pill: ["Restart needed", "warn"],
    };
  }
  if (audio.error) return { text: audio.error, pill: ["Audio error", "warn"] };
  if (audio.keyed) return { text: `The repeater is node ${status.node}'s radio. The node is transmitting.`, pill: ["Receiving", "on"] };
  return { text: `The repeater is node ${status.node}'s radio.`, pill: ["Ready", "on"] };
}

function shortTime(value: string | number) {
  const when = new Date(value);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return time;
  const farOff = when.getTime() - Date.now() > 6 * 24 * 3600 * 1000;
  const day = when.toLocaleDateString([], farOff ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "short" });
  return `${day} ${time}`;
}

function scheduleText(row: ScheduleStatus | undefined, active: string) {
  if (!row) return "";
  if (row.until) return `${active} until ${shortTime(row.until)}`;
  return row.next_start ? `Next: ${shortTime(row.next_start)}` : "";
}

function emptyFavorite(): Favorite {
  return { node: "", name: "", monitor: false };
}

function emptySchedule(): LinkSchedule {
  return { node: "", name: "", days: [], time: "", minutes: 60, monitor: false, enabled: true };
}

function emptyEchoForm(): EchoLinkForm {
  return {
    callsign: "",
    password: "",
    name: "",
    location: "",
    email: "",
    node_number: "",
    lat: "",
    lon: "",
    frequency_mhz: "",
    tone_hz: "",
    max_stations: 20,
  };
}

function describeEcho(status: EchoLinkStatus, node: string | null, isAdmin: boolean): { text: string; pill: [string, string] } {
  if (status.error) return { text: status.error, pill: ["Error", "warn"] };
  const settings = status.settings;
  if (!status.enabled) {
    const next = !node
      ? "Choose the repeater's node above first."
      : isAdmin
        ? "Fill in the station's details to turn it on."
        : "An admin can turn it on here.";
    return { text: `EchoLink is off. ${next}`, pill: ["Off", "off"] };
  }
  if (!settings) return { text: "EchoLink is on.", pill: ["On", "on"] };
  const station = `${settings.callsign || "?"} (node ${settings.node_number || "?"})`;
  if (status.loaded === false) {
    return {
      text: `EchoLink is set up as ${station} but Asterisk hasn't loaded its driver. Check the Asterisk log (sudo journalctl -u asterisk).`,
      pill: ["Not loaded", "warn"],
    };
  }
  if (node && settings.astnode && settings.astnode !== node) {
    return {
      text: `EchoLink is on as ${station}, but connects to node ${settings.astnode}. Save to move it to node ${node}.`,
      pill: ["On", "warn"],
    };
  }
  return { text: `EchoLink is on as ${station}. EchoLink users connect to it like any AllStar link.`, pill: ["On", "on"] };
}

export function AllStar() {
  const { session } = useSession();
  const { config, save: saveConfig } = useConfig();
  const isAdmin = session?.role === "admin";

  const [status, setStatus] = useState<AllStarStatus | null>(null);
  const [selectedNode, setSelectedNode] = useState("");
  const [nodeBusy, setNodeBusy] = useState(false);

  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [favoritesSaving, setFavoritesSaving] = useState(false);
  const [known, setKnown] = useState<Map<string, FavoriteInfo>>(new Map());

  const [schedules, setSchedules] = useState<LinkSchedule[]>([]);
  const [scheduleStatus, setScheduleStatus] = useState<ScheduleStatus[]>([]);
  const [schedulesSaving, setSchedulesSaving] = useState(false);

  const [echo, setEcho] = useState<EchoLinkStatus | null>(null);
  const [echoForm, setEchoForm] = useState<EchoLinkForm>(emptyEchoForm());
  const [echoFilled, setEchoFilled] = useState(false);
  const [echoEdited, setEchoEdited] = useState(false);
  const [echoBusy, setEchoBusy] = useState(false);

  const refresh = useCallback(async () => {
    const next = await api<AllStarStatus>("/api/allstar");
    setStatus(next);
    setSelectedNode((prev) => {
      if (next.node && next.nodes.some((n) => n.number === next.node)) return next.node;
      if (prev && next.nodes.some((n) => n.number === prev)) return prev;
      return next.nodes[0]?.number ?? "";
    });
  }, []);

  const refreshEcho = useCallback(async () => {
    setEcho(await api<EchoLinkStatus>("/api/allstar/echolink"));
  }, []);

  const refreshLinks = useCallback(async () => {
    const links = await api<{ favorites: FavoriteInfo[]; schedules: ScheduleStatus[] }>("/api/links");
    setKnown(new Map(links.favorites.map((f) => [f.node, f])));
    setScheduleStatus(links.schedules ?? []);
  }, []);

  useEffect(() => {
    refresh().catch(notifyError);
    refreshEcho().catch(notifyError);
    refreshLinks().catch(() => {});
  }, [refresh, refreshEcho, refreshLinks]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (!document.hidden) refresh().catch(() => {});
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const list = (config?.link_favorites as Favorite[] | undefined) ?? [];
    setFavorites(list.map((f) => ({ ...f })));
  }, [config]);

  useEffect(() => {
    const list = (config?.link_schedules as LinkSchedule[] | undefined) ?? [];
    setSchedules(list.map((s) => ({ ...s, days: [...(s.days ?? [])] })));
  }, [config]);

  useEffect(() => {
    if (!echo?.settings || echoFilled || echoEdited) return;
    const settings = echo.settings;
    const tone = (config?.aprs_tone_hz as number | undefined) ?? (config?.require_ctcss_hz as number | undefined);
    const pick = <T,>(value: T, empty: T, fallback: T | "" | undefined) =>
      value === empty || value == null ? (fallback ?? ("" as T)) : value;
    setEchoForm({
      callsign: pick(settings.callsign, "", config?.callsign ? `${String(config.callsign).toUpperCase()}-R` : ""),
      password: "",
      name: pick(settings.name, "", ""),
      location: pick(settings.location, "", ""),
      email: pick(settings.email, "", ""),
      node_number: pick(settings.node_number, "", ""),
      lat: pick(settings.lat, 0, (config?.aprs_lat as number | undefined) ?? ""),
      lon: pick(settings.lon, 0, (config?.aprs_lon as number | undefined) ?? ""),
      frequency_mhz: pick(settings.frequency_mhz, 0, (config?.aprs_frequency_mhz as number | undefined) ?? ""),
      tone_hz: pick(settings.tone_hz, 0, tone ?? ""),
      max_stations: settings.max_stations || 20,
    });
    setEchoFilled(true);
  }, [echo, echoFilled, echoEdited, config]);

  useEffect(() => {
    if (echo?.settings && !echoEdited) setEchoFilled(false);
  }, [config]); // eslint-disable-line react-hooks/exhaustive-deps -- refill EchoLink defaults when station config changes

  const selected = status?.nodes.find((n) => n.number === selectedNode);
  const view = status ? describeAllStar(status, isAdmin) : null;
  const showNodeForm = isAdmin && status?.available && !status.error && (status.nodes.length ?? 0) > 0;
  const echoView = echo ? describeEcho(echo, status?.node ?? null, isAdmin) : null;
  const showEcho = Boolean(echo && (echo.available || echo.error));

  const bindNode = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedNode) return;
    const other = status?.node && status.node !== selectedNode ? ` Node ${status.node} gets its old radio back.` : "";
    if (
      !window.confirm(
        `Make the repeater node ${selectedNode}'s radio? Asterisk restarts, dropping links for a few seconds.${other}`,
      )
    ) {
      return;
    }
    setNodeBusy(true);
    try {
      setStatus(await api<AllStarStatus>("/api/allstar", { method: "PUT", json: { node: selectedNode } }));
      notifySuccess(`The repeater is node ${selectedNode}'s radio`);
      refreshEcho().catch(() => {});
    } catch (error) {
      notifyError(error);
      refresh().catch(() => {});
    } finally {
      setNodeBusy(false);
    }
  };

  const releaseNode = async () => {
    if (!status?.node) return;
    if (!window.confirm(`Give node ${status.node} its old radio back? Asterisk restarts, and the repeater stops linking.`)) {
      return;
    }
    setNodeBusy(true);
    try {
      setStatus(await api<AllStarStatus>("/api/allstar", { method: "DELETE" }));
      notifySuccess("Node settings put back");
    } catch (error) {
      notifyError(error);
      refresh().catch(() => {});
    } finally {
      setNodeBusy(false);
    }
  };

  const lookupText = (node: string) => {
    const info = known.get(node);
    if (!info) return "";
    return [info.callsign, info.description].filter(Boolean).join(" · ");
  };

  const saveFavorites = async (event: FormEvent) => {
    event.preventDefault();
    setFavoritesSaving(true);
    try {
      await saveConfig({ link_favorites: favorites });
      await refreshLinks();
      notifySuccess("Favorites saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setFavoritesSaving(false);
    }
  };

  const saveSchedules = async (event: FormEvent) => {
    event.preventDefault();
    if (schedules.some((schedule) => schedule.days.length === 0)) {
      notifyError(new Error("Pick at least one day for each schedule."));
      return;
    }
    setSchedulesSaving(true);
    try {
      await saveConfig({ link_schedules: schedules });
      await refreshLinks();
      notifySuccess("Schedules saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setSchedulesSaving(false);
    }
  };

  const setEchoField = <K extends keyof EchoLinkForm>(name: K, value: EchoLinkForm[K]) => {
    setEchoForm((prev) => ({ ...prev, [name]: value }));
    setEchoEdited(true);
  };

  const saveEcho = async (event: FormEvent) => {
    event.preventDefault();
    const body = {
      callsign: echoForm.callsign.trim().toUpperCase(),
      password: echoForm.password,
      name: echoForm.name.trim(),
      location: echoForm.location.trim(),
      email: echoForm.email.trim(),
      node_number: echoForm.node_number.trim(),
      lat: echoForm.lat === "" ? 0 : Number(echoForm.lat),
      lon: echoForm.lon === "" ? 0 : Number(echoForm.lon),
      frequency_mhz: echoForm.frequency_mhz === "" ? 0 : Number(echoForm.frequency_mhz),
      tone_hz: echoForm.tone_hz === "" ? 0 : Number(echoForm.tone_hz),
      max_stations: Number(echoForm.max_stations) || 20,
    };
    if (!window.confirm(`Save EchoLink as ${body.callsign}? Asterisk restarts, dropping links for a few seconds.`)) return;
    setEchoBusy(true);
    try {
      const next = await api<EchoLinkStatus>("/api/allstar/echolink", { method: "PUT", json: body });
      setEcho(next);
      setEchoFilled(false);
      setEchoEdited(false);
      notifySuccess(next.loaded === false ? "Saved, but Asterisk didn't load EchoLink" : `EchoLink is on as ${body.callsign}`);
    } catch (error) {
      notifyError(error);
    } finally {
      setEchoBusy(false);
    }
  };

  const turnOffEcho = async () => {
    if (!window.confirm("Turn off EchoLink? Asterisk restarts, and EchoLink stations are disconnected. The details are kept.")) {
      return;
    }
    setEchoBusy(true);
    try {
      setEcho(await api<EchoLinkStatus>("/api/allstar/echolink", { method: "DELETE" }));
      setEchoFilled(false);
      setEchoEdited(false);
      notifySuccess("EchoLink turned off");
    } catch (error) {
      notifyError(error);
    } finally {
      setEchoBusy(false);
    }
  };

  return (
    <Page
      title="AllStarLink"
      description="Link the repeater to other AllStarLink nodes. The controller becomes the radio of a node in the AllStarLink (ASL3) install beside it."
    >
      <Card withBorder padding="lg" radius="md">
        <Group justify="space-between" align="flex-start" mb="sm">
          <Title order={2}>Node</Title>
          {view?.pill ? (
            <Badge color={pillColor(view.pill[1])} variant="light">
              {view.pill[0]}
            </Badge>
          ) : null}
        </Group>
        <Text c="dimmed" size="sm">
          {view?.text ?? "Loading…"}
        </Text>
        {showNodeForm ? (
          <form onSubmit={(event) => void bindNode(event)}>
            <Stack gap="md" mt="md">
              <Select
                label="Node"
                description={
                  selected
                    ? selected.controlled
                      ? "The repeater is its radio."
                      : `Its radio is ${radioName(selected.rxchannel)} now.`
                    : undefined
                }
                data={status!.nodes.map((n) => ({ value: n.number, label: n.number }))}
                value={selectedNode || null}
                onChange={(value) => setSelectedNode(value || "")}
                allowDeselect={false}
                maw={280}
              />
              <Group justify="space-between">
                {status?.node ? (
                  <Button type="button" variant="subtle" color="gray" loading={nodeBusy} onClick={() => void releaseNode()}>
                    Stop using this node
                  </Button>
                ) : (
                  <span />
                )}
                <Button type="submit" loading={nodeBusy}>
                  {selected?.controlled ? "Set up again" : "Use this node"}
                </Button>
              </Group>
              <Text c="dimmed" size="sm">
                This changes the node&apos;s <Code>rxchannel</Code>, <Code>duplex</Code> and hang time in <Code>rpt.conf</Code>,
                turns on Asterisk&apos;s USRP channel, and restarts Asterisk.
              </Text>
            </Stack>
          </form>
        ) : null}
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void saveFavorites(event)}>
        <Stack gap="md">
          <Title order={2}>Favorite nodes</Title>
          <Text c="dimmed" size="sm" maw="75ch">
            One-click buttons on the dashboard&apos;s Links card. EchoLink nodes are 3 and the EchoLink number padded to six digits (
            <Code>3009999</Code> for 9999).
          </Text>
          <Table.ScrollContainer minWidth={480}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Node</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Monitor only</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {favorites.map((favorite, index) => (
                  <Table.Tr key={index}>
                    <Table.Td>
                      <TextInput
                        required
                        inputMode="numeric"
                        pattern="[0-9]{1,10}"
                        placeholder="2000"
                        value={favorite.node}
                        onChange={(event) => {
                          const next = [...favorites];
                          next[index] = { ...favorite, node: event.currentTarget.value };
                          setFavorites(next);
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <TextInput
                        maxLength={40}
                        placeholder={lookupText(favorite.node) || "Name"}
                        value={favorite.name}
                        onChange={(event) => {
                          const next = [...favorites];
                          next[index] = { ...favorite, name: event.currentTarget.value };
                          setFavorites(next);
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Checkbox
                        aria-label="Monitor only"
                        checked={favorite.monitor}
                        onChange={(event) => {
                          const next = [...favorites];
                          next[index] = { ...favorite, monitor: event.currentTarget.checked };
                          setFavorites(next);
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Button
                        size="compact-sm"
                        color="red"
                        variant="subtle"
                        onClick={() => setFavorites(favorites.filter((_, i) => i !== index))}
                      >
                        Remove
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {favorites.length === 0 ? (
            <Text c="dimmed" size="sm">
              No favorites yet.
            </Text>
          ) : null}
          <Group justify="space-between">
            <Button type="button" variant="subtle" onClick={() => setFavorites([...favorites, emptyFavorite()])}>
              Add a node
            </Button>
            <Button type="submit" loading={favoritesSaving}>
              Save favorites
            </Button>
          </Group>
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void saveSchedules(event)}>
        <Stack gap="md">
          <Title order={2}>Scheduled links</Title>
          <Text c="dimmed" size="sm" maw="75ch">
            Link a node at a set time each week and drop the link after the given number of minutes (0 keeps it until someone
            disconnects it).
          </Text>
          {schedules.map((schedule, index) => (
            <Card key={index} withBorder padding="md" radius="sm" bg="var(--mantine-color-default)">
              <Stack gap="sm">
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <TextInput
                    label="Node"
                    required
                    inputMode="numeric"
                    pattern="[0-9]{1,10}"
                    placeholder="2000"
                    value={schedule.node}
                    onChange={(event) => {
                      const next = [...schedules];
                      next[index] = { ...schedule, node: event.currentTarget.value };
                      setSchedules(next);
                    }}
                  />
                  <TextInput
                    label="Name"
                    maxLength={40}
                    placeholder="Tuesday net"
                    value={schedule.name}
                    onChange={(event) => {
                      const next = [...schedules];
                      next[index] = { ...schedule, name: event.currentTarget.value };
                      setSchedules(next);
                    }}
                  />
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
                    label="Minutes linked"
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
                <Group justify="space-between" wrap="wrap">
                  <Group gap="md">
                    <Checkbox
                      label="Monitor only"
                      checked={schedule.monitor}
                      onChange={(event) => {
                        const next = [...schedules];
                        next[index] = { ...schedule, monitor: event.currentTarget.checked };
                        setSchedules(next);
                      }}
                    />
                    <Checkbox
                      label="On"
                      checked={schedule.enabled}
                      onChange={(event) => {
                        const next = [...schedules];
                        next[index] = { ...schedule, enabled: event.currentTarget.checked };
                        setSchedules(next);
                      }}
                    />
                  </Group>
                  <Group gap="sm">
                    <Text size="sm" c="dimmed">
                      {scheduleText(scheduleStatus[index], "Linked")}
                    </Text>
                    <Button
                      size="compact-sm"
                      color="red"
                      variant="subtle"
                      onClick={() => setSchedules(schedules.filter((_, i) => i !== index))}
                    >
                      Remove
                    </Button>
                  </Group>
                </Group>
              </Stack>
            </Card>
          ))}
          {schedules.length === 0 ? (
            <Text c="dimmed" size="sm">
              No scheduled links.
            </Text>
          ) : null}
          <Group justify="space-between">
            <Button type="button" variant="subtle" onClick={() => setSchedules([...schedules, emptySchedule()])}>
              Add a schedule
            </Button>
            <Button type="submit" loading={schedulesSaving}>
              Save schedules
            </Button>
          </Group>
        </Stack>
      </Card>

      {showEcho ? (
        <Card withBorder padding="lg" radius="md">
          <Group justify="space-between" align="flex-start" mb="sm">
            <Title order={2}>EchoLink</Title>
            {echoView?.pill ? (
              <Badge color={pillColor(echoView.pill[1])} variant="light">
                {echoView.pill[0]}
              </Badge>
            ) : null}
          </Group>
          <Text c="dimmed" size="sm">
            {echoView?.text}
          </Text>
          {isAdmin && echo?.settings ? (
            <form onSubmit={(event) => void saveEcho(event)}>
              <Stack gap="md" mt="md">
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <TextInput
                    label="Callsign"
                    description="With -R for a repeater, as validated by EchoLink."
                    required
                    value={echoForm.callsign}
                    onChange={(event) => setEchoField("callsign", event.currentTarget.value)}
                  />
                  <TextInput
                    label="Password"
                    type="password"
                    autoComplete="new-password"
                    required={!echo.settings.has_password}
                    placeholder={echo.settings.has_password ? "Saved (leave blank to keep it)" : undefined}
                    value={echoForm.password}
                    onChange={(event) => setEchoField("password", event.currentTarget.value)}
                  />
                  <TextInput
                    label="EchoLink node number"
                    required
                    inputMode="numeric"
                    value={echoForm.node_number}
                    onChange={(event) => setEchoField("node_number", event.currentTarget.value)}
                  />
                  <TextInput
                    label="Sysop name"
                    required
                    maxLength={40}
                    value={echoForm.name}
                    onChange={(event) => setEchoField("name", event.currentTarget.value)}
                  />
                  <TextInput
                    label="Location"
                    required
                    maxLength={40}
                    placeholder="e.g. Newington, CT"
                    value={echoForm.location}
                    onChange={(event) => setEchoField("location", event.currentTarget.value)}
                  />
                  <TextInput
                    label="Email"
                    type="email"
                    required
                    maxLength={80}
                    value={echoForm.email}
                    onChange={(event) => setEchoField("email", event.currentTarget.value)}
                  />
                  <NumberInput
                    label="Latitude"
                    min={-90}
                    max={90}
                    decimalScale={4}
                    value={echoForm.lat === "" ? undefined : echoForm.lat}
                    onChange={(value) => setEchoField("lat", value === "" || value === undefined ? "" : Number(value))}
                  />
                  <NumberInput
                    label="Longitude"
                    min={-180}
                    max={180}
                    decimalScale={4}
                    value={echoForm.lon === "" ? undefined : echoForm.lon}
                    onChange={(value) => setEchoField("lon", value === "" || value === undefined ? "" : Number(value))}
                  />
                  <NumberInput
                    label="Frequency"
                    suffix=" MHz"
                    min={0}
                    max={3000}
                    value={echoForm.frequency_mhz === "" ? undefined : echoForm.frequency_mhz}
                    onChange={(value) =>
                      setEchoField("frequency_mhz", value === "" || value === undefined ? "" : Number(value))
                    }
                  />
                  <NumberInput
                    label="Tone"
                    description="0 for none."
                    suffix=" Hz"
                    min={0}
                    max={300}
                    decimalScale={1}
                    value={echoForm.tone_hz === "" ? undefined : echoForm.tone_hz}
                    onChange={(value) => setEchoField("tone_hz", value === "" || value === undefined ? "" : Number(value))}
                  />
                  <NumberInput
                    label="Most stations at once"
                    min={1}
                    max={100}
                    required
                    value={echoForm.max_stations}
                    onChange={(value) => setEchoField("max_stations", Number(value) || 20)}
                  />
                </SimpleGrid>
                <Group justify="space-between">
                  {echo.enabled ? (
                    <Button type="button" variant="subtle" color="gray" loading={echoBusy} onClick={() => void turnOffEcho()}>
                      Turn off EchoLink
                    </Button>
                  ) : (
                    <span />
                  )}
                  <Button type="submit" loading={echoBusy} disabled={!status?.node}>
                    {echo.enabled ? "Save" : "Save and turn on"}
                  </Button>
                </Group>
              </Stack>
            </form>
          ) : null}
        </Card>
      ) : null}
    </Page>
  );
}
