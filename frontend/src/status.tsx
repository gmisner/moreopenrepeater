import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

// Live repeater snapshot from /ws/status. Keep consumers narrow so TX/RX
// updates do not re-render the whole dashboard.

export type RepeaterStatus = {
  state?: string;
  ptt_active?: boolean;
  cos_active?: boolean;
  ctcss_hz?: number | null;
  transmitter_enabled?: boolean;
  locked_out?: boolean;
  callsign?: string;
  [key: string]: unknown;
};

type StatusContextValue = {
  status: RepeaterStatus | null;
  connected: boolean;
};

const STATUS_STALE_MS = 10000;
const RECONNECT_MS = 2000;

const StatusContext = createContext<StatusContextValue>({ status: null, connected: false });

export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<RepeaterStatus | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let stale: number | undefined;

    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${location.host}/ws/status`);
      socket = ws;

      const disconnect = () => {
        if (socket !== ws) return;
        socket = null;
        window.clearTimeout(stale);
        setStatus(null);
        setConnected(false);
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
          setStatus(snapshot);
          expectFrame();
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = disconnect;
      expectFrame();
    };

    connect();
    return () => {
      const ws = socket;
      socket = null;
      window.clearTimeout(retry);
      window.clearTimeout(stale);
      ws?.close();
    };
  }, []);

  const value = useMemo(() => ({ status, connected }), [status, connected]);
  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}

export function useStatus() {
  return useContext(StatusContext);
}
