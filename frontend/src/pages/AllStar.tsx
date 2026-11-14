import {
  Accordion,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Grid,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ActionsHeader } from "../components/ActionsHeader";
import { ConfigEditDrawer } from "../components/ConfigEditDrawer";
import { EmptyState } from "../components/EmptyState";
import { ErrorCard, LoadingBlock } from "../components/StateCards";
import { labeled } from "../components/FieldHint";
import { notifyError, notifySuccess, useConfig } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { ConnectBar } from "./allstar/ConnectBar";
import { LinkMap } from "./allstar/LinkMap";
import { ScheduleWeek } from "./allstar/ScheduleWeek";
import { SetupChecklist } from "./allstar/SetupChecklist";
import { isReady, recentNodes, setupSteps, type LinkEvent, type LinkRow } from "./allstar/model";
import "./allstar/allstar.css";

/** Extract a user-facing message from any error object. */
function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === "object" && "message" in error) return String((error as { message: unknown }).message);
  return String(error);
}

/** Background status/links refresh — was 2 s; too hot for on-Pi Chromium. */
const REFRESH_MS = 10_000;
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const ALLSTAR_TABS = ["links", "schedule", "node"];

type AllStarNode = { number: string; rxchannel: string; duplex: string; controlled: boolean };
type AllStarIdentity = { node: string; callsign: string; has_password: boolean };
type AllStarStatus = {
  available: boolean;
  manual: boolean;
  can_restart: boolean;
  restart_needed: boolean;
  error: string | null;
  nodes: AllStarNode[];
  node: string | null;
  identity?: AllStarIdentity | null;
  audio: { node?: string | null; keyed?: boolean; error?: string | null };
};

/** Persistent inline alert for AllStar setup errors — stays until dismissed. */
type ErrorAlert = { message: string; dismissed: boolean };

