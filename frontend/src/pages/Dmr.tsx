import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ActionsHeader } from "../components/ActionsHeader";
import { ConfigEditDrawer } from "../components/ConfigEditDrawer";
import { ConfigSummaryCard } from "../components/ConfigSummaryCard";
import { EmptyState } from "../components/EmptyState";
import { labeled } from "../components/FieldHint";
import { notifyError, notifySuccess, useConfig } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const REFRESH_MS = 3000;

type DmrSettings = {
  callsign: string;
  dmr_id: string;
  repeater_ssid: string;
  has_password: boolean;
  network: "brandmeister" | "tgif" | "custom";
  host: string;
  port: number;
  talkgroup: number;
  slot: number;
  color_code: number;
  location: string;
  description: string;
  use_emulator: boolean;
};

type DmrLastHeard = {
  radio_id: number;
  callsign: string;
  name: string;
  talkgroup: number;
  slot: number;
  started_at: number;
  ended_at: number | null;
  active: boolean;
};

type DmrStatus = {
  available: boolean;
  enabled: boolean;
  error: string | null;
  settings: DmrSettings | null;
  audio: {
    configured: boolean;
    running: boolean;
    error: string | null;
    peer: string | null;
    listen: string | null;
    keyed: boolean;
    last_heard: DmrLastHeard | null;
  };
  services: { analog_bridge: boolean | null; mmdvm_bridge: boolean | null; md380_emu: boolean | null };
  restart: { available: boolean; requested: boolean; last_action: string | null };
};

function formatHeardAt(startedAt: number, endedAt: number | null, active: boolean): string {
  const start = new Date(startedAt * 1000);
  const time = start.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
  if (active || endedAt == null) return `since ${time}`;
  const seconds = Math.max(0, Math.round(endedAt - startedAt));
  return `${time} · ${seconds}s`;
}

type DmrForm = {
  callsign: string;
  dmr_id: string;
  repeater_ssid: string;
  password: string;
  network: "brandmeister" | "tgif" | "custom";
  host: string;
  port: number;
  talkgroup: number;
  slot: string;
  color_code: number;
  location: string;
  description: string;
  use_emulator: boolean;
};

type TalkgroupFavorite = { talkgroup: string; name: string; slot: string };

function emptyForm(): DmrForm {
  return {
    callsign: "",
    dmr_id: "",
    repeater_ssid: "01",
    password: "",
    network: "brandmeister",
    host: "3102.repeater.net",
    port: 62031,
    talkgroup: 9,
    slot: "2",
    color_code: 1,
    location: "",
    description: "moreopenrepeater",
    use_emulator: true,
  };
}

function emptyFavorite(): TalkgroupFavorite {
  return { talkgroup: "", name: "", slot: "2" };
}

function formFromSettings(settings: DmrSettings): DmrForm {
  return {
    callsign: settings.callsign,
    dmr_id: settings.dmr_id,
    repeater_ssid: settings.repeater_ssid,
    password: "",
    network: settings.network,
    host: settings.host,
    port: settings.port,
    talkgroup: settings.talkgroup,
    slot: String(settings.slot),
    color_code: settings.color_code,
    location: settings.location,
    description: settings.description,
    use_emulator: settings.use_emulator,
  };
}

function serviceLabel(value: boolean | null | undefined): string {
  if (value === true) return "up";
  if (value === false) return "down";
  return "unknown";
}

function favoritesFromConfig(raw: unknown): TalkgroupFavorite[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((row) => {
    const item = row as { talkgroup?: number | string; name?: string; slot?: number | string };
    return {
      talkgroup: item.talkgroup != null ? String(item.talkgroup) : "",
      name: item.name ?? "",
      slot: item.slot != null ? String(item.slot) : "2",
    };
  });
}

