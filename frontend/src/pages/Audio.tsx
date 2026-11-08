import { Anchor, Badge, Card, Group, Stack, Table, Tabs, Text } from "@mantine/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import {
  ENGINE_DEFAULTS,
  ENGINE_KEYS,
  GLITCH_SHORT,
  LINK_DEFAULTS,
  LINK_KEYS,
  LinkRadioCard,
  POLL_MS,
  PTT_LABELS,
  RadioInterfaceCard,
  SILENCE_DB,
  SILENT_POLLS_BEFORE_HINT,
  SOUND_DEFAULTS,
  SOUND_KEYS,
  SoundLevelsCard,
  SoundsCard,
  engineState,
  parseCustomTone,
  playPreview,
  serializeCustomTone,
  type Asset,
  type AudioDevice,
  type AudioScore,
  type EngineStatus,
  type EngineValues,
  type LinkRadioLiveStatus,
  type LinkValues,
  type SerialPort,
  type SoundLevel,
  type SoundValues,
} from "../components/audio";
import { AudioHardwareCards, MonitorReceiverCard } from "../components/AudioHardware";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import {
  serialAssignments,
  soundAssignments,
} from "../deviceUsage";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { useSetupWizard } from "../wizard/SetupWizard";
import { labeled } from "../components/FieldHint";

const AUDIO_TABS = ["sounds", "radio", "linked", "pins"];

