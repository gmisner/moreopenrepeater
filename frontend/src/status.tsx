import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

// Live repeater snapshot from /ws/status. Split into narrow contexts so TX/RX
// updates do not re-render the shell or pages that only need connection /
// callsign / gpio extras (ADR 0001).

export type RepeaterStatus = {
  state?: string;
  ptt_active?: boolean;
  cos_active?: boolean;
  ctcss_hz?: number | null;
  transmitter_enabled?: boolean;
  locked_out?: boolean;
  callsign?: string;
  gpio?: unknown;
  link_radio?: unknown;
  [key: string]: unknown;
};

/** On-air / controller fields that change while someone is talking. */
export type LiveStatus = {
  state?: string;
  ptt_active?: boolean;
  cos_active?: boolean;
  ctcss_hz?: number | null;
  transmitter_enabled?: boolean;
  locked_out?: boolean;
  net_active?: boolean;
  tot_remaining?: number | null;
  hang_remaining?: number | null;
  id_due_in?: number | null;
  callsign?: string;
};

export type StatusExtras = {
  gpio: unknown;
  link_radio: unknown;
};

type StatusContextValue = {
  status: RepeaterStatus | null;
  connected: boolean;
};

const STATUS_STALE_MS = 10000;
const RECONNECT_MS = 2000;

const ConnectedContext = createContext(false);
const IdentityContext = createContext<string | undefined>(undefined);
const LiveContext = createContext<LiveStatus | null>(null);
const ExtrasContext = createContext<StatusExtras>({ gpio: null, link_radio: null });
const FullStatusContext = createContext<StatusContextValue>({ status: null, connected: false });

function liveFrom(snapshot: RepeaterStatus): LiveStatus {
  return {
    state: snapshot.state,
    ptt_active: snapshot.ptt_active,
    cos_active: snapshot.cos_active,
    ctcss_hz: snapshot.ctcss_hz ?? null,
    transmitter_enabled: snapshot.transmitter_enabled,
    locked_out: snapshot.locked_out,
    net_active: Boolean(snapshot.net_active),
    tot_remaining: typeof snapshot.tot_remaining === "number" ? snapshot.tot_remaining : null,
    hang_remaining: typeof snapshot.hang_remaining === "number" ? snapshot.hang_remaining : null,
    id_due_in: typeof snapshot.id_due_in === "number" ? snapshot.id_due_in : null,
    callsign: typeof snapshot.callsign === "string" ? snapshot.callsign : undefined,
  };
}

function liveKey(live: LiveStatus | null): string {
  if (!live) return "";
  const q = (n: number | null | undefined) => (n == null ? "" : (Math.round(n * 4) / 4).toFixed(2));
  return [
    live.state ?? "",
    live.ptt_active ? 1 : 0,
    live.cos_active ? 1 : 0,
    live.ctcss_hz ?? "",
    live.transmitter_enabled === false ? 0 : 1,
    live.locked_out ? 1 : 0,
    live.net_active ? 1 : 0,
    q(live.tot_remaining),
    q(live.hang_remaining),
    q(live.id_due_in),
    live.callsign ?? "",
  ].join("|");
}

function gpioKey(gpio: unknown): string {
  if (!gpio || typeof gpio !== "object") return "";
  const g = gpio as {
    available?: boolean;
    error?: string | null;
    pins?: { pin: number; mode: string; on: boolean }[];
    fan?: { output?: string; on?: boolean; error?: string | null } | null;
  };
  const pins = (g.pins ?? []).map((pin) => `${pin.pin}:${pin.mode}:${pin.on ? 1 : 0}`).join(",");
  const fan = g.fan ? `${g.fan.output}:${g.fan.on ? 1 : 0}:${g.fan.error ?? ""}` : "";
  return `${g.available}:${g.error ?? ""}:${pins}:${fan}`;
}

function linkRadioKey(link: unknown): string {
  if (!link || typeof link !== "object") return "";
  const l = link as {
    enabled?: boolean;
    running?: boolean;
    receiving?: boolean;
    transmitting?: boolean;
    timed_out?: boolean;
    error?: string | null;
    rig_error?: string | null;
  };
  return [
    l.enabled ? 1 : 0,
    l.running ? 1 : 0,
    l.receiving ? 1 : 0,
    l.transmitting ? 1 : 0,
    l.timed_out ? 1 : 0,
    l.error ?? "",
    l.rig_error ?? "",
  ].join(":");
}