export function Dmr() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const { config, save: saveConfig } = useConfig();
  const [status, setStatus] = useState<DmrStatus | null>(null);
  const [form, setForm] = useState<DmrForm>(emptyForm());
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [favoritesOpen, setFavoritesOpen] = useState(false);
  const [favorites, setFavorites] = useState<TalkgroupFavorite[]>([]);
  const [saving, setSaving] = useState(false);
  const [favoritesSaving, setFavoritesSaving] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api<DmrStatus>("/api/dmr");
      setStatus(next);
      return next;
    } catch (error) {
      notifyError(error);
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (status?.settings) setForm(formFromSettings(status.settings));
  }, [status?.settings?.dmr_id, status?.settings?.callsign, status?.enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setFavorites(favoritesFromConfig(config?.dmr_talkgroup_favorites));
  }, [config?.dmr_talkgroup_favorites]);

  const setField = <K extends keyof DmrForm>(name: K, value: DmrForm[K]) => {
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const onNetwork = (network: DmrForm["network"]) => {
    setForm((prev) => {
      const next = { ...prev, network };
      if (network === "brandmeister" && (!prev.host || prev.host === "tgif.network")) {
        next.host = "3102.repeater.net";
      } else if (network === "tgif" && (!prev.host || prev.host.includes("repeater.net"))) {
        next.host = "tgif.network";
      }
      return next;
    });
  };

  const onSave = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin) return;
    if (!window.confirm(`Save DMR as ${form.callsign} / ${form.dmr_id}? The DVSwitch bridges restart.`)) return;
    setSaving(true);
    try {
      const body = {
        ...form,
        slot: Number(form.slot),
        password: form.password || undefined,
      };
      const next = await api<DmrStatus>("/api/dmr", { method: "PUT", json: body });
      setStatus(next);
      if (next.settings) setForm(formFromSettings(next.settings));
      setDrawerOpen(false);
      notifySuccess(next.restart.requested ? "Saved — bridges restarting" : "Saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setSaving(false);
    }
  };

  const onDisable = async () => {
    if (!isAdmin) return;
    if (!window.confirm("Turn off DMR? The bridges stop; saved details are kept.")) return;
    setSaving(true);
    try {
      setStatus(await api<DmrStatus>("/api/dmr", { method: "DELETE" }));
      notifySuccess("DMR turned off");
      setDrawerOpen(false);
    } catch (error) {
      notifyError(error);
    } finally {
      setSaving(false);
    }
  };

  const saveFavorites = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin) return;
    setFavoritesSaving(true);
    try {
      const cleaned = favorites
        .filter((f) => f.talkgroup.trim())
        .map((f) => ({
          talkgroup: Number(f.talkgroup),
          name: f.name.trim(),
          slot: Number(f.slot) === 1 ? 1 : 2,
        }));
      await saveConfig({ dmr_talkgroup_favorites: cleaned });
      setFavoritesOpen(false);
      notifySuccess("Talkgroup favorites saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setFavoritesSaving(false);
    }
  };

  const selectFavorite = async (favorite: TalkgroupFavorite) => {
    const talkgroup = Number(favorite.talkgroup);
    if (!talkgroup || !status?.enabled) return;
    const key = `${favorite.talkgroup}-${favorite.slot}`;
    setSelecting(key);
    try {
      const next = await api<DmrStatus>("/api/dmr/talkgroup", {
        method: "POST",
        json: { talkgroup, slot: Number(favorite.slot) === 1 ? 1 : 2 },
      });
      setStatus(next);
      if (next.settings) setForm(formFromSettings(next.settings));
      notifySuccess(
        next.restart.requested
          ? `Talkgroup ${talkgroup} — bridges restarting`
          : `Talkgroup ${talkgroup}`,
      );
    } catch (error) {
      notifyError(error);
    } finally {
      setSelecting(null);
    }
  };

  const settings = status?.settings;
  const savedFavorites = favorites.filter((f) => f.talkgroup.trim());
  const currentTg = settings?.talkgroup;
  const badge = !status
    ? "…"
    : !status.available
      ? "Not installed"
      : status.enabled
        ? status.audio.keyed
          ? "Keyed"
          : "On"
        : "Off";
  const badgeColor = status?.enabled ? "green" : "gray";

  const summary = !status
    ? "Loading…"
    : status.error
      ? status.error
      : !status.available
        ? "Install with: sudo bash scripts/install-pi.sh --dmr"
        : status.enabled && settings
          ? `${settings.callsign} · Id ${settings.dmr_id} · TG ${settings.talkgroup} slot ${settings.slot}`
          : "Set callsign, DMR Id, network password and talkgroup to bridge the analog repeater.";

  return (
    <Page title="DMR">
      <Stack gap="md">
        <ConfigSummaryCard
          title="DMR bridge"
          badge={badge}
          badgeColor={badgeColor}
          blurb={summary}
          hint="Cross-mode bridge via DVSwitch to BrandMeister, TGIF, or a custom master."
          onEdit={isAdmin && status?.available ? () => setDrawerOpen(true) : undefined}
          editLabel={status?.enabled ? "Edit…" : "Set up…"}
        />
        {status?.available && (
          <Text size="sm" c="dimmed">
            Analog_Bridge {serviceLabel(status.services.analog_bridge)} · MMDVM_Bridge{" "}
            {serviceLabel(status.services.mmdvm_bridge)} · md380-emu {serviceLabel(status.services.md380_emu)}
            {status.audio.listen ? ` · USRP ${status.audio.listen} → ${status.audio.peer}` : ""}
          </Text>
        )}

        {status?.available && status.enabled ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-label="Last heard">
            <h2 className="mor-card-title">
              {labeled(
                "Last heard",
                <>Callsign and talkgroup from Analog_Bridge USRP metadata (exportMetadata). Updates when a DMR station keys up on the network.</>,
              )}
            </h2>
            {status.audio.last_heard ? (
              <Group gap="md" align="baseline" wrap="wrap">
                <Text size="lg" fw={600}>
                  {status.audio.last_heard.callsign}
                  {status.audio.last_heard.name ? ` · ${status.audio.last_heard.name}` : ""}
                </Text>
                <Badge variant="light" color={status.audio.last_heard.active ? "green" : "gray"} style={{ textTransform: "none" }}>
                  {status.audio.last_heard.active ? "Talking" : "Idle"}
                </Badge>
                <Text size="sm" c="dimmed">
                  Id {status.audio.last_heard.radio_id} · TG {status.audio.last_heard.talkgroup} · TS
                  {status.audio.last_heard.slot} ·{" "}
                  {formatHeardAt(
                    status.audio.last_heard.started_at,
                    status.audio.last_heard.ended_at,
                    status.audio.last_heard.active,
                  )}
                </Text>
              </Group>
            ) : (
              <Text size="sm" c="dimmed">
                No network transmission heard yet. Metadata appears after Analog_Bridge sends SET_INFO on the USRP path.
              </Text>
            )}
          </Card>
        ) : null}

        {status?.available ? (
          <Card withBorder padding="lg" radius="md" component="section" aria-label="Talkgroup favorites">
            <Group justify="space-between" align="flex-start" wrap="wrap" mb="sm">
              <div>
                <h2 className="mor-card-title">
                  {labeled("Talkgroup favorites", <>DMR talkgroup numbers — not AllStar node numbers. One click retunes the static TX talkgroup.</>)}
                </h2>
              </div>
              {isAdmin ? (
                <Button variant="default" size="xs" onClick={() => setFavoritesOpen(true)}>
                  Edit favorites
                </Button>
              ) : null}
            </Group>
            {savedFavorites.length ? (
              <Group gap="xs">
                {savedFavorites.map((favorite) => {
                  const tg = Number(favorite.talkgroup);
                  const active = status.enabled && currentTg === tg;
                  const key = `${favorite.talkgroup}-${favorite.slot}`;
                  return (
                    <Button
                      key={key}
                      size="compact-md"
                      variant={active ? "filled" : "light"}
                      color={active ? "accent" : "gray"}
                      loading={selecting === key}
                      disabled={!status.enabled || selecting !== null}
                      onClick={() => void selectFavorite(favorite)}
                      style={{ textTransform: "none", fontWeight: 500 }}
                    >
                      <Text span ff="monospace" size="sm">
                        {favorite.talkgroup}
                      </Text>
                      {favorite.name.trim() ? ` · ${favorite.name.trim()}` : ""}
                      {favorite.slot === "1" ? " · TS1" : ""}
                    </Button>
                  );
                })}
              </Group>
            ) : (
              <EmptyState
                action={
                  isAdmin ? (
                    <Button size="xs" variant="default" onClick={() => setFavoritesOpen(true)}>
                      Add a talkgroup
                    </Button>
                  ) : undefined
                }
              >
                No talkgroup favorites yet. Save the groups you use often for one-click retune.
              </EmptyState>
            )}
            {status.enabled && currentTg != null ? (
              <Badge mt="sm" variant="light" color="gray" style={{ textTransform: "none" }}>
                Current TG {currentTg} · slot {settings?.slot}
              </Badge>
            ) : null}
          </Card>
        ) : (
          <EmptyState>DMR setup appears here after install-pi.sh --dmr on this machine.</EmptyState>
        )}
      </Stack>

      <ConfigEditDrawer
        opened={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="DMR"
        subtitle="DVSwitch bridge to BrandMeister, TGIF, or a custom master."
      >
        <form onSubmit={onSave}>
          <Stack gap="md">
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <TextInput
                label={labeled("Callsign", "As registered with the DMR network.")}
                value={form.callsign}
                onChange={(e) => setField("callsign", e.currentTarget.value.toUpperCase())}
                required
              />
              <TextInput
                label={labeled("DMR Id", "Seven-digit radio Id.")}
                value={form.dmr_id}
                onChange={(e) => setField("dmr_id", e.currentTarget.value)}
                required
              />
              <TextInput
                label={labeled("Repeater SSID", "Two digits appended to the Id (01–99).")}
                value={form.repeater_ssid}
                onChange={(e) => setField("repeater_ssid", e.currentTarget.value)}
                required
              />
              <TextInput
                label={labeled("Network password", settings?.has_password ? "Leave blank to keep the saved password." : "Required.")}
                type="password"
                value={form.password}
                onChange={(e) => setField("password", e.currentTarget.value)}
                autoComplete="new-password"
              />
              <Select
                label="Network"
                data={[
                  { value: "brandmeister", label: "BrandMeister" },
                  { value: "tgif", label: "TGIF" },
                  { value: "custom", label: "Custom" },
                ]}
                value={form.network}
                onChange={(value) => value && onNetwork(value as DmrForm["network"])}
              />
              <TextInput label="Host" value={form.host} onChange={(e) => setField("host", e.currentTarget.value)} required />
              <NumberInput label="Port" value={form.port} onChange={(value) => setField("port", Number(value) || 62031)} min={1} max={65535} />
              <NumberInput
                label="Talkgroup"
                value={form.talkgroup}
                onChange={(value) => setField("talkgroup", Number(value) || 9)}
                min={1}
              />
              <Select
                label="Slot"
                data={[
                  { value: "1", label: "1" },
                  { value: "2", label: "2" },
                ]}
                value={form.slot}
                onChange={(value) => value && setField("slot", value)}
              />
              <NumberInput
                label="Color code"
                value={form.color_code}
                onChange={(value) => setField("color_code", Number(value) || 1)}
                min={0}
                max={15}
              />
              <TextInput label="Location" value={form.location} onChange={(e) => setField("location", e.currentTarget.value)} />
              <TextInput
                label="Description"
                value={form.description}
                onChange={(e) => setField("description", e.currentTarget.value)}
              />
            </SimpleGrid>
            <Switch
              label={labeled("Software AMBE (md380-emu)", "Use when no USB AMBE stick is attached.")}
              checked={form.use_emulator}
              onChange={(e) => setField("use_emulator", e.currentTarget.checked)}
            />
            <Group justify="space-between">
              {status?.enabled ? (
                <Button variant="default" color="red" onClick={() => void onDisable()} disabled={saving}>
                  Turn off DMR
                </Button>
              ) : (
                <span />
              )}
              <Button type="submit" loading={saving}>
                Save and enable
              </Button>
            </Group>
          </Stack>
        </form>
      </ConfigEditDrawer>

      <ConfigEditDrawer
        opened={favoritesOpen}
        onClose={() => setFavoritesOpen(false)}
        title="Talkgroup favorites"
        subtitle="DMR talkgroups for one-click retune. These are not AllStar node numbers."
      >
        <form onSubmit={(event) => void saveFavorites(event)}>
          <Stack gap="md">
            <Table.ScrollContainer minWidth={480}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Talkgroup</Table.Th>
                    <Table.Th>Name</Table.Th>
                    <Table.Th>Slot</Table.Th>
                    <ActionsHeader />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {favorites.map((favorite, index) => (
                    <Table.Tr key={index}>
                      <Table.Td>
                        <TextInput
                          required
                          inputMode="numeric"
                          pattern="[0-9]{1,7}"
                          placeholder="e.g. 3100"
                          value={favorite.talkgroup}
                          onChange={(event) => {
                            const next = [...favorites];
                            next[index] = { ...favorite, talkgroup: event.currentTarget.value };
                            setFavorites(next);
                          }}
                        />
                      </Table.Td>
                      <Table.Td>
                        <TextInput
                          maxLength={40}
                          placeholder="Optional name"
                          value={favorite.name}
                          onChange={(event) => {
                            const next = [...favorites];
                            next[index] = { ...favorite, name: event.currentTarget.value };
                            setFavorites(next);
                          }}
                        />
                      </Table.Td>
                      <Table.Td>
                        <Select
                          data={[
                            { value: "1", label: "1" },
                            { value: "2", label: "2" },
                          ]}
                          value={favorite.slot}
                          onChange={(value) => {
                            if (!value) return;
                            const next = [...favorites];
                            next[index] = { ...favorite, slot: value };
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
                Add a talkgroup
              </Button>
              <Button type="submit" loading={favoritesSaving}>
                Save favorites
              </Button>
            </Group>
          </Stack>
        </form>
      </ConfigEditDrawer>
    </Page>
  );
}