type IdentityForm = { fromNode: string; node: string; callsign: string; password: string };

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
    const placeholder = status.nodes.length === 1 && status.nodes[0]?.number === "1999";
    const next = isAdmin
      ? placeholder
        ? "ASL3 ships with placeholder node 1999 — run sudo asl-menu for your real node number, callsign and password, then choose it below."
        : "Choose this machine's AllStarLink node below (from rpt.conf). Favorite nodes below are for linking out, not this list."
      : "An admin can choose one here.";
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
  const canWrite = session?.role !== "viewer";
  const [tab, setTab] = useState<string>(() => {
    const fromHash = window.location.hash.slice(1);
    return ALLSTAR_TABS.includes(fromHash) ? fromHash : "links";
  });
  const changeTab = (value: string | null) => {
    if (!value) return;
    setTab(value);
    window.history.replaceState(null, "", `#${value}`);
  };
  const [linkRows, setLinkRows] = useState<LinkRow[]>([]);
  const [linkEvents, setLinkEvents] = useState<LinkEvent[]>([]);

  const [status, setStatus] = useState<AllStarStatus | null>(null);
  const [selectedNode, setSelectedNode] = useState("");
  const [nodeBusy, setNodeBusy] = useState(false);
  const [identityForm, setIdentityForm] = useState<IdentityForm>({ fromNode: "", node: "", callsign: "", password: "" });
  const [identityFilled, setIdentityFilled] = useState(false);
  const [identityEdited, setIdentityEdited] = useState(false);
  const [identityBusy, setIdentityBusy] = useState(false);
  const [errorAlert, setErrorAlert] = useState<ErrorAlert | null>(null);

  const [favoriteDraft, setFavoriteDraft] = useState<Favorite[]>([]);
  const [savedFavorites, setSavedFavorites] = useState<Favorite[]>([]);
  const [favoritesSaving, setFavoritesSaving] = useState(false);

  const [scheduleDraft, setScheduleDraft] = useState<LinkSchedule[]>([]);
  const [savedSchedules, setSavedSchedules] = useState<LinkSchedule[]>([]);
  const [scheduleStatus, setScheduleStatus] = useState<ScheduleStatus[]>([]);
  const [schedulesSaving, setSchedulesSaving] = useState(false);

  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusStale, setStatusStale] = useState(false);
  const [linkBusy, setLinkBusy] = useState(false);

  const [echoLoading, setEchoLoading] = useState(true);
  const [echoError, setEchoError] = useState<string | null>(null);
  const [echoStale, setEchoStale] = useState(false);

  const [linksLoading, setLinksLoading] = useState(true);
  const [linksError, setLinksError] = useState<string | null>(null);
  const [linksStale, setLinksStale] = useState(false);

  const echoReadVersionRef = useRef(0);
  const echoMutationIdRef = useRef(0);
  const linksReadVersionRef = useRef(0);
  const linksMutationIdRef = useRef(0);

  const [echo, setEcho] = useState<EchoLinkStatus | null>(null);
  const [echoForm, setEchoForm] = useState<EchoLinkForm>(emptyEchoForm());
  const [echoFilled, setEchoFilled] = useState(false);
  const [echoEdited, setEchoEdited] = useState(false);
  const [echoSnapshot, setEchoSnapshot] = useState<string | null>(null);
  const [echoBusy, setEchoBusy] = useState(false);

  const [editingNode, setEditingNode] = useState(false);
  const [editingFavorites, setEditingFavorites] = useState(false);
  const [editingSchedules, setEditingSchedules] = useState(false);
  const [editingEcho, setEditingEcho] = useState(false);

  const refresh = useCallback(async () => {
    setStatusLoading(true);
    try {
      const next = await api<AllStarStatus>("/api/allstar");
      setStatus(next);
      setStatusError(null);
      setStatusStale(false);
      setSelectedNode((prev) => {
        if (next.node && next.nodes.some((n) => n.number === next.node)) return next.node;
        if (prev && next.nodes.some((n) => n.number === prev)) return prev;
        return next.nodes[0]?.number ?? "";
      });
      return next;
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : String(error));
      setStatusStale(true);
      throw error;
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const refreshEcho = useCallback(async () => {
    const version = ++echoReadVersionRef.current;
    setEchoLoading(true);
    try {
      const next = await api<EchoLinkStatus>("/api/allstar/echolink");
      if (version !== echoReadVersionRef.current) return next;
      if (echoMutationIdRef.current !== 0) return next;
      setEcho(next);
      setEchoError(null);
      setEchoStale(false);
      return next;
    } catch (error) {
      if (version !== echoReadVersionRef.current) throw error;
      if (echoMutationIdRef.current !== 0) throw error;
      setEchoError(error instanceof Error ? error.message : String(error));
      setEchoStale(true);
      throw error;
    } finally {
      if (version === echoReadVersionRef.current) setEchoLoading(false);
    }
  }, []);

  const applyLinksResponse = useCallback((links: { favorites: FavoriteInfo[]; schedules: ScheduleStatus[]; links?: LinkRow[]; error?: string | null }, version: number) => {
    if (version !== linksReadVersionRef.current) return false;
    if (linksMutationIdRef.current !== 0) return false;
    if (links.error) {
      setLinksError(links.error);
      setLinksStale(true);
      return false;
    }
    setScheduleStatus(links.schedules ?? []);
    setLinkRows(links.links ?? []);
    setLinksError(null);
    setLinksStale(false);
    return true;
  }, []);

  const refreshLinks = useCallback(async () => {
    const version = ++linksReadVersionRef.current;
    try {
      const links = await api<{ favorites: FavoriteInfo[]; schedules: ScheduleStatus[]; links?: LinkRow[]; error?: string | null }>(
        "/api/links",
      );
      if (version !== linksReadVersionRef.current) return links;
      if (linksMutationIdRef.current !== 0) return links;
      if (links.error) throw new Error(links.error);
      applyLinksResponse(links, version);
      return links;
    } catch (error) {
      if (version !== linksReadVersionRef.current) throw error;
      if (linksMutationIdRef.current !== 0) throw error;
      setLinksError(error instanceof Error ? error.message : String(error));
      setLinksStale(true);
      throw error;
    } finally {
      if (version === linksReadVersionRef.current) setLinksLoading(false);
    }
  }, [applyLinksResponse]);

  const refreshLinkEvents = useCallback(async () => {
    const data = await api<{ events: LinkEvent[] }>("/api/links/events?limit=30");
    setLinkEvents(data.events ?? []);
  }, []);

  const connectNode = async (target: string, monitorOnly: boolean) => {
    if (linksMutationIdRef.current !== 0) return;
    const mutationId = ++linksMutationIdRef.current;
    ++linksReadVersionRef.current;
    setLinkBusy(true);
    try {
      const next = await api<{ links?: LinkRow[]; schedules?: ScheduleStatus[]; error?: string | null }>("/api/links", { method: "POST", json: { node: target, monitor: monitorOnly } });
      if (mutationId !== linksMutationIdRef.current) return;
      if (next.error) {
        setLinksError(next.error);
        setLinksStale(true);
        notifyError(new Error(next.error));
      } else {
        setScheduleStatus(next.schedules ?? []);
        setLinkRows(next.links ?? []);
        setLinksError(null);
        setLinksStale(false);
        notifySuccess(`Connecting node ${target}`);
      }
    } catch (error) {
      if (mutationId !== linksMutationIdRef.current) return;
      setLinksError(error instanceof Error ? error.message : String(error));
      setLinksStale(true);
      notifyError(error);
    } finally {
      if (mutationId === linksMutationIdRef.current) {
        setLinkBusy(false);
        linksMutationIdRef.current = 0;
        ++linksReadVersionRef.current;
        void refreshLinks().catch(() => {});
      }
    }
  };

  const disconnectNode = async (target: string) => {
    if (linksMutationIdRef.current !== 0) return;
    const mutationId = ++linksMutationIdRef.current;
    ++linksReadVersionRef.current;
    setLinkBusy(true);
    try {
      const next = await api<{ links?: LinkRow[]; schedules?: ScheduleStatus[]; error?: string | null }>(`/api/links/${encodeURIComponent(target)}`, { method: "DELETE" });
      if (mutationId !== linksMutationIdRef.current) return;
      if (next.error) {
        setLinksError(next.error);
        setLinksStale(true);
        notifyError(new Error(next.error));
      } else {
        setScheduleStatus(next.schedules ?? []);
        setLinkRows(next.links ?? []);
        setLinksError(null);
        setLinksStale(false);
        notifySuccess(`Disconnecting node ${target}`);
      }
    } catch (error) {
      if (mutationId !== linksMutationIdRef.current) return;
      setLinksError(error instanceof Error ? error.message : String(error));
      setLinksStale(true);
      notifyError(error);
    } finally {
      if (mutationId === linksMutationIdRef.current) {
        setLinkBusy(false);
        linksMutationIdRef.current = 0;
        ++linksReadVersionRef.current;
        void refreshLinks().catch(() => {});
      }
    }
  };

  const disconnectAll = async () => {
    if (!window.confirm("Disconnect every link?")) return;
    if (linksMutationIdRef.current !== 0) return;
    const mutationId = ++linksMutationIdRef.current;
    ++linksReadVersionRef.current;
    setLinkBusy(true);
    try {
      const next = await api<{ links?: LinkRow[]; schedules?: ScheduleStatus[]; error?: string | null }>("/api/links", { method: "DELETE" });
      if (mutationId !== linksMutationIdRef.current) return;
      if (next.error) {
        setLinksError(next.error);
        setLinksStale(true);
        notifyError(new Error(next.error));
      } else {
        setScheduleStatus(next.schedules ?? []);
        setLinkRows(next.links ?? []);
        setLinksError(null);
        setLinksStale(false);
        notifySuccess("Disconnecting all links");
      }
    } catch (error) {
      if (mutationId !== linksMutationIdRef.current) return;
      setLinksError(error instanceof Error ? error.message : String(error));
      setLinksStale(true);
      notifyError(error);
    } finally {
      if (mutationId === linksMutationIdRef.current) {
        setLinkBusy(false);
        linksMutationIdRef.current = 0;
        ++linksReadVersionRef.current;
        void refreshLinks().catch(() => {});
      }
    }
  };

  useEffect(() => {
    refresh().catch((error) => {
      setStatusStale(true);
      notifyError(error);
    });
    refreshEcho().catch(notifyError);
    refreshLinks().catch(() => {});
    refreshLinkEvents().catch(() => {});
  }, [refresh, refreshEcho, refreshLinks, refreshLinkEvents]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.hidden) return;
      refresh()
        .then(() => setStatusStale(false))
        .catch(() => setStatusStale(true));
      refreshEcho().catch(() => {});
      refreshLinks().catch(() => {});
      refreshLinkEvents().catch(() => {});
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh, refreshEcho, refreshLinks, refreshLinkEvents]);

  useEffect(() => {
    const list = (config?.link_favorites as Favorite[] | undefined) ?? [];
    setSavedFavorites(list.map((f) => ({ ...f })));
  }, [config]);

  useEffect(() => {
    const list = (config?.link_schedules as LinkSchedule[] | undefined) ?? [];
    setSavedSchedules(list.map((s) => ({ ...s, days: [...(s.days ?? [])] })));
  }, [config]);

  const savedSchedulesKey = JSON.stringify(savedSchedules);
  const previousSchedulesKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (previousSchedulesKeyRef.current !== null && previousSchedulesKeyRef.current !== savedSchedulesKey) {
      setScheduleStatus([]);
      setLinksStale(true);
    }
    previousSchedulesKeyRef.current = savedSchedulesKey;
  }, [savedSchedulesKey]);

  useEffect(() => {
    if (!echo?.settings || echoEdited) return;
    const snapshot = JSON.stringify(echo.settings);
    if (echoFilled && snapshot === echoSnapshot) return;
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
    setEchoSnapshot(snapshot);
    setEchoFilled(true);
  }, [echo, echoFilled, echoEdited, echoSnapshot, config]);

  useEffect(() => {
    if (echo?.settings && !echoEdited) {
      setEchoFilled(false);
      setEchoSnapshot(null);
    }
  }, [config]); // eslint-disable-line react-hooks/exhaustive-deps -- refill EchoLink defaults when station config changes

  useEffect(() => {
    if (!status?.available || identityEdited) return;
    const focus = status.identity?.node || status.node || status.nodes[0]?.number || "";
    if (!focus && identityFilled) return;
    setIdentityForm({
      fromNode: focus,
      node: focus === "1999" ? "" : focus,
      callsign: status.identity?.callsign || (config?.callsign ? String(config.callsign).toUpperCase() : ""),
      password: "",
    });
    setIdentityFilled(true);
  }, [status, config, identityEdited, identityFilled]);

  const openFavoritesEditor = () => {
    setFavoriteDraft(savedFavorites.map((f) => ({ ...f })));
    setEditingFavorites(true);
  };
  const openSchedulesEditor = () => {
    setScheduleDraft(savedSchedules.map((s) => ({ ...s, days: [...(s.days ?? [])] })));
    setEditingSchedules(true);
  };

  const selected = status?.nodes.find((n) => n.number === selectedNode);
  const view = status ? describeAllStar(status, isAdmin) : null;
  const showNodeForm = isAdmin && status?.available && !status.error && (status.nodes.length ?? 0) > 0;
  const showIdentityForm = isAdmin && status?.available && !status.error && (status.nodes.length ?? 0) > 0;
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
      setErrorAlert(null);
      notifySuccess(`The repeater is node ${selectedNode}'s radio`);
      setEditingNode(false);
      refreshEcho().catch(() => {});
    } catch (error) {
      const detail = getErrorMessage(error);
      setErrorAlert({ message: detail, dismissed: false });
      notifications.show({
        color: "red",
        message: detail,
        autoClose: false,
      });
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
      setErrorAlert(null);
      notifySuccess("Node settings put back");
      setEditingNode(false);
    } catch (error) {
      const detail = getErrorMessage(error);
      setErrorAlert({ message: detail, dismissed: false });
      notifications.show({
        color: "red",
        message: detail,
        autoClose: false,
      });
      refresh().catch(() => {});
    } finally {
      setNodeBusy(false);
    }
  };

  const setIdentityField = <K extends keyof IdentityForm>(name: K, value: IdentityForm[K]) => {
    setIdentityForm((prev) => ({ ...prev, [name]: value }));
    setIdentityEdited(true);
  };

  const saveIdentity = async (event: FormEvent) => {
    event.preventDefault();
    const fromNode = identityForm.fromNode || selectedNode;
    const node = identityForm.node.trim();
    const callsign = identityForm.callsign.trim().toUpperCase();
    if (!fromNode || !node || !callsign) return;
    const needsPassword = Number(node) >= 2000 && !status?.identity?.has_password && !identityForm.password;
    if (needsPassword) {
      notifyError(new Error("Enter the node password from the AllStarLink portal."));
      return;
    }
    if (
      !window.confirm(
        `Save AllStarLink node ${node} (${callsign})? This updates ASL3's config and restarts Asterisk, dropping links for a few seconds.`,
      )
    ) {
      return;
    }
    setIdentityBusy(true);
    try {
      const next = await api<AllStarStatus>("/api/allstar/identity", {
        method: "PUT",
        json: {
          from_node: fromNode,
          node,
          callsign,
          password: identityForm.password,
        },
      });
      setStatus(next);
      setSelectedNode(node);
      setIdentityEdited(false);
      setIdentityFilled(false);
      setIdentityForm((prev) => ({ ...prev, password: "" }));
      setErrorAlert(null);
      notifySuccess(`Node ${node} is set up and the repeater is its radio`);
      setEditingNode(false);
      refreshEcho().catch(() => {});
    } catch (error) {
      const detail = getErrorMessage(error);
      setErrorAlert({ message: detail, dismissed: false });
      notifications.show({
        color: "red",
        message: detail,
        autoClose: false,
      });
      refresh().catch(() => {});
    } finally {
      setIdentityBusy(false);
    }
  };

  const saveFavorites = async (event: FormEvent) => {
    event.preventDefault();
    setFavoritesSaving(true);
    try {
      await saveConfig({ link_favorites: favoriteDraft });
      await refreshLinks();
      notifySuccess("Favorites saved");
      setFavoriteDraft([]);
      setEditingFavorites(false);
    } catch (error) {
      notifyError(error);
    } finally {
      setFavoritesSaving(false);
    }
  };

  const saveSchedules = async (event: FormEvent) => {
    event.preventDefault();
    if (scheduleDraft.some((schedule) => schedule.days.length === 0)) {
      notifyError(new Error("Pick at least one day for each schedule."));
      return;
    }
    if (linksMutationIdRef.current !== 0) return;
    const mutationId = ++linksMutationIdRef.current;
    ++linksReadVersionRef.current;
    setScheduleStatus([]);
    setLinksStale(true);
    setSchedulesSaving(true);
    try {
      await saveConfig({ link_schedules: scheduleDraft });
      if (mutationId !== linksMutationIdRef.current) return;
      await refreshLinks();
      if (mutationId !== linksMutationIdRef.current) return;
      notifySuccess("Schedules saved");
      setScheduleDraft([]);
      setEditingSchedules(false);
    } catch (error) {
      if (mutationId !== linksMutationIdRef.current) return;
      notifyError(error);
    } finally {
      if (mutationId === linksMutationIdRef.current) {
        setSchedulesSaving(false);
        linksMutationIdRef.current = 0;
        ++linksReadVersionRef.current;
        void refreshLinks().catch(() => {});
      }
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
    const mutationId = ++echoMutationIdRef.current;
    ++echoReadVersionRef.current;
    setEchoBusy(true);
    try {
      const next = await api<EchoLinkStatus>("/api/allstar/echolink", { method: "PUT", json: body });
      if (mutationId !== echoMutationIdRef.current) return;
      setEcho(next);
      setEchoFilled(false);
      setEchoEdited(false);
      setEchoSnapshot(null);
      notifySuccess(next.loaded === false ? "Saved, but Asterisk didn't load EchoLink" : `EchoLink is on as ${body.callsign}`);
      setEditingEcho(false);
    } catch (error) {
      if (mutationId !== echoMutationIdRef.current) return;
      notifyError(error);
    } finally {
      if (mutationId === echoMutationIdRef.current) {
        setEchoBusy(false);
        echoMutationIdRef.current = 0;
        ++echoReadVersionRef.current;
        void refreshEcho().catch(() => {});
      }
    }
  };

  const turnOffEcho = async () => {
    if (!window.confirm("Turn off EchoLink? Asterisk restarts, and EchoLink stations are disconnected. The details are kept.")) {
      return;
    }
    const mutationId = ++echoMutationIdRef.current;
    ++echoReadVersionRef.current;
    setEchoBusy(true);
    try {
      const next = await api<EchoLinkStatus>("/api/allstar/echolink", { method: "DELETE" });
      if (mutationId !== echoMutationIdRef.current) return;
      setEcho(next);
      setEchoFilled(false);
      setEchoEdited(false);
      setEchoSnapshot(null);
      notifySuccess("EchoLink turned off");
      setEditingEcho(false);
    } catch (error) {
      if (mutationId !== echoMutationIdRef.current) return;
      notifyError(error);
    } finally {
      if (mutationId === echoMutationIdRef.current) {
        setEchoBusy(false);
        echoMutationIdRef.current = 0;
        ++echoReadVersionRef.current;
        void refreshEcho().catch(() => {});
      }
    }
  };

  const callsign =
    status?.identity?.callsign || (config?.callsign ? String(config.callsign).toUpperCase() : "");
  const savedFavoriteList = savedFavorites.filter((f) => f.node.trim());
  // De-duplicate by node for one-click buttons while keeping the editor list intact.
  const uniqueSavedFavorites = savedFavoriteList.filter(
    (favorite, index, list) => list.findIndex((item) => item.node === favorite.node) === index,
  );
  const canEditNode = Boolean(showIdentityForm || showNodeForm);
  const ready = isReady(status);
  const steps = setupSteps(status, uniqueSavedFavorites.length);
  const recents = recentNodes(
    linkEvents,
    uniqueSavedFavorites.map((f) => f.node),
    linkRows.map((l) => l.node),
  ).map((r) => ({ node: r.node, name: r.label }));

  return (
    <Page title="AllStarLink" description="Monitor connected nodes, manage links, and configure your network.">
      <Card withBorder radius="md" className="mor-as mor-as-strip" component="section" aria-label="This node">
        <div>
          <div className="mor-as-strip-label">This machine&apos;s node</div>
          <div className="mor-as-strip-node">{status?.node || (status ? "Not set" : "…")}</div>
        </div>
        {callsign ? <span className="mor-as-strip-call">{callsign}</span> : null}
        {view?.pill ? (
          <Badge color={pillColor(view.pill[1])} variant="light">
            {view.pill[0]}
          </Badge>
        ) : null}
        {status?.audio?.keyed ? (
          <Badge color="green" variant="light">
            Node keyed
          </Badge>
        ) : null}
      </Card>

      {errorAlert && !errorAlert.dismissed ? (
        <Alert
          color="red"
          role="alert"
          mt="md"
          withCloseButton
          onClose={() => setErrorAlert((prev) => (prev ? { ...prev, dismissed: true } : null))}
        >
          {errorAlert.message}
        </Alert>
      ) : null}

      {statusLoading && !status ? <LoadingBlock label="Loading AllStarLink" /> : null}
      {statusError && !status ? (
        <ErrorCard
          title="Couldn't load AllStarLink"
          onRetry={() => {
            void refresh().catch((error) => {
              setStatusStale(true);
              notifyError(error);
            });
          }}
        >
          {statusError}
        </ErrorCard>
      ) : null}
      {statusStale && status ? (
        <Text size="sm" c="amber">
          Status may be out of date. Retrying…
        </Text>
      ) : null}

      {linksLoading && linkRows.length === 0 && scheduleStatus.length === 0 ? <LoadingBlock label="Loading links" /> : null}
      {linksError && linkRows.length === 0 && scheduleStatus.length === 0 ? (
        <ErrorCard title="Couldn't load links" onRetry={() => { void refreshLinks().catch(() => {}); }}>
          {linksError}
        </ErrorCard>
      ) : null}
      {linksError && (linkRows.length > 0 || scheduleStatus.length > 0) ? (
        <Text size="sm" c="red">
          {linksError}{" "}
          <Button variant="subtle" size="xs" onClick={() => void refreshLinks().catch(() => {})}>
            Retry
          </Button>
        </Text>
      ) : null}
      {linksStale && !linksError && (linkRows.length > 0 || scheduleStatus.length > 0) ? (
        <Text size="sm" c="amber">
          Links may be out of date. Retrying…
        </Text>
      ) : null}

      <Tabs className="mor-link-workspace" value={tab} onChange={changeTab} variant="default">
        <Tabs.List style={{ overflowX: "auto", flexWrap: "nowrap" }}>
          <Tabs.Tab value="links">Links</Tabs.Tab>
          <Tabs.Tab value="schedule">Favorites and schedule</Tabs.Tab>
          <Tabs.Tab value="node">Node and EchoLink</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="links" pt="md">
          <Stack gap="md">
            {linksLoading && linkRows.length === 0 ? (
              <LoadingBlock label="Loading links" />
            ) : ready ? (
              <>
                <Card withBorder padding="lg" radius="md" component="section" aria-label="Linked nodes">
                  <LinkMap
                    hub={status?.node || "—"}
                    callsign={callsign}
                    hubKeyed={Boolean(status?.audio?.keyed)}
                    links={linkRows}
                    canWrite={canWrite}
                    busy={linkBusy}
                    onDisconnect={(n) => void disconnectNode(n)}
                    onDisconnectAll={() => void disconnectAll()}
                  />
                </Card>
                {canWrite ? (
                  <Card withBorder padding="lg" radius="md" component="section" aria-label="Connect to a node">
                    <ConnectBar
                      favorites={uniqueSavedFavorites}
                      recent={recents}
                      linked={linkRows.map((l) => l.node)}
                      busy={linkBusy}
                      onConnect={connectNode}
                    />
                  </Card>
                ) : null}
              </>
            ) : (
              <Card withBorder padding="lg" radius="md" component="section" aria-label="Setup">
                <SetupChecklist
                  steps={steps}
                  canAct={(action) => (action === "node" ? Boolean(canEditNode) : isAdmin)}
                  onAction={(action) => (action === "node" ? setEditingNode(true) : openFavoritesEditor())}
                />
                {status && !status.available && !status.manual ? (
                  <Text size="xs" c="dimmed" mt="md">
                    {status.error ?? "AllStarLink wasn't found on this machine (no /etc/asterisk/rpt.conf)."}
                  </Text>
                ) : null}
              </Card>
            )}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="schedule" pt="md">
          <Stack gap="md">
            <Card withBorder padding="lg" radius="md" component="section" aria-label="Favorite nodes">
              <Group justify="space-between" align="flex-start" wrap="wrap" mb="sm">
                <div>
                  <h2 className="mor-card-title">{labeled("Favorite nodes", <>One-click buttons on the Links tab and the dashboard. EchoLink: 3 plus the six-digit number.</>)}</h2>
                </div>
                {isAdmin ? (
                  <Button variant="default" size="xs" onClick={openFavoritesEditor}>
                    Edit favorites
                  </Button>
                ) : null}
              </Group>
              {uniqueSavedFavorites.length ? (
                <Group gap="xs">
                  {uniqueSavedFavorites.map((favorite) => (
                    <Badge
                      key={`${favorite.node}-${favorite.name}`}
                      size="lg"
                      variant="light"
                      color={favorite.monitor ? "gray" : "accent"}
                      style={{ textTransform: "none", fontWeight: 500 }}
                    >
                      <Text span ff="monospace" size="sm">
                        {favorite.node}
                      </Text>
                      {favorite.name.trim() ? ` · ${favorite.name.trim()}` : ""}
                      {favorite.monitor ? " · monitor" : ""}
                    </Badge>
                  ))}
                </Group>
              ) : (
                <EmptyState
                  action={
                    isAdmin ? (
                      <Button size="xs" variant="default" onClick={openFavoritesEditor}>
                        Add a favorite
                      </Button>
                    ) : undefined
                  }
                >
                  No favorites yet. Save the nodes you link to often and they become one-click buttons.
                </EmptyState>
              )}
            </Card>

            <Card withBorder padding="lg" radius="md" component="section" aria-label="Scheduled links">
              <Group justify="space-between" align="flex-start" wrap="wrap" mb="sm">
                <div>
                  <h2 className="mor-card-title">{labeled("Scheduled links", <>Connect automatically each week, then drop after a set time.</>)}</h2>
                </div>
                {isAdmin ? (
                  <Button variant="default" size="xs" onClick={openSchedulesEditor}>
                    Edit schedules
                  </Button>
                ) : null}
              </Group>
              <ScheduleWeek schedules={savedSchedules} live={scheduleStatus} onEdit={isAdmin ? openSchedulesEditor : undefined} />
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="node" pt="md">
          {echoLoading && !echo ? <LoadingBlock label="Loading EchoLink" /> : null}
          {echoError && !echo ? (
            <ErrorCard title="Couldn't load EchoLink" onRetry={() => void refreshEcho().catch(() => {})}>
              {echoError}
            </ErrorCard>
          ) : null}
          {echoError && echo ? (
            <Text size="sm" c="red" mb="sm">
              {echoError}{" "}
              <Button variant="subtle" size="xs" onClick={() => void refreshEcho().catch(() => {})}>
                Retry
              </Button>
            </Text>
          ) : null}
          {echoStale && !echoError && echo ? (
            <Text size="sm" c="amber" mb="sm">
              EchoLink status may be out of date. Retrying…
            </Text>
          ) : null}
          <Grid>
            <Grid.Col span={{ base: 12, md: 6 }}>
              <Card withBorder padding="lg" radius="md" h="100%" component="section" aria-label="Node">
                <Stack gap="md">
                  <Group justify="space-between" align="flex-start" wrap="wrap">
                    <div>
                      <h2 className="mor-card-title">AllStarLink node</h2>
                      <Group gap="sm" mt={6} wrap="wrap">
                        <Text ff="monospace" fw={700}>
                          {status?.node || "Not set"}
                        </Text>
                        {callsign ? (
                          <Text size="sm" fw={600}>
                            {callsign}
                          </Text>
                        ) : null}
                        {view?.pill ? (
                          <Badge color={pillColor(view.pill[1])} variant="light">
                            {view.pill[0]}
                          </Badge>
                        ) : null}
                      </Group>
                    </div>
                    {canEditNode ? (
                      <Button variant="default" size="xs" onClick={() => setEditingNode(true)}>
                        {status?.node ? "Edit node" : "Set up node"}
                      </Button>
                    ) : null}
                  </Group>
                  <Text c="dimmed" size="sm">
                    {view?.text ?? "Loading…"}
                  </Text>
                </Stack>
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 6 }}>
              {showEcho ? (
              <Card withBorder padding="lg" radius="md" h="100%" component="section" aria-label="EchoLink">
                <Stack gap="md">
                  <Group justify="space-between" align="flex-start" wrap="wrap">
                    <div>
                      <Text className="mor-card-title">
                        EchoLink
                      </Text>
                      <Group gap="sm" mt={6} wrap="wrap">
                        {echoView?.pill ? (
                          <Badge color={pillColor(echoView.pill[1])} variant="light">
                            {echoView.pill[0]}
                          </Badge>
                        ) : null}
                        {echoEdited ? (
                          <Badge color="amber" variant="light">
                            Unsaved
                          </Badge>
                        ) : null}
                      </Group>
                    </div>
                    {isAdmin && echo?.settings ? (
                      <Button variant="light" size="xs" onClick={() => setEditingEcho(true)}>
                        {echo.enabled ? "Edit…" : "Set up…"}
                      </Button>
                    ) : null}
                  </Group>
                  <Text c="dimmed" size="sm">
                    {echoView?.text}
                  </Text>
                  {echo?.settings && echo.enabled ? (
                    <Stack gap={6}>
                      <Group justify="space-between" gap="md" wrap="nowrap">
                        <Text size="sm" c="dimmed">
                          Callsign
                        </Text>
                        <Text size="sm" ta="right">
                          {echo.settings.callsign || "—"}
                        </Text>
                      </Group>
                      <Group justify="space-between" gap="md" wrap="nowrap">
                        <Text size="sm" c="dimmed">
                          EchoLink node
                        </Text>
                        <Text size="sm" ta="right" ff="monospace">
                          {echo.settings.node_number || "—"}
                        </Text>
                      </Group>
                      <Group justify="space-between" gap="md" wrap="nowrap">
                        <Text size="sm" c="dimmed">
                          Location
                        </Text>
                        <Text size="sm" ta="right">
                          {echo.settings.location || "—"}
                        </Text>
                      </Group>
                    </Stack>
                  ) : null}
                </Stack>
              </Card>
              ) : (
                <EmptyState>EchoLink setup appears here once AllStarLink is available on this machine.</EmptyState>
              )}
            </Grid.Col>
          </Grid>
        </Tabs.Panel>
      </Tabs>

      <ConfigEditDrawer
        opened={editingNode}
        onClose={() => {
          setEditingNode(false);
          setIdentityForm((prev) => ({ ...prev, password: "" }));
          setIdentityEdited(false);
          setIdentityFilled(false);
        }}
        title="AllStarLink node"
        subtitle="Register or pick the local ASL3 node this repeater becomes the radio for."
      >
        <Stack gap="md">
          {showIdentityForm ? (
            <form onSubmit={(event) => void saveIdentity(event)}>
              <Stack gap="md">
                <Text size="sm" fw={600}>
                  Set node number
                </Text>
                <Text c="dimmed" size="sm" maw="75ch">
                  From the AllStarLink portal: node number, password, and callsign. Updates <Code>rpt.conf</Code> and
                  makes the repeater its radio.
                </Text>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  {status!.nodes.length > 1 ? (
                    <Select
                      label="Change this local node"
                      data={status!.nodes.map((n) => ({
                        value: n.number,
                        label: n.number === "1999" ? "1999 (ASL3 placeholder)" : n.number,
                      }))}
                      value={identityForm.fromNode || null}
                      onChange={(value) => {
                        const fromNode = value || "";
                        setIdentityEdited(true);
                        setIdentityForm((prev) => ({
                          ...prev,
                          fromNode,
                          node:
                            !prev.node || prev.node === prev.fromNode
                              ? fromNode === "1999"
                                ? ""
                                : fromNode
                              : prev.node,
                        }));
                      }}
                      allowDeselect={false}
                    />
                  ) : null}
                  <TextInput
                    label={labeled("Node number", "Assigned in the AllStarLink portal. Not a favorite/remote node.")}
                    required
                    inputMode="numeric"
                    pattern="[0-9]{1,10}"
                    placeholder="e.g. 531870"
                    value={identityForm.node}
                    onChange={(event) => setIdentityField("node", event.currentTarget.value)}
                  />
                  <TextInput
                    label="Callsign / ID"
                    required
                    maxLength={10}
                    placeholder="e.g. W1AW"
                    value={identityForm.callsign}
                    onChange={(event) => setIdentityField("callsign", event.currentTarget.value)}
                  />
                  <TextInput
                    label={labeled("Node password", status?.identity?.has_password ? "Leave blank to keep the saved password" : "From the portal")}
                    type="password"
                    autoComplete="new-password"
                    required={Number(identityForm.node) >= 2000 && !status?.identity?.has_password}
                    placeholder={status?.identity?.has_password ? "******" : undefined}
                    description={status?.identity?.has_password ? "Saved password. Leave blank to keep it, or enter a replacement." : "Enter the node password from the AllStarLink portal."}
                    value={identityForm.password}
                    onChange={(event) => setIdentityField("password", event.currentTarget.value)}
                  />
                </SimpleGrid>
                <Group justify="flex-end">
                  <Button type="submit" loading={identityBusy}>
                    Save and use this node
                  </Button>
                </Group>
              </Stack>
            </form>
          ) : null}
          {showNodeForm ? (
            <form onSubmit={(event) => void bindNode(event)}>
              <Stack gap="md">
                <Text size="sm" fw={600}>
                  Or pick an existing local node
                </Text>
                <Select
                  label={labeled(
                    "This machine's node",
                    selected
                      ? selected.controlled
                        ? "The repeater is its radio."
                        : `Its radio is ${radioName(selected.rxchannel)} now.`
                      : "Nodes already in /etc/asterisk/rpt.conf.",
                  )}
                  data={status!.nodes.map((n) => ({
                    value: n.number,
                    label: n.number === "1999" ? "1999 (ASL3 placeholder)" : n.number,
                  }))}
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
                  Updates the node&apos;s <Code>rxchannel</Code>, <Code>duplex</Code>, and hang time, then restarts Asterisk.
                </Text>
              </Stack>
            </form>
          ) : null}
        </Stack>
      </ConfigEditDrawer>

      <ConfigEditDrawer
        opened={editingFavorites}
        onClose={() => {
          setEditingFavorites(false);
          setFavoriteDraft([]);
        }}
        title="Favorite nodes"
        subtitle="Remote nodes for the dashboard Links card."
      >
        <form onSubmit={(event) => void saveFavorites(event)}>
          <Stack gap="md">
            <Table.ScrollContainer minWidth={480}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Node</Table.Th>
                    <Table.Th>Name</Table.Th>
                    <Table.Th>Monitor only</Table.Th>
                    <ActionsHeader />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {favoriteDraft.map((favorite, index) => (
                    <Table.Tr key={index}>
                      <Table.Td>
                        <TextInput
                          required
                          inputMode="numeric"
                          pattern="[0-9]{1,10}"
                          placeholder="e.g. 2000"
                          value={favorite.node}
                          onChange={(event) => {
                            const next = [...favoriteDraft];
                            next[index] = { ...favorite, node: event.currentTarget.value };
                            setFavoriteDraft(next);
                          }}
                        />
                      </Table.Td>
                      <Table.Td>
                        <TextInput
                          maxLength={40}
                          placeholder="Optional name"
                          value={favorite.name}
                          onChange={(event) => {
                            const next = [...favoriteDraft];
                            next[index] = { ...favorite, name: event.currentTarget.value };
                            setFavoriteDraft(next);
                          }}
                        />
                      </Table.Td>
                      <Table.Td>
                        <Checkbox
                          aria-label="Monitor only"
                          checked={favorite.monitor}
                          onChange={(event) => {
                            const next = [...favoriteDraft];
                            next[index] = { ...favorite, monitor: event.currentTarget.checked };
                            setFavoriteDraft(next);
                          }}
                        />
                      </Table.Td>
                      <Table.Td>
                        <Button
                          size="compact-sm"
                          color="red"
                          variant="subtle"
                          onClick={() => setFavoriteDraft(favoriteDraft.filter((_, i) => i !== index))}
                        >
                          Remove
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {favoriteDraft.length === 0 ? (
              <Text c="dimmed" size="sm">
                No favorites yet.
              </Text>
            ) : null}
            <Group justify="space-between">
              <Button type="button" variant="subtle" onClick={() => setFavoriteDraft([...favoriteDraft, emptyFavorite()])}>
                Add a node
              </Button>
              <Button type="submit" loading={favoritesSaving}>
                Save favorites
              </Button>
            </Group>
          </Stack>
        </form>
      </ConfigEditDrawer>

      <ConfigEditDrawer
        opened={editingSchedules}
        onClose={() => {
          setEditingSchedules(false);
          setScheduleDraft([]);
        }}
        title="Scheduled links"
        subtitle="0 minutes keeps the link until someone disconnects it."
      >
        <form onSubmit={(event) => void saveSchedules(event)}>
          <Stack gap="md">
            {scheduleDraft.map((schedule, index) => (
              <Stack
                key={index}
                gap="sm"
                p="md"
                style={{
                  border: "1px solid var(--mantine-color-default-border)",
                  borderRadius: "var(--mantine-radius-md)",
                }}
              >
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <TextInput
                    label="Node"
                    required
                    inputMode="numeric"
                    pattern="[0-9]{1,10}"
                    placeholder="e.g. 2000"
                    value={schedule.node}
                    onChange={(event) => {
                      const next = [...scheduleDraft];
                      next[index] = { ...schedule, node: event.currentTarget.value };
                      setScheduleDraft(next);
                    }}
                  />
                  <TextInput
                    label="Name"
                    maxLength={40}
                    placeholder="e.g. Tuesday net"
                    value={schedule.name}
                    onChange={(event) => {
                      const next = [...scheduleDraft];
                      next[index] = { ...schedule, name: event.currentTarget.value };
                      setScheduleDraft(next);
                    }}
                  />
                  <TextInput
                    label="Start"
                    type="time"
                    required
                    value={schedule.time}
                    onChange={(event) => {
                      const next = [...scheduleDraft];
                      next[index] = { ...schedule, time: event.currentTarget.value };
                      setScheduleDraft(next);
                    }}
                  />
                  <NumberInput
                    label={labeled("Minutes linked", "0 = until disconnected")}
                    min={0}
                    max={720}
                    required
                    value={schedule.minutes}
                    onChange={(value) => {
                      const next = [...scheduleDraft];
                      next[index] = { ...schedule, minutes: Number(value) || 0 };
                      setScheduleDraft(next);
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
                        const next = [...scheduleDraft];
                        next[index] = { ...schedule, days };
                        setScheduleDraft(next);
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
                        const next = [...scheduleDraft];
                        next[index] = { ...schedule, monitor: event.currentTarget.checked };
                        setScheduleDraft(next);
                      }}
                    />
                    <Checkbox
                      label="On"
                      checked={schedule.enabled}
                      onChange={(event) => {
                        const next = [...scheduleDraft];
                        next[index] = { ...schedule, enabled: event.currentTarget.checked };
                        setScheduleDraft(next);
                      }}
                    />
                  </Group>
                  <Group gap="sm">
                    <Button
                      size="compact-sm"
                      color="red"
                      variant="subtle"
                      onClick={() => setScheduleDraft(scheduleDraft.filter((_, i) => i !== index))}
                    >
                      Remove
                    </Button>
                  </Group>
                </Group>
              </Stack>
            ))}
            {scheduleDraft.length === 0 ? (
              <Text c="dimmed" size="sm">
                No scheduled links.
              </Text>
            ) : null}
            <Group justify="space-between">
              <Button type="button" variant="subtle" onClick={() => setScheduleDraft([...scheduleDraft, emptySchedule()])}>
                Add a schedule
              </Button>
              <Button type="submit" loading={schedulesSaving}>
                Save schedules
              </Button>
            </Group>
          </Stack>
        </form>
      </ConfigEditDrawer>

      <ConfigEditDrawer
        opened={editingEcho}
        onClose={() => {
          setEditingEcho(false);
          setEchoForm((prev) => ({ ...prev, password: "" }));
          setEchoEdited(false);
          setEchoFilled(false);
          setEchoSnapshot(null);
        }}
        title="EchoLink"
        subtitle="Station details for EchoLink users connecting through AllStar."
      >
        {isAdmin && echo?.settings ? (
          <form onSubmit={(event) => void saveEcho(event)}>
            <Stack gap="md">
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <TextInput
                  label={labeled("Callsign", "With -R for a repeater, as validated by EchoLink.")}
                  required
                  value={echoForm.callsign}
                  onChange={(event) => setEchoField("callsign", event.currentTarget.value)}
                />
                <TextInput
                  label={labeled("Password", echo.settings.has_password ? "Leave blank to keep the saved password" : undefined)}
                  type="password"
                  autoComplete="new-password"
                  required={!echo.settings.has_password}
                  placeholder={echo.settings.has_password ? "******" : undefined}
                  description={echo.settings.has_password ? "Saved password. Leave blank to keep it, or enter a replacement." : "Enter the EchoLink password."}
                  value={echoForm.password}
                  onChange={(event) => setEchoField("password", event.currentTarget.value)}
                />
                <TextInput
                  label="EchoLink node number"
                  required
                  inputMode="numeric"
                  placeholder="e.g. 123456"
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
                  placeholder="e.g. city, state"
                  value={echoForm.location}
                  onChange={(event) => setEchoField("location", event.currentTarget.value)}
                />
                <TextInput
                  label="Email"
                  type="email"
                  required
                  maxLength={80}
                  placeholder="e.g. you@example.com"
                  value={echoForm.email}
                  onChange={(event) => setEchoField("email", event.currentTarget.value)}
                />
              </SimpleGrid>
              <Accordion variant="separated" radius="md">
                <Accordion.Item value="advanced">
                  <Accordion.Control>Advanced · position &amp; RF</Accordion.Control>
                  <Accordion.Panel>
                    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
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
                        label={labeled("Tone", "0 for none.")}
                        suffix=" Hz"
                        min={0}
                        max={300}
                        decimalScale={1}
                        value={echoForm.tone_hz === "" ? undefined : echoForm.tone_hz}
                        onChange={(value) =>
                          setEchoField("tone_hz", value === "" || value === undefined ? "" : Number(value))
                        }
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
                  </Accordion.Panel>
                </Accordion.Item>
              </Accordion>
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
      </ConfigEditDrawer>
    </Page>
  );
}
