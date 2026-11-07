import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  type EngineStatus,
  type EngineValues,
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

export function Audio() {
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
  const [levels, setLevels] = useState<SoundLevel[]>([]);
  const [levelNotes, setLevelNotes] = useState<string[]>([]);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);
  const [levelsSaving, setLevelsSaving] = useState(false);
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

  return (
    <Page
      title="Audio & tones"
      description="Sounds, radio interface, and linked radio."
    >
      <SoundsCard
        sounds={sounds}
        assets={assets}
        customParts={customParts}
        previewBusy={previewBusy}
        preview={preview}
        setCustomPart={setCustomPart}
      />
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
      />
      <MonitorReceiverCard devices={devices} />
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
      <LinkRadioCard
        link={link}
        devices={devices}
        devicesError={devicesError}
        onReloadDevices={() => void loadDevices()}
        serialPorts={serialPorts}
        soundUsage={soundUsage}
        serialUsage={serialUsage}
        linkRemoteBase={linkRemoteBase}
        linkCosHardware={linkCosHardware}
        linkTxCtcss={linkTxCtcss}
        remoteOffset={remoteOffset}
        remoteTone={remoteTone}
      />
      <AudioHardwareCards assets={assets} reloadAssets={loadAssets} canWrite={!isViewer} />
    </Page>
  );
}
