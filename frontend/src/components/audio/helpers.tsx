import { Box } from "@mantine/core";
import { apiBlob } from "../../api";
import { METER_FLOOR_DB } from "./constants";
import type { Asset, EngineStatus, SoundLevel } from "./types";

let previewAudio: HTMLAudioElement | null = null;

export async function playPreview(clip: string, config: Record<string, unknown> = {}) {
  const blob = await apiBlob("/api/audio/preview", { method: "POST", json: { clip, config, net: false } });
  if (previewAudio) {
    previewAudio.pause();
    URL.revokeObjectURL(previewAudio.src);
  }
  const next = new window.Audio(URL.createObjectURL(blob));
  previewAudio = next;
  await next.play();
}

export function meterPercent(db: number) {
  return Math.max(0, Math.min(100, ((db - METER_FLOOR_DB) / -METER_FLOOR_DB) * 100));
}

/** Shorten long ALSA / CoreAudio device names for summary rows. */
export function shortLabel(value: string, empty = "System default", max = 42) {
  const text = value.trim();
  if (!text) return empty;
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

/** Shorten long ALSA / CoreAudio device names for summary rows. */
export function shortDevice(name: string, empty = "System default") {
  const trimmed = (name || "").trim();
  if (!trimmed) return empty;
  return trimmed.length > 42 ? `${trimmed.slice(0, 40)}…` : trimmed;
}

export function parseCustomTone(text: string): [string, string][] {
  return (text || "")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => {
      const [hz = "", ms = ""] = part.split(":");
      return [hz, ms] as [string, string];
    });
}

export function serializeCustomTone(parts: [string, string][]) {
  return parts
    .filter(([hz, ms]) => hz !== "" && ms !== "")
    .map(([hz, ms]) => `${Number(hz)}:${Number(ms)}`)
    .join(" ");
}

export function assetOptions(assets: Asset[], kind: string) {
  return assets.filter((asset) => asset.kind === kind).map((asset) => ({ value: asset.id, label: asset.filename }));
}

export function soundLevelText(level: SoundLevel) {
  if (level.kind === "switch") return level.value > 0 ? "on" : "off";
  if (level.db == null) return `${Math.round((100 * (level.value - level.min)) / (level.max - level.min || 1))}%`;
  return `${level.db > 0 ? "+" : ""}${level.db.toFixed(1)} dB`;
}

export function engineState(engine: EngineStatus | null) {
  if (!engine) return { label: "…", color: "gray" as const };
  if (engine.running) return { label: "running", color: "green" as const };
  if (engine.error) return { label: "error", color: "red" as const };
  return { label: engine.enabled ? "stopped" : "off", color: "gray" as const };
}

export function LevelMeter({
  db,
  running,
  open,
  thresholdDb,
  showThreshold,
}: {
  db: number;
  running: boolean;
  open: boolean;
  thresholdDb: number;
  showThreshold: boolean;
}) {
  const pct = running ? meterPercent(db) : 0;
  return (
    <Box
      pos="relative"
      h={12}
      style={{
        borderRadius: 6,
        background: "var(--mantine-color-default-hover)",
        overflow: "hidden",
      }}
    >
      <Box
        style={{
          position: "absolute",
          inset: 0,
          transformOrigin: "left center",
          transform: `scaleX(${pct / 100})`,
          background: open ? "var(--mantine-color-green-6)" : "var(--mantine-color-accent-5)",
          transition: "transform 80ms linear",
        }}
      />
      {showThreshold ? (
        <Box
          title="VOX threshold"
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${meterPercent(thresholdDb)}%`,
            width: 2,
            background: "var(--mantine-color-yellow-6)",
          }}
        />
      ) : null}
    </Box>
  );
}