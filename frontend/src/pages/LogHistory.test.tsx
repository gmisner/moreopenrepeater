import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));
vi.mock("../api", () => ({
  api: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

import { api } from "../api";
import { LogHistory } from "./LogHistory";

const STATUS = {
  configured: true,
  available: true,
  stats: {
    events: 1234,
    errors: 3,
    oldest: "2026-11-10T10:00:00Z",
    newest: "2026-11-18T10:00:00Z",
    modules: { core: {}, api: {} },
    retention: { routine_days: 14, critical_days: 30 },
  },
};

const event = (n: number, extra: object = {}) => ({
  time: `2026-11-18T10:0${n}:00Z`,
  severity: 20,
  event: "test.line",
  module: "core",
  message: `line ${n}`,
  context: {},
  ...extra,
});

function routes(table: Record<string, unknown>) {
  vi.mocked(api).mockImplementation(async (path: string) => {
    const key = Object.keys(table).find((k) => path.startsWith(k));
    if (!key) throw new Error(`unexpected request ${path}`);
    const value = table[key];
    return (typeof value === "function" ? (value as (p: string) => unknown)(path) : value) as never;
  });
}

const view = () =>
  render(
    <MantineProvider>
      <LogHistory />
    </MantineProvider>,
  );

const requests = (prefix: string) => vi.mocked(api).mock.calls.map(([p]) => String(p)).filter((p) => p.startsWith(prefix));

describe("LogHistory", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("says why when there is no collector, and does not ask for events", async () => {
    routes({ "/api/log-history/status": { configured: false, available: false, reason: "the log collector is not set up on this install" } });
    view();
    expect(await screen.findByText(/event history is not available: the log collector is not set up/)).toBeTruthy();
    expect(screen.getByText(/live log and the log file still work/)).toBeTruthy();
    expect(requests("/api/log-history/events")).toHaveLength(0);
  });

  it("shows the newest events, what is kept, and the gaps it knows of", async () => {
    routes({
      "/api/log-history/status": STATUS,
      "/api/log-history/events": { events: [event(2, { severity: 40, event: "audio.glitch", message: "glitch", context: { card: "aioc", count: 1 } }), event(1)], next_before: null },
      "/api/log-history/gaps": { gaps: [{ time: "2026-11-18T09:00:00Z", kind: "sequence_gap", module: "core", count: 4, detail: null }] },
    });
    view();
    expect(await screen.findByText("glitch")).toBeTruthy();
    expect(screen.getByText("ERROR")).toBeTruthy();
    expect(screen.getByText("audio.glitch")).toBeTruthy();
    expect(screen.getByText("card=aioc count=1")).toBeTruthy();
    expect(screen.getByText(/Keeping 1,234 events/)).toBeTruthy();
    expect(screen.getByText(/14 days of routine events, 30 days of warnings and errors/)).toBeTruthy();
    expect(screen.getByText("Some events are known to be missing")).toBeTruthy();
    expect(screen.getByText(/core events lost on the way \(4\)/)).toBeTruthy();
    expect(screen.queryByText("Show older")).toBeNull(); // nothing more to page
  });

  it("searches only when asked, with what was typed", async () => {
    routes({
      "/api/log-history/status": STATUS,
      "/api/log-history/events": { events: [event(1)], next_before: null },
      "/api/log-history/gaps": { gaps: [] },
    });
    view();
    await screen.findByText("line 1");
    expect(requests("/api/log-history/events")).toHaveLength(1); // the opening search, and no timer after it
    await userEvent.type(screen.getByLabelText("Search"), "audio glitch");
    expect(requests("/api/log-history/events")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(requests("/api/log-history/events")).toHaveLength(2));
    const params = new URLSearchParams(requests("/api/log-history/events")[1].split("?")[1]);
    expect(params.get("q")).toBe("audio glitch");
    expect(params.get("limit")).toBe("100");
    expect(Number(params.get("since"))).toBeGreaterThan(Date.now() / 1000 - 90000); // the default is the last 24 hours
  });

  it("pages back with show older, keeping what is already shown", async () => {
    routes({
      "/api/log-history/status": STATUS,
      "/api/log-history/events": (path: string) =>
        path.includes("before=7") ? { events: [event(1)], next_before: null } : { events: [event(3), event(2)], next_before: 7 },
      "/api/log-history/gaps": { gaps: [] },
    });
    view();
    await screen.findByText("line 3");
    await userEvent.click(screen.getByRole("button", { name: "Show older" }));
    await screen.findByText("line 1");
    expect(screen.getAllByRole("listitem").map((li) => li.textContent?.match(/line \d/)?.[0])).toEqual(["line 3", "line 2", "line 1"]);
    expect(screen.queryByText("Show older")).toBeNull();
  });

  it("says so when nothing matches", async () => {
    routes({
      "/api/log-history/status": STATUS,
      "/api/log-history/events": { events: [], next_before: null },
      "/api/log-history/gaps": { gaps: [] },
    });
    view();
    expect(await screen.findByText("No events match.")).toBeTruthy();
  });
});
