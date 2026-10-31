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
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { labeled } from "../components/FieldHint";

type Macro = {
  pattern: string;
  description: string;
  action: string;
  command: string;
  node_id: string;
  needs_code: boolean;
  help_hidden: boolean | null;
};

type Announcement = { id: string; name: string };

type HaValues = {
  homeassistant_url: string;
  homeassistant_say_result: boolean;
};

const SPARE_PINS = [1, 2, 4, 5, 6, 7, 8];
const HIDDEN_FROM_HELP = new Set(["tx_disable", "tx_enable", "link_radio_on", "link_radio_off"]);
const GPIO_VERBS: Record<string, string> = { on: "on", off: "off", toggle: "toggle", pulse: "on for" };

const ACTION_OPTIONS = [
  { value: "link", label: "Link command" },
  { value: "time", label: "Talking clock" },
  { value: "weather", label: "Weather alerts" },
  { value: "id", label: "Station ID now" },
  { value: "announcement", label: "Play an announcement" },
  { value: "say", label: "Say text" },
  { value: "parrot", label: "Parrot (play next transmission back)" },
  { value: "tx_disable", label: "Transmitter off" },
  { value: "tx_enable", label: "Transmitter on" },
  { value: "lockout_clear", label: "Clear a stuck-carrier lockout" },
  { value: "link_radio_on", label: "Link radio on" },
  { value: "link_radio_off", label: "Link radio off" },
  { value: "remote_tune", label: "Remote base frequency (keyed after, then #)" },
  { value: "remote_shift", label: "Remote base shift" },
  { value: "remote_tone", label: "Remote base tone (keyed after, then #)" },
  { value: "remote_status", label: "Remote base status" },
  { value: "net_start", label: "Start net mode" },
  { value: "net_end", label: "End net mode" },
  { value: "aprs", label: "APRS stations nearby" },
  { value: "gpio", label: "Switch a GPIO output" },
  { value: "homeassistant", label: "Home Assistant" },
  { value: "help", label: "Spoken help (read out the commands)" },
  { value: "metar", label: "Airport weather" },
];

const ACTION_LABELS = Object.fromEntries(ACTION_OPTIONS.map((o) => [o.value, o.label]));

const HA_KEYS = ["homeassistant_url", "homeassistant_say_result"] as (keyof HaValues)[];
const HA_DEFAULTS: HaValues = { homeassistant_url: "", homeassistant_say_result: false };

const EMPTY_FORM = {
  pattern: "",
  description: "",
  action: "link",
  command: "",
  node_id: "",
  say_text: "",
  announcement_id: "",
  ha_target: "",
  help_page: "",
  metar_icao: "",
  remote_shift: "minus",
  gpio_pin: "1",
  gpio_verb: "on",
  gpio_seconds: 1,
  needs_code: false,
  help_hidden: false,
};

function pinLabel(pin: number | string, name?: string) {
  return name ? `${name} (GPIO${pin})` : `GPIO${pin}`;
}

function macroDetails(
  macro: Macro,
  announcements: Announcement[],
  gpioPins: Record<string, { mode?: string; name?: string }>,
  airports: { icao: string; name?: string }[],
): string {
  if (macro.action === "link") {
    return macro.node_id ? `${macro.command} → node ${macro.node_id}` : macro.command || "—";
  }
  if (macro.action === "announcement") {
    const a = announcements.find((x) => x.id === macro.command);
    return a ? a.name : "missing announcement";
  }
  if (macro.action === "say") return `“${macro.command}”`;
  if (macro.action === "homeassistant") return macro.command || "—";
  if (macro.action === "help") return `Page ${macro.command || "1"}`;
  if (macro.action === "metar") {
    if (!macro.command) return "Every listed airport";
    const airport = airports.find((a) => a.icao === macro.command);
    return airport?.name ? `${macro.command} ${airport.name}` : macro.command;
  }
  if (macro.action === "remote_shift") return macro.command || "—";
  if (macro.action === "remote_tune" || macro.action === "remote_tone") return "keyed, then #";
  if (macro.action === "gpio") {
    const [pin, verb, seconds] = macro.command.split(/\s+/);
    const settings = gpioPins[pin];
    const label = pinLabel(pin, settings?.name);
    const time = verb === "pulse" ? ` ${seconds ?? "1"} s` : "";
    const warn = settings?.mode === "output" ? "" : " (not an output)";
    return `${label} ${GPIO_VERBS[verb] ?? verb}${time}${warn}`;
  }
  return "—";
}

