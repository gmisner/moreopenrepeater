import {
  Accordion,
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, notifySuccess, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { labeled } from "../components/FieldHint";

const REFRESH_MS = 1000;
const TRUNK_REFRESH_MS = 5000;

const TWILIO_SIGNALING = [
  "54.172.60.0/30",
  "54.244.51.0/30",
  "54.171.127.192/30",
  "35.156.191.128/30",
  "54.65.63.192/30",
  "54.169.127.128/30",
  "54.252.254.64/30",
  "177.71.206.192/30",
];

const PROVIDERS: Record<
  string,
  { placeholder?: string; server?: string; prefix: string; registers: boolean; incoming?: string[]; hint: string }
> = {
  voipms: {
    placeholder: "e.g. atlanta.voip.ms",
    prefix: "1",
    registers: true,
    hint: "The server your account or number is set to, and your 6-digit account ID or a sub-account (123456_repeater).",
  },
  telnyx: {
    server: "sip.telnyx.com",
    prefix: "+1",
    registers: true,
    hint: "The username and password of a Credentials connection.",
  },
  twilio: {
    placeholder: "yourtrunk.pstn.twilio.com",
    prefix: "+1",
    registers: false,
    incoming: TWILIO_SIGNALING,
    hint: "The trunk's termination URI, and a user from its credential list. Turn on Symmetric RTP. Caller ID must be a Twilio or verified number.",
  },
  other: {
    placeholder: "sip.example.com",
    prefix: "",
    registers: true,
    hint: "Your provider's Asterisk (PJSIP) setup guide lists these.",
  },
};

const SIP_MODULE = /pjsip|pjproject|rtp|sorcery|codec/;

type AutopatchCall = {
  number?: string | null;
  actor: string;
  state: "dialing" | "connected" | "ended";
  started_at: number;
  connected_at?: number | null;
  ended_at?: number | null;
  result: string;
  direction: "outgoing" | "incoming";
};

type AutopatchStatus = {
  available: boolean;
  configured: boolean;
  error: string | null;
  enabled: boolean;
  call: AutopatchCall | null;
  last_call: AutopatchCall | null;
};

type TrunkSettings = {
  server: string;
  port: number;
  transport: "udp" | "tcp";
  username: string;
  auth_username: string;
  has_password: boolean;
  registers: boolean;
  incoming_from: string[];
  answers_calls: boolean;
};

type TrunkStatus = {
  configured: boolean;
  error: string | null;
  missing_modules: string[];
  trunk: TrunkSettings | null;
  registration: string | null;
  reachability: string | null;
  in_use: boolean;
  ten_digit_prefix: string;
};

type TrunkForm = {
  provider: string;
  server: string;
  port: number;
  transport: string;
  username: string;
  auth_username: string;
  password: string;
  registers: boolean;
  ten_digit_prefix: string;
  incoming_from: string;
};

type PatchValues = {
  autopatch_enabled: boolean;
  autopatch_access_code: string;
  autopatch_hangup_code: string;
  autopatch_allowed: string;
  autopatch_blocked: string;
  autopatch_max_call_seconds: number;
  autopatch_ring_seconds: number;
  autopatch_dial_string: string;
  autopatch_caller_id: string;
};

type IncomingValues = {
  autopatch_incoming_enabled: boolean;
  autopatch_incoming_pin: string;
};

const PATCH_KEYS = [
  "autopatch_enabled",
  "autopatch_access_code",
  "autopatch_hangup_code",
  "autopatch_allowed",
  "autopatch_blocked",
  "autopatch_max_call_seconds",
  "autopatch_ring_seconds",
  "autopatch_dial_string",
  "autopatch_caller_id",
] as (keyof PatchValues)[];

const PATCH_DEFAULTS: PatchValues = {
  autopatch_enabled: false,
  autopatch_access_code: "*6",
  autopatch_hangup_code: "#",
  autopatch_allowed: "911 NXXNXXXXXX",
  autopatch_blocked: "900XXXXXXX NXX976XXXX",
  autopatch_max_call_seconds: 180,
  autopatch_ring_seconds: 30,
  autopatch_dial_string: "PJSIP/{number}@mor-trunk",
  autopatch_caller_id: "",
};

const INCOMING_KEYS = ["autopatch_incoming_enabled", "autopatch_incoming_pin"] as (keyof IncomingValues)[];
const INCOMING_DEFAULTS: IncomingValues = {
  autopatch_incoming_enabled: false,
  autopatch_incoming_pin: "",
};

function clock(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function time(epochSeconds: number) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function pillColor(kind?: string) {
  if (kind === "on") return "green";
  if (kind === "warn") return "amber";
  return "gray";
}

function providerFor(server: string) {
  if (/(^|\.)voip\.ms$/i.test(server)) return "voipms";
  if (/(^|\.)telnyx\.com$/i.test(server)) return "telnyx";
  if (/\.pstn\.twilio\.com$/i.test(server)) return "twilio";
  return "other";
}

function describeCall(status: AutopatchStatus): string {
  const { call, last_call: last } = status;
  if (!status.configured) {
    return "Asterisk isn't connected. Set MOREOPENREPEATER_AMI_HOST and the other AMI settings in the service's environment (see the setup guide).";
  }
  if (status.error) return status.error;
  if (call) {
    const number = call.number || "an unknown number";
    const talking = clock(Date.now() / 1000 - (call.connected_at ?? call.started_at));
    if (call.direction === "incoming") return `On a call from ${number} for ${talking}.`;
    const by = call.actor === "DTMF" ? "over the air" : `by ${call.actor}`;
    if (call.state === "dialing") return `Dialing ${number} (started ${by})…`;
    return `Connected to ${number} for ${talking} (started ${by}).`;
  }
  const off = status.enabled ? "" : " Autopatch is turned off.";
  if (!last) return `No calls yet.${off}`;
  const talk = last.connected_at && last.ended_at ? `, ${clock(last.ended_at - last.connected_at)}` : "";
  const who = last.direction === "incoming" ? `from ${last.number || "an unknown number"}` : last.number;
  return `Last call: ${who} at ${time(last.started_at)} — ${last.result}${talk}.${off}`;
}

function describeTrunk(
  status: TrunkStatus,
  isAdmin: boolean,
): { text: string; pill?: [string, string]; needsModules?: boolean } {
  const { trunk } = status;
  if (!status.configured) {
    return {
      text: "The phone line settings appear here once the controller can reach Asterisk. Set MOREOPENREPEATER_AMI_HOST, MOREOPENREPEATER_AMI_USER and MOREOPENREPEATER_AMI_SECRET in the service's environment and restart it.",
      pill: ["Not connected", "off"],
    };
  }
  if (status.error) return { text: status.error };
  const missing = status.missing_modules;
  if (missing.length) {
    const what = missing.some((m) => SIP_MODULE.test(m))
      ? "SIP"
      : missing.some((m) => m.includes("audiosocket"))
        ? "AudioSocket"
        : "Part of the dialplan autopatch uses";
    const who = isAdmin ? "Turning it on" : "An admin can turn it on here, which";
    return {
      text: `${what} is turned off in Asterisk. ${who} also sets it to load whenever Asterisk starts.`,
      needsModules: true,
    };
  }
  if (!trunk) {
    const next = isAdmin ? "Enter your SIP provider's details." : "An admin can set one up here.";
    return { text: `No phone line yet. ${next}` };
  }
  const line = `${trunk.username} at ${trunk.server}`;
  let unused = status.in_use ? "" : " Autopatch isn't using it: the dial string below points somewhere else.";
  if (!trunk.answers_calls) unused += " Save it again to answer calls in.";
  if (trunk.registers) {
    const registration = status.registration ?? "Unknown";
    if (registration === "Registered") return { text: `Registered as ${line}.${unused}`, pill: ["Registered", "on"] };
    if (registration.startsWith("Rejected")) {
      return {
        text: `The provider rejected the login for ${line}. Check the username and password.${unused}`,
        pill: ["Rejected", "warn"],
      };
    }
    return {
      text: `Registering ${line}… Asterisk retries every minute if the provider doesn't answer.${unused}`,
      pill: ["Not registered", "off"],
    };
  }
  if (status.reachability === "Reachable") return { text: `Calls go to ${line}.${unused}`, pill: ["Reachable", "on"] };
  if (status.reachability === "Unreachable") {
    return { text: `Asterisk can't reach ${line}.${unused}`, pill: ["Unreachable", "warn"] };
  }
  return { text: `Calls go to ${line}.${unused}` };
}

function emptyTrunkForm(): TrunkForm {
  return {
    provider: "voipms",
    server: "",
    port: 5060,
    transport: "udp",
    username: "",
    auth_username: "",
    password: "",
    registers: true,
    ten_digit_prefix: "1",
    incoming_from: "",
  };
}

function trunkToForm(trunk: TrunkSettings | null, prefix: string): TrunkForm {
  if (!trunk) {
    const form = emptyTrunkForm();
    const provider = PROVIDERS.voipms;
    form.ten_digit_prefix = provider.prefix;
    form.registers = provider.registers;
    return form;
  }
  return {
    provider: providerFor(trunk.server),
    server: trunk.server,
    port: trunk.port,
    transport: trunk.transport,
    username: trunk.username,
    auth_username: trunk.auth_username,
    password: "",
    registers: trunk.registers,
    ten_digit_prefix: prefix,
    incoming_from: (trunk.incoming_from ?? []).join(" "),
  };
}

export function Autopatch() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const patch = useConfigSlice<PatchValues>(PATCH_KEYS, PATCH_DEFAULTS);
  const incoming = useConfigSlice<IncomingValues>(INCOMING_KEYS, INCOMING_DEFAULTS);

  const [status, setStatus] = useState<AutopatchStatus | null>(null);
  const [number, setNumber] = useState("");
  const [dialBusy, setDialBusy] = useState(false);
  const [hangupBusy, setHangupBusy] = useState(false);

  const [trunk, setTrunk] = useState<TrunkStatus | null>(null);
  const [trunkForm, setTrunkForm] = useState<TrunkForm>(emptyTrunkForm());
  const [trunkDirty, setTrunkDirty] = useState(false);
  const [trunkBusy, setTrunkBusy] = useState(false);
  const trunkDirtyRef = useRef(false);
  const savedTrunkKeyRef = useRef("");
  const trunkFormRef = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    trunkDirtyRef.current = trunkDirty;
  }, [trunkDirty]);

  const refresh = useCallback(async () => {
    setStatus(await api<AutopatchStatus>("/api/autopatch"));
  }, []);

  const refreshTrunk = useCallback(async () => {
    const next = await api<TrunkStatus>("/api/autopatch/trunk");
    setTrunk(next);
    const key = JSON.stringify(next.trunk);
    const focusedInForm = trunkFormRef.current?.contains(document.activeElement) ?? false;
    if ((key !== savedTrunkKeyRef.current || !trunkDirtyRef.current) && !focusedInForm) {
      setTrunkForm(trunkToForm(next.trunk, next.ten_digit_prefix));
      setTrunkDirty(false);
    }
    savedTrunkKeyRef.current = key;
  }, []);

  useEffect(() => {
    refresh().catch(notifyError);
    refreshTrunk().catch(notifyError);
  }, [refresh, refreshTrunk]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (!document.hidden) refresh().catch(() => {});
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (!document.hidden) refreshTrunk().catch(() => {});
    }, TRUNK_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refreshTrunk]);

  const setTrunkField = <K extends keyof TrunkForm>(name: K, value: TrunkForm[K]) => {
    setTrunkForm((prev) => ({ ...prev, [name]: value }));
    setTrunkDirty(true);
  };

  const chooseProvider = (key: string) => {
    const provider = PROVIDERS[key];
    const fixedServers = Object.values(PROVIDERS)
      .map((p) => p.server)
      .filter(Boolean) as string[];
    setTrunkForm((prev) => {
      let server = prev.server;
      if (provider.server) server = provider.server;
      else if (fixedServers.includes(server)) server = "";
      return {
        ...prev,
        provider: key,
        server,
        ten_digit_prefix: provider.prefix,
        registers: provider.registers,
        incoming_from: (provider.incoming ?? []).join(" "),
      };
    });
    setTrunkDirty(true);
  };

  const recognizeServer = (server: string) => {
    const detected = providerFor(server.trim());
    if (detected === "other" || detected === trunkForm.provider) return;
    const provider = PROVIDERS[detected];
    setTrunkForm((prev) => ({
      ...prev,
      provider: detected,
      ten_digit_prefix: provider.prefix,
      registers: provider.registers,
      incoming_from: (provider.incoming ?? []).join(" "),
    }));
    setTrunkDirty(true);
  };

  const dial = async () => {
    const cleaned = number.replace(/[^0-9+]/g, "");
    if (!cleaned) return;
    setDialBusy(true);
    try {
      setStatus(await api<AutopatchStatus>("/api/autopatch/dial", { method: "POST", json: { number: cleaned } }));
    } catch (error) {
      notifyError(error);
      await refresh().catch(() => {});
    } finally {
      setDialBusy(false);
    }
  };

  const hangup = async () => {
    setHangupBusy(true);
    try {
      setStatus(await api<AutopatchStatus>("/api/autopatch/hangup", { method: "POST" }));
    } catch (error) {
      notifyError(error);
    } finally {
      setHangupBusy(false);
    }
  };

  const saveTrunk = async (event: FormEvent) => {
    event.preventDefault();
    setTrunkBusy(true);
    try {
      const next = await api<TrunkStatus>("/api/autopatch/trunk", {
        method: "PUT",
        json: {
          server: trunkForm.server.trim(),
          port: Number(trunkForm.port),
          transport: trunkForm.transport,
          username: trunkForm.username.trim(),
          auth_username: trunkForm.auth_username.trim(),
          password: trunkForm.password,
          registers: trunkForm.registers,
          ten_digit_prefix: trunkForm.ten_digit_prefix,
          incoming_from: trunkForm.incoming_from.split(/[\s,]+/).filter(Boolean),
        },
      });
      setTrunk(next);
      setTrunkForm(trunkToForm(next.trunk, next.ten_digit_prefix));
      setTrunkDirty(false);
      savedTrunkKeyRef.current = JSON.stringify(next.trunk);
      notifySuccess("Phone line saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setTrunkBusy(false);
    }
  };

  const removeTrunk = async () => {
    if (!window.confirm("Remove the phone line from Asterisk? Autopatch calls will fail until one is set up again.")) return;
    setTrunkBusy(true);
    try {
      const next = await api<TrunkStatus>("/api/autopatch/trunk", { method: "DELETE" });
      setTrunk(next);
      setTrunkForm(trunkToForm(next.trunk, next.ten_digit_prefix));
      setTrunkDirty(false);
      savedTrunkKeyRef.current = JSON.stringify(next.trunk);
      notifySuccess("Phone line removed");
    } catch (error) {
      notifyError(error);
    } finally {
      setTrunkBusy(false);
    }
  };

  const enableModules = async () => {
    setTrunkBusy(true);
    try {
      setTrunk(await api<TrunkStatus>("/api/autopatch/trunk/modules", { method: "POST" }));
      notifySuccess("SIP is on in Asterisk");
    } catch (error) {
      notifyError(error);
      refreshTrunk().catch(() => {});
    } finally {
      setTrunkBusy(false);
    }
  };

  const trunkView = trunk ? describeTrunk(trunk, isAdmin) : null;
  const showTrunkForm = isAdmin && trunk?.configured && !trunk.error && !trunkView?.needsModules;
  const provider = PROVIDERS[trunkForm.provider] ?? PROVIDERS.other;

  return (
    <Page
      title="Autopatch"
      description="Let users place phone calls through the repeater. Calls go out through a SIP phone line in the Asterisk sidecar; see the setup guide."
    >
      <Card withBorder padding="lg" radius="md">
        <Title order={2} mb="sm">
          Call
        </Title>
        <Text c="dimmed" size="sm">
          {status ? describeCall(status) : "Loading…"}
        </Text>
        <Group mt="md" align="flex-end" wrap="wrap">
          <TextInput
            style={{ flex: 1, minWidth: 180 }}
            placeholder="Number to call"
            inputMode="numeric"
            autoComplete="off"
            value={number}
            onChange={(event) => setNumber(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !status?.call && status?.available) {
                event.preventDefault();
                void dial();
              }
            }}
          />
          <Button variant="light" loading={dialBusy} disabled={Boolean(status?.call) || !status?.available} onClick={() => void dial()}>
            Test call
          </Button>
          {status?.call ? (
            <Button color="red" loading={hangupBusy} onClick={() => void hangup()}>
              Hang up
            </Button>
          ) : null}
        </Group>
        <Text c="dimmed" size="sm" mt="sm">
          A test call goes out over the air like a DTMF-dialed one, and follows the same rules.
        </Text>
      </Card>

      <Card withBorder padding="lg" radius="md">
        <Group justify="space-between" align="flex-start" mb="sm">
          <Title order={2}>Phone line</Title>
          {trunkView?.pill ? (
            <Badge color={pillColor(trunkView.pill[1])} variant="light">
              {trunkView.pill[0]}
            </Badge>
          ) : null}
        </Group>
        <Text c="dimmed" size="sm">
          {trunkView?.text ?? "Loading…"}
        </Text>
        {trunkView?.needsModules && isAdmin ? (
          <Group justify="flex-end" mt="md">
            <Button loading={trunkBusy} onClick={() => void enableModules()}>
              Turn on SIP in Asterisk
            </Button>
          </Group>
        ) : null}
        {showTrunkForm ? (
          <form
            ref={trunkFormRef}
            onSubmit={(event) => void saveTrunk(event)}
          >
            <Stack gap="md" mt="md">
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <Select
                  label={labeled("Provider", provider.hint)}
                  data={[
                    { value: "voipms", label: "VoIP.ms" },
                    { value: "telnyx", label: "Telnyx" },
                    { value: "twilio", label: "Twilio Elastic SIP Trunking" },
                    { value: "other", label: "Other" },
                  ]}
                  value={trunkForm.provider}
                  onChange={(value) => chooseProvider(value || "other")}
                  allowDeselect={false}
                />
                <TextInput
                  label="Server"
                  required
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={provider.placeholder}
                  value={trunkForm.server}
                  onChange={(event) => setTrunkField("server", event.currentTarget.value)}
                  onBlur={(event) => recognizeServer(event.currentTarget.value)}
                />
                <TextInput
                  label="Username"
                  required
                  autoComplete="off"
                  spellCheck={false}
                  value={trunkForm.username}
                  onChange={(event) => setTrunkField("username", event.currentTarget.value)}
                />
                <TextInput
                  label="Password"
                  type="password"
                  autoComplete="new-password"
                  required={!trunk?.trunk?.has_password}
                  placeholder={trunk?.trunk?.has_password ? "Saved; leave blank to keep it" : undefined}
                  value={trunkForm.password}
                  onChange={(event) => setTrunkField("password", event.currentTarget.value)}
                />
                <Select
                  label={labeled("Number format", "How 10-digit numbers are sent. 911 always goes out as dialed.")}
                  data={[
                    { value: "", label: "As dialed" },
                    { value: "1", label: "1 + 10 digits" },
                    { value: "+1", label: "+1 + 10 digits" },
                  ]}
                  value={trunkForm.ten_digit_prefix}
                  onChange={(value) => setTrunkField("ten_digit_prefix", value ?? "")}
                  allowDeselect={false}
                />
              </SimpleGrid>
              <Accordion variant="contained">
                <Accordion.Item value="more">
                  <Accordion.Control>More settings</Accordion.Control>
                  <Accordion.Panel>
                    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                      <TextInput
                        label={labeled("Login name", "Only if the provider gives a separate one.")}
                        autoComplete="off"
                        spellCheck={false}
                        placeholder="Same as username"
                        value={trunkForm.auth_username}
                        onChange={(event) => setTrunkField("auth_username", event.currentTarget.value)}
                      />
                      <NumberInput
                        label="Port"
                        min={1}
                        max={65535}
                        required
                        value={trunkForm.port}
                        onChange={(value) => setTrunkField("port", Number(value) || 5060)}
                      />
                      <Select
                        label="Transport"
                        data={[
                          { value: "udp", label: "UDP" },
                          { value: "tcp", label: "TCP" },
                        ]}
                        value={trunkForm.transport}
                        onChange={(value) => setTrunkField("transport", value || "udp")}
                        allowDeselect={false}
                      />
                      <TextInput
                        label={labeled("Calls in come from", "Addresses or networks, separated by spaces, that your provider sends calls from besides the server.")}
                        autoComplete="off"
                        spellCheck={false}
                        placeholder="Just the server"
                        value={trunkForm.incoming_from}
                        onChange={(event) => setTrunkField("incoming_from", event.currentTarget.value)}
                      />
                    </SimpleGrid>
                  </Accordion.Panel>
                </Accordion.Item>
              </Accordion>
              <Switch
                label="Register with the provider"
                checked={trunkForm.registers}
                onChange={(event) => setTrunkField("registers", event.currentTarget.checked)}
              />
              <Group justify="space-between">
                {trunk?.trunk ? (
                  <Button type="button" color="red" variant="subtle" loading={trunkBusy} onClick={() => void removeTrunk()}>
                    Remove
                  </Button>
                ) : (
                  <span />
                )}
                <Button type="submit" loading={trunkBusy}>
                  Save phone line
                </Button>
              </Group>
              <Text c="dimmed" size="sm">
                Saving writes the line to Asterisk&apos;s pjsip.conf and points autopatch at it. The first save also restarts
                Asterisk.
              </Text>
            </Stack>
          </form>
        ) : null}
      </Card>

      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void patch.submit();
        }}
      >
        <Stack gap="md">
          <Switch
            label="Allow autopatch calls"
            checked={patch.values.autopatch_enabled}
            onChange={(event) => patch.setField("autopatch_enabled", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label={labeled("Access code", "Users send this, the number, then # (or just unkey, or wait 5 seconds).")}
              required
              autoComplete="off"
              value={patch.values.autopatch_access_code}
              onChange={(event) => patch.setField("autopatch_access_code", event.currentTarget.value)}
            />
            <TextInput
              label={labeled("Hang-up code", "Ends the call while one is up.")}
              required
              autoComplete="off"
              value={patch.values.autopatch_hangup_code}
              onChange={(event) => patch.setField("autopatch_hangup_code", event.currentTarget.value)}
            />
          </SimpleGrid>
          <Accordion variant="contained" multiple>
            <Accordion.Item value="numbers">
              <Accordion.Control>Numbers users can call</Accordion.Control>
              <Accordion.Panel>
                <Stack gap="md">
                  <TextInput
                    label={labeled("Allowed numbers", "Patterns separated by spaces: X is any digit, N is 2–9, Z is 1–9.")}
                    autoComplete="off"
                    value={patch.values.autopatch_allowed}
                    onChange={(event) => patch.setField("autopatch_allowed", event.currentTarget.value)}
                  />
                  <TextInput
                    label={labeled("Blocked numbers", "Checked after the allowed list.")}
                    autoComplete="off"
                    value={patch.values.autopatch_blocked}
                    onChange={(event) => patch.setField("autopatch_blocked", event.currentTarget.value)}
                  />
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="length">
              <Accordion.Control>Call length</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <NumberInput
                    label={labeled("Maximum call length", "Users hear a warning 30 seconds before the end.")}
                    suffix=" s"
                    min={30}
                    max={3600}
                    step={30}
                    required
                    value={patch.values.autopatch_max_call_seconds}
                    onChange={(value) => patch.setField("autopatch_max_call_seconds", Number(value) || 0)}
                  />
                  <NumberInput
                    label="Ring for"
                    suffix=" s"
                    min={5}
                    max={120}
                    step={5}
                    required
                    value={patch.values.autopatch_ring_seconds}
                    onChange={(value) => patch.setField("autopatch_ring_seconds", Number(value) || 0)}
                  />
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="dialing">
              <Accordion.Control>Dialing and caller ID</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <TextInput
                    label={labeled("Dial string", "{number} becomes the dialed digits. PJSIP/{number}@mor-trunk is the phone line above.")}
                    required
                    autoComplete="off"
                    value={patch.values.autopatch_dial_string}
                    onChange={(event) => patch.setField("autopatch_dial_string", event.currentTarget.value)}
                  />
                  <TextInput
                    label={labeled("Caller ID", "e.g. \"W1AW Repeater\" <8605550100>")}
                    autoComplete="off"
                    value={patch.values.autopatch_caller_id}
                    onChange={(event) => patch.setField("autopatch_caller_id", event.currentTarget.value)}
                  />
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>
          <ConfigActions dirty={patch.dirty} saving={patch.saving} onReset={patch.reset} />
        </Stack>
      </Card>

      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void incoming.submit();
        }}
      >
        <Stack gap="md">
          <Title order={2}>Calls in</Title>
          <Switch
            label="Answer calls to the phone line"
            checked={incoming.values.autopatch_incoming_enabled}
            onChange={(event) => incoming.setField("autopatch_incoming_enabled", event.currentTarget.checked)}
          />
          <TextInput
            label={labeled("Access code", "Callers key it in, then #. Calls aren\'t answered without one.")}
            inputMode="numeric"
            autoComplete="off"
            placeholder="4 to 8 digits"
            required={incoming.values.autopatch_incoming_enabled}
            value={incoming.values.autopatch_incoming_pin}
            onChange={(event) => incoming.setField("autopatch_incoming_pin", event.currentTarget.value)}
            maw={320}
          />
          <Text c="dimmed" size="sm" maw="75ch">
            Callers get three tries at the code. Then the repeater says “Incoming phone call” and puts them on the air, with
            the same time limit and hang-up code as calls out.
          </Text>
          <ConfigActions dirty={incoming.dirty} saving={incoming.saving} onReset={incoming.reset} />
        </Stack>
      </Card>
    </Page>
  );
}
