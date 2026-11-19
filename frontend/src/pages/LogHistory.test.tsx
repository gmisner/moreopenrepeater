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

  describe("whole activity", () => {
    const ID = "0123456789abcdef0123456789abcdef";
    const cycleRoutes = () =>
      routes({
        "/api/log-history/status": STATUS,
        "/api/log-history/events": (path: string) =>
          path.includes("correlation_id=")
            ? {
                events: [
                  event(3, { time: "2026-11-18T10:00:04Z", event: "receiver.opened", message: "receiver opened", correlation_id: ID }),
                  event(2, { time: "2026-11-18T10:00:01Z", event: "controller.state_changed", message: "state IDLE -> RX", correlation_id: ID }),
                ],
                next_before: null,
              }
            : {
                events: [
                  event(3, { event: "receiver.opened", message: "receiver opened", correlation_id: ID }),
                  event(1, { message: "plain line" }),
                ],
                next_before: null,
              },
        "/api/log-history/gaps": { gaps: [] },
      });

    it("offers it only on events that belong to an activity", async () => {
      cycleRoutes();
      view();
      await screen.findByText("plain line");
      expect(screen.getAllByRole("button", { name: "Whole activity" })).toHaveLength(1);
    });

    it("shows that activity in the order it happened, then goes back to the results", async () => {
      cycleRoutes();
      view();
      await userEvent.click(await screen.findByRole("button", { name: "Whole activity" }));
      await screen.findByText("Everything from one activity", { exact: false });
      const asked = new URLSearchParams(requests("/api/log-history/events").at(-1)?.split("?")[1]);
      expect(asked.get("correlation_id")).toBe(ID);
      expect(asked.get("since")).toBeNull(); // an old activity is still found, whatever the search was
      const rows = screen.getAllByRole("listitem");
      expect(rows.map((row) => row.textContent)).toEqual([
        expect.stringContaining("+0.0 s"),
        expect.stringContaining("+3.0 s"),
      ]);
      expect(rows[0].textContent).toContain("state IDLE -> RX"); // the first thing that happened is on top
      expect(rows[1].textContent).toContain("receiver opened");
      expect(screen.getByText(/2 events, 3.0 s/)).toBeTruthy();
      expect(screen.queryByText("plain line")).toBeNull();

      await userEvent.click(screen.getByRole("button", { name: "Back to results" }));
      await screen.findByText("plain line");
      expect(screen.queryByText("Everything from one activity", { exact: false })).toBeNull();
    });

    it("leaves the activity when a new search is made", async () => {
      cycleRoutes();
      view();
      await userEvent.click(await screen.findByRole("button", { name: "Whole activity" }));
      await screen.findByText("Everything from one activity", { exact: false });
      await userEvent.click(screen.getByRole("button", { name: "Search" }));
      await screen.findByText("plain line");
    });
  });
});