export function Macros() {
  const { config } = useConfig();
  const [macros, setMacros] = useState<Macro[]>([]);
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [editingPattern, setEditingPattern] = useState<string | null>(null);
  const [helpHiddenTouched, setHelpHiddenTouched] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const ha = useConfigSlice<HaValues>(HA_KEYS, HA_DEFAULTS);

  const gpioPins = (config?.gpio_pins as Record<string, { mode?: string; name?: string }> | undefined) ?? {};
  const airports = (config?.metar_airports as { icao: string; name?: string }[] | undefined) ?? [];

  const load = useCallback(async () => {
    const [nextMacros, nextAnnouncements] = await Promise.all([
      api<Macro[]>("/api/macros"),
      api<Announcement[]>("/api/announcements"),
    ]);
    setMacros(nextMacros);
    setAnnouncements(nextAnnouncements);
  }, []);

  useEffect(() => {
    load().catch(notifyError);
  }, [load]);

  const sorted = useMemo(
    () => [...macros].sort((a, b) => a.pattern.localeCompare(b.pattern)),
    [macros],
  );

  const gpioOptions = useMemo(() => {
    const outputs = SPARE_PINS.filter((pin) => gpioPins[String(pin)]?.mode === "output");
    const pins = outputs.length ? outputs : SPARE_PINS;
    return pins.map((pin) => ({
      value: String(pin),
      label: pinLabel(pin, gpioPins[String(pin)]?.name),
    }));
  }, [gpioPins]);

  const airportOptions = useMemo(() => {
    const options = [{ value: "", label: "Every listed airport" }, ...airports.map((a) => ({
      value: a.icao,
      label: a.name ? `${a.icao} ${a.name}` : a.icao,
    }))];
    if (form.metar_icao && !airports.some((a) => a.icao === form.metar_icao)) {
      options.push({ value: form.metar_icao, label: form.metar_icao });
    }
    return options;
  }, [airports, form.metar_icao]);

  const setField = <K extends keyof typeof EMPTY_FORM>(name: K, value: (typeof EMPTY_FORM)[K]) => {
    setForm((prev) => {
      const next = { ...prev, [name]: value };
      if (name === "action" && !helpHiddenTouched) {
        next.help_hidden = HIDDEN_FROM_HELP.has(String(value)) || next.needs_code;
      }
      if (name === "needs_code" && !helpHiddenTouched) {
        next.help_hidden = HIDDEN_FROM_HELP.has(next.action) || Boolean(value);
      }
      return next;
    });
  };

  const stopEdit = () => {
    setEditingPattern(null);
    setHelpHiddenTouched(false);
    setForm(EMPTY_FORM);
  };

  const startEdit = (macro: Macro) => {
    const next = { ...EMPTY_FORM };
    next.pattern = macro.pattern;
    next.description = macro.description;
    next.action = macro.action;
    next.node_id = macro.node_id;
    next.needs_code = macro.needs_code;
    next.help_hidden = Boolean(macro.help_hidden);
    if (macro.action === "link") next.command = macro.command;
    if (macro.action === "say") next.say_text = macro.command;
    if (macro.action === "announcement") next.announcement_id = macro.command;
    if (macro.action === "homeassistant") next.ha_target = macro.command;
    if (macro.action === "help") next.help_page = macro.command;
    if (macro.action === "metar") next.metar_icao = macro.command;
    if (macro.action === "remote_shift") next.remote_shift = macro.command || "minus";
    if (macro.action === "gpio") {
      const [pin, verb, seconds] = macro.command.split(/\s+/);
      next.gpio_pin = pin;
      next.gpio_verb = verb;
      next.gpio_seconds = Number(seconds ?? 1) || 1;
    }
    setEditingPattern(macro.pattern);
    setHelpHiddenTouched(true);
    setForm(next);
  };

  const collect = () => {
    const action = form.action;
    const gpio = `${form.gpio_pin} ${form.gpio_verb}${form.gpio_verb === "pulse" ? ` ${form.gpio_seconds}` : ""}`;
    const command =
      (
        {
          link: form.command,
          say: form.say_text,
          announcement: form.announcement_id,
          gpio,
          homeassistant: form.ha_target,
          help: form.help_page,
          metar: form.metar_icao,
          remote_shift: form.remote_shift,
        } as Record<string, string>
      )[action] ?? "";
    return {
      pattern: form.pattern.trim().toUpperCase(),
      description: form.description.trim(),
      action,
      command: command.trim(),
      node_id: action === "link" ? form.node_id.trim() : "",
      needs_code: form.needs_code,
      help_hidden: form.help_hidden,
    };
  };

  const remove = async (pattern: string) => {
    if (!window.confirm(`Delete macro ${pattern}?`)) return;
    try {
      setMacros(await api<Macro[]>(`/api/macros/${encodeURIComponent(pattern)}`, { method: "DELETE" }));
      if (editingPattern === pattern) stopEdit();
      notifySuccess(`Deleted macro ${pattern}`);
    } catch (error) {
      notifyError(error);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body = collect();
    const existing = macros.find((m) => m.pattern === body.pattern);
    if (existing && body.pattern !== editingPattern && !window.confirm(`Macro ${body.pattern} already exists. Replace it?`)) {
      return;
    }
    setSaving(true);
    try {
      let next = await api<Macro[]>("/api/macros", { method: "POST", json: body });
      if (editingPattern && editingPattern !== body.pattern) {
        next = await api<Macro[]>(`/api/macros/${encodeURIComponent(editingPattern)}`, { method: "DELETE" });
      }
      setMacros(next);
      notifySuccess(editingPattern ? `Updated macro ${body.pattern}` : `Added macro ${body.pattern}`);
      stopEdit();
    } catch (error) {
      notifyError(error);
    } finally {
      setSaving(false);
    }
  };

  const action = form.action;

  return (
    <Page
      title="DTMF macros"
      description="Digit patterns users can key in over the air: repeater functions like a talking clock, or commands for the link layer (e.g. *81 → disconnect_all). For control-operator functions, require a one-time code or put a PIN in the pattern."
    >
      <Card withBorder padding="lg" radius="md">
        <Table.ScrollContainer minWidth={720}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Pattern</Table.Th>
                <Table.Th>Description</Table.Th>
                <Table.Th>Action</Table.Th>
                <Table.Th>Details</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sorted.map((macro) => (
                <Table.Tr key={macro.pattern} bg={macro.pattern === editingPattern ? "var(--mantine-color-accent-light)" : undefined}>
                  <Table.Td>
                    <Group gap="xs">
                      <Code>{macro.pattern}</Code>
                      {macro.needs_code ? <Badge size="sm" variant="light">+ code</Badge> : null}
                      {macro.help_hidden && macro.action !== "help" ? (
                        <Badge size="sm" variant="light" color="gray">
                          unlisted
                        </Badge>
                      ) : null}
                    </Group>
                  </Table.Td>
                  <Table.Td>{macro.description || "—"}</Table.Td>
                  <Table.Td>{ACTION_LABELS[macro.action] ?? macro.action}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c={macro.action === "announcement" && !announcements.find((a) => a.id === macro.command) ? "amber" : undefined}>
                      {macroDetails(macro, announcements, gpioPins, airports)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="flex-end" wrap="nowrap">
                      <Button size="compact-sm" variant="subtle" onClick={() => startEdit(macro)}>
                        Edit
                      </Button>
                      <Button size="compact-sm" color="red" variant="subtle" onClick={() => void remove(macro.pattern)}>
                        Delete
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {sorted.length === 0 ? (
          <Text c="dimmed" size="sm" mt="sm">
            No macros defined yet.
          </Text>
        ) : null}
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void submit(event)}>
        <Stack gap="md">
          <Title order={2}>{editingPattern ? `Edit macro ${editingPattern}` : "Add macro"}</Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Pattern"
              placeholder="*81"
              required
              pattern="[0-9A-Da-d*#]+"
              value={form.pattern}
              onChange={(event) => setField("pattern", event.currentTarget.value)}
            />
            <Select
              label="Action"
              data={ACTION_OPTIONS}
              value={form.action}
              onChange={(value) => setField("action", value || "link")}
              allowDeselect={false}
            />
            <TextInput
              label={labeled("Description", "Spoken help reads this after the digits.")}
              placeholder="Disconnect all links"
              value={form.description}
              onChange={(event) => setField("description", event.currentTarget.value)}
            />
            {action === "help" ? (
              <NumberInput
                label={labeled("Page", "Long lists are split into pages of 8 when there\'s a help macro for the next page.")}
                min={1}
                max={99}
                value={form.help_page === "" ? undefined : Number(form.help_page)}
                onChange={(value) => setField("help_page", value === "" || value === undefined ? "" : String(value))}
              />
            ) : null}
            {action === "link" ? (
              <>
                <TextInput
                  label="Command"
                  placeholder="disconnect_all"
                  required
                  value={form.command}
                  onChange={(event) => setField("command", event.currentTarget.value)}
                />
                <TextInput
                  label="Node ID"
                  placeholder="(optional)"
                  value={form.node_id}
                  onChange={(event) => setField("node_id", event.currentTarget.value)}
                />
              </>
            ) : null}
            {action === "announcement" ? (
              <Select
                label="Announcement"
                data={announcements.map((a) => ({ value: a.id, label: a.name }))}
                value={form.announcement_id || null}
                onChange={(value) => setField("announcement_id", value || "")}
                required
                allowDeselect={false}
              />
            ) : null}
            {action === "say" ? (
              <TextInput
                label="Text to say"
                placeholder="Club meeting Thursday at 7"
                required
                value={form.say_text}
                onChange={(event) => setField("say_text", event.currentTarget.value)}
              />
            ) : null}
            {action === "gpio" ? (
              <>
                <Select
                  label="Output"
                  data={gpioOptions}
                  value={form.gpio_pin}
                  onChange={(value) => setField("gpio_pin", value || gpioOptions[0]?.value || "1")}
                  allowDeselect={false}
                />
                <Select
                  label="Switch it"
                  data={[
                    { value: "on", label: "On" },
                    { value: "off", label: "Off" },
                    { value: "toggle", label: "On if off, off if on" },
                    { value: "pulse", label: "On for a moment" },
                  ]}
                  value={form.gpio_verb}
                  onChange={(value) => setField("gpio_verb", value || "on")}
                  allowDeselect={false}
                />
                {form.gpio_verb === "pulse" ? (
                  <NumberInput
                    label="For"
                    suffix=" s"
                    min={0.1}
                    max={60}
                    step={0.1}
                    decimalScale={1}
                    value={form.gpio_seconds}
                    onChange={(value) => setField("gpio_seconds", Number(value) || 1)}
                  />
                ) : null}
              </>
            ) : null}
            {action === "homeassistant" ? (
              <TextInput
                label={labeled("Webhook ID or event", "A webhook ID, or event: and an event type (letters, digits, _ . -).")}
                placeholder="porch_light or event:repeater_porch"
                required
                pattern="(event:)?[A-Za-z0-9_.\-]{1,100}"
                value={form.ha_target}
                onChange={(event) => setField("ha_target", event.currentTarget.value)}
              />
            ) : null}
            {action === "metar" ? (
              <Select
                label={labeled("Airport", "Airports are listed on the Weather page.")}
                data={airportOptions}
                value={form.metar_icao}
                onChange={(value) => setField("metar_icao", value ?? "")}
              />
            ) : null}
            {action === "remote_shift" ? (
              <Select
                label="Shift"
                data={[
                  { value: "minus", label: "Minus" },
                  { value: "plus", label: "Plus" },
                  { value: "simplex", label: "Simplex" },
                ]}
                value={form.remote_shift}
                onChange={(value) => setField("remote_shift", value || "minus")}
                allowDeselect={false}
              />
            ) : null}
          </SimpleGrid>
          {action === "remote_tune" ? (
            <Text c="dimmed" size="sm">
              After the pattern, key the frequency in MHz with * as the decimal point, then #: 146*94#. Six digits work too (146940#).
            </Text>
          ) : null}
          {action === "remote_tone" ? (
            <Text c="dimmed" size="sm">
              After the pattern, key the tone with * as the decimal point, then #: 100*0# or 1000#. 0# turns the tone off.
            </Text>
          ) : null}
          <Checkbox
            label="Needs a one-time code, keyed right after the pattern"
            checked={form.needs_code}
            onChange={(event) => setField("needs_code", event.currentTarget.checked)}
          />
          <Checkbox
            label="Hide from spoken help (on by default for the transmitter and link radio switches, and for macros that need a code)"
            checked={form.help_hidden}
            onChange={(event) => {
              setHelpHiddenTouched(true);
              setField("help_hidden", event.currentTarget.checked);
            }}
          />
          <Group justify="flex-end" gap="sm">
            {editingPattern ? (
              <Button type="button" variant="subtle" onClick={stopEdit}>
                Cancel edit
              </Button>
            ) : null}
            <Button type="submit" loading={saving}>
              {editingPattern ? "Save changes" : "Add macro"}
            </Button>
          </Group>
        </Stack>
      </Card>

      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void ha.submit();
        }}
      >
        <Stack gap="md">
          <Title order={2}>Home Assistant</Title>
          <Text c="dimmed" size="sm" maw="75ch">
            A Home Assistant macro calls a webhook trigger or fires an event. Anyone who hears the digits can key them again, so give these macros a one-time code or a PIN.
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label={labeled("Home Assistant address", "As this controller reaches it.")}
              placeholder="http://homeassistant.local:8123"
              pattern="https?://\S+"
              value={ha.values.homeassistant_url}
              onChange={(event) => ha.setField("homeassistant_url", event.currentTarget.value)}
            />
            <Checkbox
              mt="xl"
              label='Say "Done" or "Failed" afterwards'
              checked={ha.values.homeassistant_say_result}
              onChange={(event) => ha.setField("homeassistant_say_result", event.currentTarget.checked)}
            />
          </SimpleGrid>
          <ConfigActions dirty={ha.dirty} saving={ha.saving} onReset={ha.reset} />
        </Stack>
      </Card>
    </Page>
  );
}
