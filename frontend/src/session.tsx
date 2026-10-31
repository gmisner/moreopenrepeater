import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";

export type Role = "admin" | "operator" | "viewer" | "listener" | string;

export type Session = {
  auth_required: boolean;
  authenticated: boolean;
  username?: string;
  role?: Role;
};

type SessionContextValue = {
  session: Session | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

const ROLE_LABELS: Record<string, string> = {
  admin: "admin",
  operator: "operator",
  viewer: "read-only",
  listener: "listener",
};

export function roleLabel(role?: string) {
  return (role && ROLE_LABELS[role]) || role || "";
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    const next = await api<Session>("/api/session");
    if (next.auth_required && !next.authenticated) {
      location.replace("/login");
      return;
    }
    if (next.role === "listener") {
      location.replace("/listen");
      return;
    }
    setSession(next);
  };

  const logout = async () => {
    await api("/api/logout", { method: "POST" });
    location.replace("/login");
  };

  useEffect(() => {
    refresh()
      .catch(() => location.replace("/login"))
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo(() => ({ session, loading, refresh, logout }), [session, loading]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession requires SessionProvider");
  return ctx;
}
