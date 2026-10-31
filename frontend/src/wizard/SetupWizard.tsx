import {
  Badge,
  Box,
  Button,
  Group,
  Modal,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api";
import { notifyError, notifySuccess, useConfig, type Config } from "../config";
import { useSession } from "../session";

const BY_HAND = "";
const POLL_MS = 400;

type Board = {
  id: string;
  name: string;
  maker: string;
  kind: "usb" | "pi";
  notes: string;
  device_hints: string[];
  two_port: boolean;
  mixer: [string, string][];
  unsupported: string | null;
};

type AudioDevice = { name: string; inputs: number; outputs: number };
type EngineStatus = {
  running: boolean;
  error: string | null;
  cos_open: boolean;
  transmitting: boolean;
};
type MixerApplyResult = {
  config: Config;
  mixer: { card: number; control: string; value: string; error: string | null }[];
  mixer_skipped: string[];
};

type StepId = "station" | "board" | "devices" | "test" | "account";

const STEPS: { id: StepId; title: string; adminOnly?: boolean }[] = [
  { id: "station", title: "Welcome to moreopenrepeater" },
  { id: "board", title: "Radio interface" },
  { id: "devices", title: "Sound devices" },
  { id: "test", title: "Test the transmitter" },
  { id: "account", title: "Your admin account", adminOnly: true },
];

type WizardContextValue = {
  openWizard: () => void;
};

const WizardContext = createContext<WizardContextValue | null>(null);

export function useSetupWizard() {
  const ctx = useContext(WizardContext);
  if (!ctx) throw new Error("useSetupWizard requires SetupWizardProvider");
  return ctx;
}

function deviceOptions(devices: AudioDevice[], kind: "inputs" | "outputs", current: string) {
  const names = devices.filter((d) => d[kind] > 0).map((d) => d.name);
  const data = [
    { value: "", label: "System default" },
    ...names.map((name) => ({ value: name, label: name })),
  ];
  if (current && !names.includes(current)) {
    data.push({ value: current, label: `${current} (not connected)` });
  }
  return data;
}

function pickDevice(
  devices: AudioDevice[],
  board: Board | null,
  kind: "inputs" | "outputs",
  saved: string,
  skip = 0,
) {
  if (saved && devices.some((d) => d.name === saved && d[kind] > 0)) return saved;
  const hints = (board?.device_hints ?? []).map((h) => h.toLowerCase());
  const matches = devices.filter((d) => d[kind] > 0 && hints.some((h) => d.name.toLowerCase().includes(h)));
  return matches[skip]?.name ?? saved;
}

function boardSelectData(boards: Board[]) {
  const groups: { group: string; items: { value: string; label: string }[] }[] = [
    {
      group: "USB interfaces",
      items: boards
        .filter((b) => b.kind === "usb" && !b.unsupported)
        .map((b) => ({ value: b.id, label: b.maker ? `${b.name} (${b.maker})` : b.name })),
    },
    {
      group: "Raspberry Pi boards",
      items: boards
        .filter((b) => b.kind === "pi" && !b.unsupported)
        .map((b) => ({ value: b.id, label: b.maker ? `${b.name} (${b.maker})` : b.name })),
    },
    {
      group: "Not supported yet",
      items: boards
        .filter((b) => b.unsupported)
        .map((b) => ({ value: b.id, label: b.maker ? `${b.name} (${b.maker})` : b.name })),
    },
  ];
  return [
    { value: BY_HAND, label: "None of these: I'll set it up myself" },
    ...groups.filter((g) => g.items.length > 0),
  ];
}

export function SetupWizardProvider({ children }: { children: ReactNode }) {
  const { config, save, refresh } = useConfig();
  const { session } = useSession();
  const [opened, setOpened] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [boards, setBoards] = useState<Board[]>([]);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [mixerLines, setMixerLines] = useState<string[]>([]);
  const autoOpened = useRef(false);

  const [callsign, setCallsign] = useState("");
  const [idMode, setIdMode] = useState("voice");
  const [boardId, setBoardId] = useState(BY_HAND);
  const [setMixer, setSetMixer] = useState(true);
  const [inputDevice, setInputDevice] = useState("");
  const [outputDevice, setOutputDevice] = useState("");
  const [linkOn, setLinkOn] = useState(false);
  const [linkInput, setLinkInput] = useState("");
  const [linkOutput, setLinkOutput] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");

  const selectedBoard = useMemo(() => boards.find((b) => b.id === boardId) ?? null, [boards, boardId]);
  const visibleSteps = useMemo(
    () => STEPS.filter((step) => !step.adminOnly || session?.role === "admin"),
    [session?.role],
  );
  const current = visibleSteps[stepIndex] ?? visibleSteps[0];

  const loadCatalog = useCallback(async () => {
    const [boardList, deviceList] = await Promise.all([
      api<Board[]>("/api/boards"),
      api<AudioDevice[]>("/api/audio/devices").catch(() => [] as AudioDevice[]),
    ]);
    setBoards(boardList);
    setDevices(deviceList);
    return { boardList, deviceList };
  }, []);

  const openWizard = useCallback(async () => {
    try {
      const cfg = config ?? (await refresh());
      const { boardList, deviceList } = await loadCatalog();
      const board = boardList.find((b) => b.id === (cfg.board_preset as string)) ?? null;
      setCallsign(String(cfg.callsign ?? ""));
      setIdMode(String(cfg.id_mode ?? "voice"));
      setBoardId(String(cfg.board_preset ?? BY_HAND));
      setSetMixer(true);
      setInputDevice(pickDevice(deviceList, board, "inputs", String(cfg.audio_input_device ?? "")));
      setOutputDevice(pickDevice(deviceList, board, "outputs", String(cfg.audio_output_device ?? "")));
      setLinkOn(Boolean(board?.two_port && cfg.link_radio_enabled));
      setLinkInput(pickDevice(deviceList, board, "inputs", String(cfg.link_radio_input_device ?? ""), 1));
      setLinkOutput(pickDevice(deviceList, board, "outputs", String(cfg.link_radio_output_device ?? ""), 1));
      setUsername("");
      setPassword("");
      setPassword2("");
      setMixerLines([]);
      setError(null);
      setStepIndex(0);
      setOpened(true);
    } catch (err) {
      notifyError(err);
    }
  }, [config, loadCatalog, refresh]);

  useEffect(() => {
    if (autoOpened.current || !config || session?.role !== "admin") return;
    if (config.setup_wizard_done) return;
    autoOpened.current = true;
    void openWizard();
  }, [config, openWizard, session?.role]);

  useEffect(() => {
    if (!opened || current?.id !== "test") return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await api<EngineStatus>("/api/audio/engine");
        if (!cancelled) setEngine(next);
      } catch {
        /* retry */
      }
      if (!cancelled) timer = window.setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [opened, current?.id]);

  useEffect(() => {
    if (!selectedBoard?.two_port) setLinkOn(false);
  }, [selectedBoard]);

  const finish = async () => {
    await save({ setup_wizard_done: true });
    setOpened(false);
  };

  const leaveStep = async (id: StepId) => {
    if (id === "station") {
      const next = callsign.trim().toUpperCase();
      if (!next) throw new Error("Enter the station's callsign.");
      setCallsign(next);
      await save({ callsign: next, id_mode: idMode });
      return;
    }
    if (id === "board") return;
    if (id === "devices") {
      const linkEnabled = Boolean(selectedBoard?.two_port && linkOn);
      if (linkEnabled && (!linkInput || !linkOutput)) {
        throw new Error("Pick the link radio's input and output.");
      }
      if (!selectedBoard) {
        await save({
          audio_enabled: true,
          audio_input_device: inputDevice,
          audio_output_device: outputDevice,
        });
        setMixerLines([]);
        return;
      }
      const result = await api<MixerApplyResult>(`/api/boards/${encodeURIComponent(selectedBoard.id)}/apply`, {
        method: "POST",
        json: {
          input_device: inputDevice,
          output_device: outputDevice,
          ...(linkEnabled ? { link_input_device: linkInput, link_output_device: linkOutput } : {}),
          set_mixer: setMixer,
        },
      });
      await refresh();
      setMixerLines([
        ...result.mixer.map((r) =>
          r.error ? `Card ${r.card}: couldn't set ${r.control}: ${r.error}` : `Card ${r.card}: ${r.control} set to ${r.value}`,
        ),
        ...result.mixer_skipped.map((name) => `Levels not set on ${name}: pick a specific sound card to set them.`),
      ]);
      return;
    }
    if (id === "test") return;
    if (id === "account") {
      const user = username.trim();
      if (!user && !password) return;
      if (!user) throw new Error("Enter a username, or clear the password to skip this.");
      if (password.length < 8) throw new Error("Use a password of at least 8 characters.");
      if (password !== password2) throw new Error("The passwords don't match.");
      const signInWasOn = Boolean(session?.auth_required);
      await save({ setup_wizard_done: true });
      await api("/api/users", { method: "POST", json: { username: user, password, role: "admin" } });
      if (!signInWasOn) {
        location.replace("/login");
        return "left";
      }
      notifySuccess(`Admin account ${user} added`);
    }
  };

  const goNext = async () => {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      const outcome = await leaveStep(current.id);
      if (outcome === "left") return;
      if (stepIndex >= visibleSteps.length - 1) await finish();
      else setStepIndex((i) => i + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const sendTestId = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/audio/test-id", { method: "POST" });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const boardUnsupported = Boolean(selectedBoard?.unsupported);
  const mixerLabel = selectedBoard?.mixer.length
    ? `(${selectedBoard.mixer.map(([c, v]) => `${c} ${v}`).join(", ")})`
    : "";

  const value = useMemo(() => ({ openWizard: () => void openWizard() }), [openWizard]);

  return (
    <WizardContext.Provider value={value}>
      {children}
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        size="lg"
        title={
          <Group justify="space-between" w="100%" pr="xl">
            <Text size="sm" c="dimmed">
              Step {stepIndex + 1} of {visibleSteps.length}
            </Text>
            <Button
              variant="subtle"
              size="compact-sm"
              onClick={() => {
                void finish().catch((err) => setError(err instanceof Error ? err.message : String(err)));
              }}
            >
              Skip setup
            </Button>
          </Group>
        }
        closeOnClickOutside={false}
      >
        <Stack gap="md">
          <Title order={2}>{current?.title}</Title>

          {current?.id === "station" ? (
            <Stack gap="md">
              <Text c="dimmed" size="sm">
                A few questions get the repeater on the air. Everything here can be changed later, and you can run this again from Audio & tones.
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <TextInput
                  label="Callsign"
                  placeholder="e.g. W1AW"
                  value={callsign}
                  onChange={(event) => setCallsign(event.currentTarget.value)}
                  autoCapitalize="characters"
                  spellCheck={false}
                  autoFocus
                />
                <Select
                  label="Station ID"
                  data={[
                    { value: "voice", label: "Voice" },
                    { value: "cw", label: "CW (Morse)" },
                    { value: "both", label: "Both" },
                  ]}
                  value={idMode}
                  onChange={(value) => setIdMode(value || "voice")}
                />
              </SimpleGrid>
            </Stack>
          ) : null}

          {current?.id === "board" ? (
            <Stack gap="md">
              <Text c="dimmed" size="sm">
                Pick the board or cable between this computer and the radio. It sets the carrier-detect and PTT wiring, and the sound card levels.
              </Text>
              <Select
                label="Interface"
                data={boardSelectData(boards)}
                value={boardId}
                onChange={(value) => setBoardId(value ?? BY_HAND)}
                searchable
              />
              <Text size="sm" c="dimmed">
                {selectedBoard ? selectedBoard.unsupported ?? selectedBoard.notes : "Leave the wiring as it is and set it up on the Audio page."}
              </Text>
              {selectedBoard?.mixer.length ? (
                <Switch
                  label={`Set the sound card levels ${mixerLabel}`}
                  checked={setMixer}
                  onChange={(event) => setSetMixer(event.currentTarget.checked)}
                />
              ) : null}
            </Stack>
          ) : null}

          {current?.id === "devices" ? (
            <Stack gap="md">
              <Text c="dimmed" size="sm">
                Choose the sound card the repeater&apos;s radio is plugged into.
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <Select
                  label="Input (receiver)"
                  data={deviceOptions(devices, "inputs", inputDevice)}
                  value={inputDevice}
                  onChange={(value) => setInputDevice(value ?? "")}
                  searchable
                />
                <Select
                  label="Output (transmitter)"
                  data={deviceOptions(devices, "outputs", outputDevice)}
                  value={outputDevice}
                  onChange={(value) => setOutputDevice(value ?? "")}
                  searchable
                />
              </SimpleGrid>
              {selectedBoard?.two_port ? (
                <Stack gap="sm">
                  <Switch
                    label="Port 2 has a link radio"
                    checked={linkOn}
                    onChange={(event) => setLinkOn(event.currentTarget.checked)}
                  />
                  {linkOn ? (
                    <SimpleGrid cols={{ base: 1, sm: 2 }}>
                      <Select
                        label="Link radio input"
                        data={deviceOptions(devices, "inputs", linkInput)}
                        value={linkInput}
                        onChange={(value) => setLinkInput(value ?? "")}
                        searchable
                      />
                      <Select
                        label="Link radio output"
                        data={deviceOptions(devices, "outputs", linkOutput)}
                        value={linkOutput}
                        onChange={(value) => setLinkOutput(value ?? "")}
                        searchable
                      />
                    </SimpleGrid>
                  ) : null}
                </Stack>
              ) : null}
            </Stack>
          ) : null}

          {current?.id === "test" ? (
            <Stack gap="md">
              <Text c="dimmed" size="sm">
                Send the station ID and check that the radio keys up and the ID is heard on another radio. Key up on the input to check that carrier detect lights up.
              </Text>
              <Group gap="sm">
                <Button onClick={() => void sendTestId()} loading={busy}>
                  Send test ID
                </Button>
                <Badge color={engine?.running ? (engine.error ? "red" : "green") : "gray"} variant="light">
                  {engine?.running ? (engine.error ? "error" : "running") : "off"}
                </Badge>
                <Badge color={engine?.running && engine.cos_open ? "green" : "gray"} variant="light">
                  carrier
                </Badge>
                <Badge color={engine?.running && engine.transmitting ? "red" : "gray"} variant="light">
                  TX
                </Badge>
              </Group>
              {engine?.error ? (
                <Text size="sm" c="dimmed">
                  {engine.error}
                </Text>
              ) : null}
              {mixerLines.length ? (
                <Box component="ul" pl="lg" m={0}>
                  {mixerLines.map((line) => (
                    <Text key={line} component="li" size="sm" c="dimmed">
                      {line}
                    </Text>
                  ))}
                </Box>
              ) : null}
            </Stack>
          ) : null}

          {current?.id === "account" ? (
            <Stack gap="md">
              <Text c="dimmed" size="sm">
                {session?.auth_required
                  ? "Sign-in is on. Add your own admin account so you don't need the installer's generated password, or leave this blank."
                  : "Anyone who can open this page can change the repeater. Create an admin account to turn on sign-in."}
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <TextInput
                  label="Username"
                  value={username}
                  onChange={(event) => setUsername(event.currentTarget.value)}
                  autoComplete="username"
                  spellCheck={false}
                />
                <TextInput
                  label="Password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.currentTarget.value)}
                  autoComplete="new-password"
                  minLength={8}
                />
                <TextInput
                  label="Password again"
                  type="password"
                  value={password2}
                  onChange={(event) => setPassword2(event.currentTarget.value)}
                  autoComplete="new-password"
                  minLength={8}
                />
              </SimpleGrid>
            </Stack>
          ) : null}

          {error ? (
            <Text c="red" role="alert" size="sm">
              {error}
            </Text>
          ) : null}

          <Group justify="space-between" mt="sm">
            <Button variant="default" disabled={stepIndex === 0 || busy} onClick={() => setStepIndex((i) => Math.max(0, i - 1))}>
              Back
            </Button>
            <Button
              onClick={() => void goNext()}
              loading={busy}
              disabled={current?.id === "board" && boardUnsupported}
            >
              {stepIndex >= visibleSteps.length - 1 ? "Finish" : "Next"}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </WizardContext.Provider>
  );
}
