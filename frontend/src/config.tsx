import { notifications } from "@mantine/notifications";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, api } from "./api";

export type Config = Record<string, unknown>;

const CLEARABLE_FIELDS = new Set([
  "require_ctcss_hz",
  "courtesy_tone_asset_id",
  "courtesy_tone_link_asset_id",
  "courtesy_tone_patch_asset_id",
  "net_courtesy_tone_asset_id",
  "id_asset_id",
  "long_id_asset_id",
  "timeout_tone_asset_id",
  "aprs_lat",
  "aprs_lon",
  "wx_lat",
  "wx_lon",
  "tx_ctcss_hz",
  "link_radio_tx_ctcss_hz",
  "remote_base_offset_mhz",
  "remote_base_tone_hz",
  "aprs_frequency_mhz",
  "aprs_offset_mhz",
  "aprs_tone_hz",
  "fan_temp_c",
]);

type ConfigContextValue = {
  config: Config | null;
  loading: boolean;
  refresh: () => Promise<Config>;
  save: (patch: Config) => Promise<Config>;
};

// Exported so tests can inject a provider without mounting the live /api/config fetch.
export const ConfigContext = createContext<ConfigContextValue | null>(null);

export function notifyError(error: unknown) {
  notifications.show({
    color: "red",
    message: error instanceof ApiError || error instanceof Error ? error.message : String(error),
  });
}

export function notifySuccess(message: string) {
  notifications.show({ color: "green", message });
}

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const next = await api<Config>("/api/config");
    setConfig(next);
    return next;
  }, []);

  const save = useCallback(async (patch: Config) => {
    const next = await api<Config>("/api/config", { method: "PUT", json: patch });
    setConfig(next);
    return next;
  }, []);

  useEffect(() => {
    refresh()
      .catch(notifyError)
      .finally(() => setLoading(false));
  }, [refresh]);

  const value = useMemo(() => ({ config, loading, refresh, save }), [config, loading, refresh, save]);
  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useConfig() {
  const ctx = useContext(ConfigContext);
  if (!ctx) throw new Error("useConfig requires ConfigProvider");
  return ctx;
}

/** Build a PATCH body from edited values; empty clearable fields become clear_<name>. */
export function buildConfigPatch(values: Config, edited: Iterable<string>): Config {
  const body: Config = {};
  for (const name of edited) {
    if (!(name in values)) continue;
    const value = values[name];
    if (typeof value === "boolean") {
      body[name] = value;
      continue;
    }
    if (value === "" || value === null || value === undefined) {
      if (CLEARABLE_FIELDS.has(name)) body[`clear_${name}`] = true;
      else if (typeof value !== "number") body[name] = "";
      continue;
    }
    body[name] = value;
  }
  return body;
}

export function useConfigSlice<T extends Config>(keys: (keyof T & string)[], defaults: T) {
  const { config, save } = useConfig();
  type Key = keyof T & string;
  const [draft, setDraft] = useState<Map<Key, { value: T[Key] }>>(() => new Map());
  const [saving, setSaving] = useState(false);

  const baseline = { ...defaults };
  if (config) {
    for (const key of keys) {
      if (key in config) baseline[key] = config[key] as T[Key];
    }
  }

  const values = { ...baseline };
  for (const [key, edit] of draft) values[key] = edit.value;

  const setField = <K extends Key>(name: K, value: T[K]) => {
    setDraft((previous) => new Map(previous).set(name, { value }));
  };

  const reset = () => setDraft(new Map());

  const submit = async () => {
    setSaving(true);
    try {
      await save(buildConfigPatch(values, draft.keys()));
      // Only clear the exact edits sent in this request. Newer edits stay in the draft.
      setDraft((current) => {
        const remaining = new Map(current);
        for (const [key, submitted] of draft) {
          if (remaining.get(key) === submitted) remaining.delete(key);
        }
        return remaining;
      });
      notifySuccess("Saved");
      return true;
    } catch (error) {
      notifyError(error);
      return false;
    } finally {
      setSaving(false);
    }
  };

  return { values, setField, dirty: draft.size > 0, reset, submit, saving, baseline };
}