export function Audio() {
  const [tab, setTab] = useState<string>(() => {
    const fromHash = window.location.hash.slice(1);
    return AUDIO_TABS.includes(fromHash) ? fromHash : "sounds";
  });
  const changeTab = (value: string | null) => {
    if (!value) return;
    setTab(value);
    window.history.replaceState(null, "", `#${value}`);
  };
  const { session } = useSession();
  const { config } = useConfig();
  const isViewer = session?.role === "viewer";
  const sounds = useConfigSlice<SoundValues>(SOUND_KEYS, SOUND_DEFAULTS);
  const engine = useConfigSlice<EngineValues>(ENGINE_KEYS, ENGINE_DEFAULTS);
  const link = useConfigSlice<LinkValues>(LINK_KEYS, LINK_DEFAULTS);

  const [assets, setAssets] = useState<Asset[]>([]);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [devicesError, setDevicesError] = useState<string | null>(null);
  const [serialPorts, setSerialPorts] = useState<SerialPort[]>([]);
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [linkStatus, setLinkStatus] = useState<LinkRadioLiveStatus | null>(null);
  const [levels, setLevels] = useState<SoundLevel[]>([]);
  const [levelNotes, setLevelNotes] = useState<string[]>([]);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);
  const [levelsSaving, setLevelsSaving] = useState(false);
  const [testIdBusy, setTestIdBusy] = useState(false);
  const [scoreBusy, setScoreBusy] = useState(false);
  const [score, setScore] = useState<AudioScore | null>(null);
  const [openings, setOpenings] = useState<
    {
      started_at: number;
      duration: number;
      open_db: number;
      peak_db: number;
      ctcss_hz: number | null;
      dtmf_digits: string;
    }[]
  >([]);
  const silentPolls = useRef(0);
  const [showSilenceHint, setShowSilenceHint] = useState(false);

  const customParts = useMemo(() => {
    const parsed = parseCustomTone(sounds.values.courtesy_tone_custom);
    return Array.from({ length: 4 }, (_, i) => parsed[i] ?? (["", ""] as [string, string]));
  }, [sounds.values.courtesy_tone_custom]);

  const soundUsage = useMemo(
    () =>
      soundAssignments({
        audio_input_device: engine.values.audio_input_device,
        audio_output_device: engine.values.audio_output_device,
        link_radio_input_device: link.values.link_radio_input_device,
        link_radio_output_device: link.values.link_radio_output_device,
        monitor_input_device: typeof config?.monitor_input_device === "string" ? config.monitor_input_device : "",
      }),
    [
      engine.values.audio_input_device,
      engine.values.audio_output_device,
      link.values.link_radio_input_device,
      link.values.link_radio_output_device,
      config?.monitor_input_device,
    ],
  );

  const serialUsage = useMemo(
    () =>
      serialAssignments({
        cos_serial_device: engine.values.cos_serial_device,
        ptt_serial_device: engine.values.ptt_serial_device,
        link_radio_cos_serial_device: link.values.link_radio_cos_serial_device,
        link_radio_ptt_serial_device: link.values.link_radio_ptt_serial_device,
      }),
    [
      engine.values.cos_serial_device,
      engine.values.ptt_serial_device,
      link.values.link_radio_cos_serial_device,
      link.values.link_radio_ptt_serial_device,
    ],
  );

  const loadAssets = useCallback(async () => {
    setAssets(await api<Asset[]>("/api/assets"));
  }, []);

  const loadDevices = useCallback(async () => {
    try {
      setDevices(await api<AudioDevice[]>("/api/audio/devices"));
      setDevicesError(null);
    } catch (error) {
      setDevices([]);
      setDevicesError(error instanceof Error ? error.message : String(error));
    }
    try {
      setSerialPorts(await api<SerialPort[]>("/api/serial/ports"));
    } catch {
      setSerialPorts([]);
    }
  }, []);

  const loadLevels = useCallback(async () => {
    try {
      const body = await api<{ levels: SoundLevel[]; notes: string[] }>("/api/audio/levels");
      setLevels(body.levels);
      setLevelNotes(body.notes);
    } catch (error) {
      setLevels([]);
      setLevelNotes([error instanceof Error ? error.message : String(error)]);
    }
  }, []);

  useEffect(() => {
    loadAssets().catch(notifyError);
    loadDevices();
    loadLevels();
  }, [loadAssets, loadDevices, loadLevels]);

  useEffect(() => {
    void loadLevels();
  }, [
    config?.audio_input_device,
    config?.audio_output_device,
    config?.link_radio_input_device,
    config?.link_radio_output_device,
    loadLevels,
  ]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api<EngineStatus>("/api/audio/engine");
        if (cancelled) return;
        setEngineStatus(next);
        silentPolls.current = next.running && next.rx_level_db <= SILENCE_DB ? silentPolls.current + 1 : 0;
        setShowSilenceHint(silentPolls.current >= SILENT_POLLS_BEFORE_HINT);
      } catch {
        // Next poll retries; session handling covers sign-out.
      }
      try {
        const linkNext = await api<LinkRadioLiveStatus>("/api/link-radio");
        if (!cancelled) setLinkStatus(linkNext);
      } catch {
        // Linked radio status is optional for this page; retry next poll.
      }
      try {
        const nextOpenings = await api<typeof openings>("/api/audio/openings");
        if (!cancelled) setOpenings(nextOpenings.slice(0, 8));
      } catch {
        // Openings are diagnostics-only.
      }
    };
    void tick();
    const id = window.setInterval(() => {
      if (!document.hidden) void tick();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const preview = async (clip: string, label: string, override: Record<string, unknown> = {}) => {
    setPreviewBusy(label);
    try {
      await playPreview(clip, { ...sounds.values, ...override });
    } catch (error) {
      notifyError(error);
    } finally {
      setPreviewBusy(null);
    }
  };

  const setCustomPart = (index: number, which: 0 | 1, value: string | number) => {
    const next = customParts.map((part) => [...part] as [string, string]);
    next[index][which] = value === "" || value === undefined ? "" : String(value);
    sounds.setField("courtesy_tone_custom", serializeCustomTone(next));
  };

  const setLevel = async (level: SoundLevel, value: number) => {
    try {
      const body = await api<{ levels: SoundLevel[]; notes: string[] }>("/api/audio/levels", {
        method: "PUT",
        json: { side: level.side, control: level.control, value },
      });
      setLevels(body.levels);
      setLevelNotes(body.notes);
    } catch (error) {
      notifyError(error);
      void loadLevels();
    }
  };

  const saveUnsavedLevels = async () => {
    setLevelsSaving(true);
    try {
      for (const level of levels.filter((item) => !item.saved)) {
        await api("/api/audio/levels", {
          method: "PUT",
          json: { side: level.side, control: level.control, value: level.value },
        });
      }
      notifySuccess("Levels saved");
      await loadLevels();
    } catch (error) {
      notifyError(error);
    } finally {
      setLevelsSaving(false);
    }
  };

  const { openWizard } = useSetupWizard();
  const state = engineState(engineStatus);
  const boardPreset = (config?.board_preset as string) || "";
  const cosHardware = ["cm108", "gpio", "serial"].includes(engine.values.cos_source);
  const linkCosHardware = ["cm108", "gpio", "serial"].includes(link.values.link_radio_cos);
  const linkRemoteBase = link.values.link_radio_mode === "remote_base";
  const linkTxCtcss =
    link.values.link_radio_tx_ctcss_hz === "" || link.values.link_radio_tx_ctcss_hz == null
      ? null
      : String(link.values.link_radio_tx_ctcss_hz);
  const remoteTone =
    link.values.remote_base_tone_hz === "" || link.values.remote_base_tone_hz == null
      ? null
      : String(link.values.remote_base_tone_hz);
  const remoteOffset =
    link.values.remote_base_offset_mhz === "" || link.values.remote_base_offset_mhz == null
      ? ""
      : link.values.remote_base_offset_mhz;
  const glitchCount = (counter: keyof typeof GLITCH_SHORT) => {
    if (!engineStatus) return 0;
    if (counter === "dropped_input_blocks") return engineStatus.dropped_input_blocks;
    if (counter === "starved_output_blocks") return engineStatus.starved_output_blocks;
    if (counter === "input_overflows") return engineStatus.input_overflows;
    return engineStatus.output_underflows;
  };
  const glitches = Object.entries(GLITCH_SHORT)
    .filter(([counter]) => glitchCount(counter as keyof typeof GLITCH_SHORT))
    .map(([counter, label]) => `${glitchCount(counter as keyof typeof GLITCH_SHORT)} ${label}`)
    .join(" · ");
  const rate = engineStatus?.device_sample_rate ?? engineStatus?.sample_rate;
  const rateText =
    engineStatus && rate != null
      ? rate === engineStatus.sample_rate
        ? `${rate / 1000} kHz`
        : `device ${rate / 1000} kHz → ${engineStatus.sample_rate / 1000} kHz`
      : "";
  const health = engineStatus?.running
    ? `${rateText}${PTT_LABELS[engineStatus.hardware_ptt ?? ""] ?? ""}${glitches ? ` · ${glitches}` : ""}`
    : "";
  const unsavedLevels = levels.some((level) => !level.saved);

  const sendTestId = async () => {
    setTestIdBusy(true);
    try {
      await api("/api/audio/test-id", { method: "POST" });
      notifySuccess("Playing the station ID");
    } catch (error) {
      notifyError(error);
    } finally {
      setTestIdBusy(false);
    }
  };

  const recordAndScore = async () => {
    setScoreBusy(true);
    try {
      const body = await api<AudioScore>("/api/audio/record-score?seconds=5", {
        method: "POST",
      });
      setScore(body);
      notifySuccess("Scored a 5 s RX capture");
    } catch (error) {
      notifyError(error);
    } finally {
      setScoreBusy(false);
    }
  };

  const scoreUploadedWav = async (file: File | null) => {
    if (!file) return;
    setScoreBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const body = await api<AudioScore>("/api/audio/score", { method: "POST", body: form });
      setScore(body);
      notifySuccess(`Scored ${file.name}`);
    } catch (error) {
      notifyError(error);
    } finally {
      setScoreBusy(false);
    }
  };

  return (
    <Page
      title="Audio & tones"
    >
      <Text size="sm" c="dimmed">
        Looking for a starting recipe? See{" "}
        <Anchor component={Link} to="/configs">
          Known configs
        </Anchor>{" "}
        for Digirig, CM108, AIOC, and SVXLink board settings.
      </Text>
      <Tabs value={tab} onChange={changeTab} keepMounted variant="default">
        <Tabs.List style={{ overflowX: "auto", flexWrap: "nowrap" }}>
          <Tabs.Tab value="sounds">Sounds</Tabs.Tab>
          <Tabs.Tab value="radio">Radio interface</Tabs.Tab>
          <Tabs.Tab value="linked">Linked radio</Tabs.Tab>
          <Tabs.Tab value="pins">Pins and outputs</Tabs.Tab>
        </Tabs.List>
        <AudioHardwareCards assets={assets} reloadAssets={loadAssets} canWrite={!isViewer}>
          {({ pins, library }) => (
            <>
              <Tabs.Panel value="sounds" pt="md">
                <Stack gap="md">
              <SoundsCard
                sounds={sounds}
                assets={assets}
                customParts={customParts}
                previewBusy={previewBusy}
                preview={preview}
                setCustomPart={setCustomPart}
              />
                  {library}
                </Stack>
              </Tabs.Panel>
              <Tabs.Panel value="radio" pt="md">
                <Stack gap="md">
              <RadioInterfaceCard
                engine={engine}
                devices={devices}
                devicesError={devicesError}
                onReloadDevices={() => void loadDevices()}
                serialPorts={serialPorts}
                soundUsage={soundUsage}
                serialUsage={serialUsage}
                engineStatus={engineStatus}
                state={state}
                boardPreset={boardPreset}
                nodeMode={(config?.node_mode as string) || "repeater"}
                cosHardware={cosHardware}
                health={health}
                showSilenceHint={showSilenceHint}
                isViewer={isViewer}
                openWizard={openWizard}
                onTestId={isViewer ? undefined : () => void sendTestId()}
                testIdBusy={testIdBusy}
                score={score}
                scoreBusy={scoreBusy}
                onRecordScore={isViewer ? undefined : () => void recordAndScore()}
                onScoreWav={isViewer ? undefined : (file) => void scoreUploadedWav(file)}
              />
              <Card withBorder padding="lg" radius="md">
                <Stack gap="sm">
                  <Group justify="space-between" align="baseline">
                    <Text component="h2" className="mor-card-title">
                      {labeled("Receiver openings", <>Live score of what opened COS — level, peak, CTCSS, DTMF</>)}</Text>
                  </Group>
                  {openings.length ? (
                    <Table striped highlightOnHover withTableBorder={false}>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>When</Table.Th>
                          <Table.Th>Length</Table.Th>
                          <Table.Th>Open</Table.Th>
                          <Table.Th>Peak</Table.Th>
                          <Table.Th>CTCSS</Table.Th>
                          <Table.Th>DTMF</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {openings.map((row) => (
                          <Table.Tr key={row.started_at}>
                            <Table.Td>{new Date(row.started_at * 1000).toLocaleTimeString()}</Table.Td>
                            <Table.Td>{row.duration.toFixed(1)} s</Table.Td>
                            <Table.Td>{row.open_db.toFixed(0)} dB</Table.Td>
                            <Table.Td>{row.peak_db.toFixed(0)} dB</Table.Td>
                            <Table.Td>
                              {row.ctcss_hz != null ? <Badge variant="light">{row.ctcss_hz} Hz</Badge> : "—"}
                            </Table.Td>
                            <Table.Td ff="monospace">{row.dtmf_digits || "—"}</Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  ) : (
                    <Text size="sm" c="dimmed">
                      No openings yet. Key a radio into the repeater (or use the Simulator) and watch open/peak levels to
                      set VOX and Mic gain. Aim for idle a few dB below the VOX threshold, and keyed peaks well under 0
                      dBFS.
                    </Text>
                  )}
                </Stack>
              </Card>
              <SoundLevelsCard
                levels={levels}
                levelNotes={levelNotes}
                unsavedLevels={unsavedLevels}
                levelsSaving={levelsSaving}
                isViewer={isViewer}
                setLevels={setLevels}
                setLevel={setLevel}
                saveUnsavedLevels={saveUnsavedLevels}
              />
                </Stack>
              </Tabs.Panel>
              <Tabs.Panel value="linked" pt="md">
                <Stack gap="md">
              <LinkRadioCard
                link={link}
                devices={devices}
                devicesError={devicesError}
                onReloadDevices={() => void loadDevices()}
                serialPorts={serialPorts}
                soundUsage={soundUsage}
                serialUsage={serialUsage}
                linkStatus={linkStatus}
                linkRemoteBase={linkRemoteBase}
                linkCosHardware={linkCosHardware}
                linkTxCtcss={linkTxCtcss}
                remoteOffset={remoteOffset}
                remoteTone={remoteTone}
              />
              <MonitorReceiverCard devices={devices} />
                </Stack>
              </Tabs.Panel>
              <Tabs.Panel value="pins" pt="md">
                <Stack gap="md">{pins}</Stack>
              </Tabs.Panel>
            </>
          )}
        </AudioHardwareCards>
      </Tabs>
    </Page>
  );
}