function extrasKey(extras: StatusExtras): string {
  return `${gpioKey(extras.gpio)}#${linkRadioKey(extras.link_radio)}`;
}

export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<RepeaterStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [callsign, setCallsign] = useState<string | undefined>(undefined);
  const [live, setLive] = useState<LiveStatus | null>(null);
  const [extras, setExtras] = useState<StatusExtras>({ gpio: null, link_radio: null });
  const liveKeyRef = useRef("");
  const extrasKeyRef = useRef(extrasKey({ gpio: null, link_radio: null }));
  // Latest frame while the tab is hidden — applied on the next visible tick so
  // Chromium on the Pi does not re-render TX/RX for a background tab.
  const pendingRef = useRef<RepeaterStatus | null>(null);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let stale: number | undefined;

    const clearSlices = () => {
      pendingRef.current = null;
      setStatus(null);
      setConnected(false);
      setCallsign(undefined);
      setLive(null);
      setExtras({ gpio: null, link_radio: null });
      liveKeyRef.current = "";
      extrasKeyRef.current = extrasKey({ gpio: null, link_radio: null });
    };

    const applySnapshot = (next: RepeaterStatus) => {
      setStatus(next);
      setCallsign(typeof next.callsign === "string" ? next.callsign : undefined);
      const nextLive = liveFrom(next);
      const nextLiveKey = liveKey(nextLive);
      if (nextLiveKey !== liveKeyRef.current) {
        liveKeyRef.current = nextLiveKey;
        setLive(nextLive);
      }
      const nextExtras: StatusExtras = {
        gpio: next.gpio ?? null,
        link_radio: next.link_radio ?? null,
      };
      const nextExtrasKey = extrasKey(nextExtras);
      if (nextExtrasKey !== extrasKeyRef.current) {
        extrasKeyRef.current = nextExtrasKey;
        setExtras(nextExtras);
      }
    };

    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${location.host}/ws/status`);
      socket = ws;

      const disconnect = () => {
        if (socket !== ws) return;
        socket = null;
        window.clearTimeout(stale);
        clearSlices();
        retry = window.setTimeout(connect, RECONNECT_MS);
      };
      const expectFrame = () => {
        window.clearTimeout(stale);
        stale = window.setTimeout(() => {
          disconnect();
          ws.close();
        }, STATUS_STALE_MS);
      };

      ws.onopen = () => {
        if (socket !== ws) return;
        setConnected(true);
        expectFrame();
      };
      ws.onmessage = (event) => {
        if (socket !== ws) return;
        try {
          const snapshot = JSON.parse(event.data);
          if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) return;
          const next = snapshot as RepeaterStatus;
          // Keep the socket + stale timer alive; skip React updates while hidden.
          expectFrame();
          if (document.hidden) {
            pendingRef.current = next;
            return;
          }
          pendingRef.current = null;
          applySnapshot(next);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = disconnect;
      expectFrame();
    };

    const onVisibility = () => {
      if (document.hidden) return;
      const pending = pendingRef.current;
      if (!pending) return;
      pendingRef.current = null;
      applySnapshot(pending);
    };
    document.addEventListener("visibilitychange", onVisibility);

    connect();
    return () => {
      const ws = socket;
      socket = null;
      window.clearTimeout(retry);
      window.clearTimeout(stale);
      document.removeEventListener("visibilitychange", onVisibility);
      ws?.close();
    };
  }, []);

  const fullValue = useMemo(() => ({ status, connected }), [status, connected]);

  return (
    <ConnectedContext.Provider value={connected}>
      <IdentityContext.Provider value={callsign}>
        <LiveContext.Provider value={live}>
          <ExtrasContext.Provider value={extras}>
            <FullStatusContext.Provider value={fullValue}>{children}</FullStatusContext.Provider>
          </ExtrasContext.Provider>
        </LiveContext.Provider>
      </IdentityContext.Provider>
    </ConnectedContext.Provider>
  );
}

/** Full snapshot + connected — prefer a narrow hook when possible. */
export function useStatus() {
  return useContext(FullStatusContext);
}

export function useStatusConnected() {
  return useContext(ConnectedContext);
}

export function useStatusCallsign() {
  return useContext(IdentityContext);
}

export function useStatusLive() {
  return useContext(LiveContext);
}

export function useStatusExtras() {
  return useContext(ExtrasContext);
}
