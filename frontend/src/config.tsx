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

const ConfigContext = createContext<ConfigContextValue | null>(null);

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
  const [values, setValues] = useState<T>(defaults);
  const [baseline, setBaseline] = useState<T>(defaults);
  const [edited, setEdited] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!config) return;
    const next = { ...defaults };
    for (const key of keys) {
      if (key in config) (next as Config)[key] = config[key] as T[typeof key];
    }
    setValues(next);
    setBaseline(next);
    setEdited(new Set());
  }, [config]); // eslint-disable-line react-hooks/exhaustive-deps -- keys/defaults are stable per form

  const setField = <K extends keyof T & string>(name: K, value: T[K]) => {
    setValues((prev) => ({ ...prev, [name]: value }));
    setEdited((prev) => new Set(prev).add(name));
  };

  const dirty = edited.size > 0;

  const reset = () => {
    setValues(baseline);
    setEdited(new Set());
  };

  const submit = async () => {
    setSaving(true);
    try {
      const next = await save(buildConfigPatch(values as Config, edited));
      const filled = { ...defaults };
      for (const key of keys) {
        if (key in next) (filled as Config)[key] = next[key] as T[typeof key];
      }
      setValues(filled);
      setBaseline(filled);
      setEdited(new Set());
      notifySuccess("Saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setSaving(false);
    }
  };

  return { values, setField, dirty, reset, submit, saving, baseline };
}
