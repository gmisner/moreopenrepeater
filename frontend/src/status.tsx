import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

// Live repeater snapshot from /ws/status. Keep consumers narrow so TX/RX
// updates do not re-render the whole dashboard.

export type RepeaterStatus = {
  state?: string;
  ptt_active?: boolean;
  cos_active?: boolean;
  ctcss_hz?: number | null;
  transmitter_enabled?: boolean;
  locked_out?: boolean;
  [key: string]: unknown;
};

type StatusContextValue = {
  status: RepeaterStatus | null;
  connected: boolean;
};

const StatusContext = createContext<StatusContextValue>({ status: null, connected: false });

export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<RepeaterStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let retry: number | undefined;

    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${location.host}/ws/status`);
      socketRef.current = ws;
      ws.onopen = () => {
        if (!cancelled) setConnected(true);
      };
      ws.onmessage = (event) => {
        try {
          setStatus(JSON.parse(event.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (cancelled) return;
        setConnected(false);
        retry = window.setTimeout(connect, 2000);
      };
    };

    connect();
    return () => {
      cancelled = true;
      window.clearTimeout(retry);
      socketRef.current?.close();
    };
  }, []);

  const value = useMemo(() => ({ status, connected }), [status, connected]);
  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}

export function useStatus() {
  return useContext(StatusContext);
}
