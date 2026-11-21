import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NAV } from "../nav";
import { theme } from "../theme";
import { Winlink } from "./Winlink";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";

const activity = {
  gateway: {
    counters: {
      sessions_today: 2,
      sessions_today_is_minimum: false,
      sessions_total: 5,
      incomplete: 1,
      messages_in: 3,
      messages_out: 4,
      bytes_in: 1536,
      bytes_out: 2_500_000,
      connects: 7,
      failed_connects: 2,
      last_session_at: "2026-11-21T01:33:07Z",
      since: "2026-11-20T12:00:00Z",
    },
    sessions: [
      {
        id: "aaaaaaaaaaaa",
        callsign: "N0CALL-1",
        started_at: "2026-11-21T01:32:53Z",
        duration_s: 14,
        messages_in: 0,
        messages_out: 0,
        bytes_in: 0,
        bytes_out: 0,
        outcome: "ended_early",
      },
      {
        id: "bbbbbbbbbbbb",
        callsign: "K1ABC",
        started_at: "2026-11-21T01:29:52Z",
        duration_s: 185,
        messages_in: 1,
        messages_out: 2,
        bytes_in: 120,
        bytes_out: 300,
        outcome: "completed",
      },
      {
        id: "cccccccccccc",
        callsign: null,
        started_at: "2026-11-20T23:00:00Z",
        duration_s: 3700,
        messages_in: 0,
        messages_out: 0,
        bytes_in: 0,
        bytes_out: 0,
        outcome: "unknown",
      },
    ],
  },
  modem: {
    counters: {
      frames_heard: 42,
      frames_sent: 9,
      last_heard_at: "2026-11-21T01:34:00Z",
      last_sent_at: null,
      since: "2026-11-20T12:00:00Z",
    },
  },
  notes: [] as string[],
};

const running = {
  enabled: true,
  callsign: "W1AW",
  checks: [
    { id: "gateway_admin", ok: true, reason: "Answering." },
    { id: "gateway_telnet", ok: true, reason: "Accepting connections." },
    { id: "modem_kiss", ok: true, reason: "Accepting connections." },
  ],
  problems: [],
  activity,
};

const modemDown = {
  ...running,
  checks: [
    running.checks[0],
    running.checks[1],
    { id: "modem_kiss", ok: false, reason: "Not running (the connection was refused)." },
  ],
  problems: ["The packet modem is not accepting connections. Check the logs (see How to set this up below)."],
};

const off = {
  enabled: false,
  callsign: null,
  checks: [
    { id: "gateway_admin", ok: false, reason: "Could not reach it (the service may not be running)." },
    { id: "gateway_telnet", ok: false, reason: "Could not reach it (the service may not be running)." },
    { id: "modem_kiss", ok: false, reason: "Could not reach it (the service may not be running)." },
  ],
  problems: [],
};

function renderWinlink() {
  return render(
    <MantineProvider theme={theme}>
      <Winlink />
    </MantineProvider>,
  );
}

describe("Winlink", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("shows a running gateway with its callsign and every check answering", async () => {
    vi.mocked(api).mockResolvedValue(running);
    renderWinlink();
    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(screen.getByText("W1AW")).toBeInTheDocument();
    expect(screen.getAllByText("Answering")).toHaveLength(3);
    expect(screen.queryByText("Not answering")).not.toBeInTheDocument();
    expect(screen.queryByText("Turn it on")).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/api/winlink");
  });

  it("explains how to turn it on, with the exact commands, when it is not enabled, and shows no error", async () => {
    vi.mocked(api).mockResolvedValue(off);
    renderWinlink();
    expect(await screen.findByText("Not running")).toBeInTheDocument();
    expect(screen.getByText("Turn it on")).toBeInTheDocument();
    expect(screen.getByText(/docker compose --profile winlink up -d --build/)).toBeInTheDocument();
    expect(screen.getByText(/cp winlink\.env\.example winlink\.env && chmod 600 winlink\.env/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Checks")).not.toBeInTheDocument();
  });

  it("says in words which check is failing and what to look at", async () => {
    vi.mocked(api).mockResolvedValue(modemDown);
    renderWinlink();
    expect(await screen.findByText("Needs attention")).toBeInTheDocument();
    expect(screen.getAllByText("Answering")).toHaveLength(2);
    expect(screen.getByText("Not answering")).toBeInTheDocument(); // a word, not only a color
    expect(screen.getByText("Not running (the connection was refused).")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("see How to set this up below");
    expect(screen.queryByText("Turn it on")).not.toBeInTheDocument();
  });

  it("always says it is amateur radio only, not for GMRS, and offers no start or stop", async () => {
    vi.mocked(api).mockResolvedValue(running);
    renderWinlink();
    await screen.findByText("Running");
    expect(screen.getByText(/not for GMRS/)).toBeInTheDocument();
    expect(screen.getAllByText(/cannot start or stop/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/not shown yet/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /start|stop|restart/i })).not.toBeInTheDocument();
  });

  it("says plainly when the callsign is not known to the dashboard", async () => {
    vi.mocked(api).mockResolvedValue({ ...running, callsign: null });
    renderWinlink();
    expect(await screen.findByText(/not shown\. Add it in How to set this up below/)).toBeInTheDocument();
  });

  it("shows a failed load with a way to try again", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockRejectedValueOnce(new Error("Server unreachable")).mockResolvedValue(running);
    renderWinlink();
    expect(await screen.findByText("Couldn't load Winlink status")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Running")).toBeInTheDocument();
  });

  describe("plain wording", () => {
    // What a repeater owner reads. The collapsed How to set this up section may hold commands and file names; nothing else may.
    const DEVELOPER_WORDS = [
      /experimental/i,
      /bench/i,
      /#219/,
      /docs\/winlink\.md/,
      /\bADR\b/,
      /\bslice\b/i,
      /LinBPQ/i,
      /Dire Wolf/i,
      /KISS/,
      /tmpfs/i,
      /container/i,
      /MOR_/,
      /docker/i,
    ];

    function readingText() {
      const copy = document.body.cloneNode(true) as HTMLElement;
      copy.querySelectorAll("[data-setup-section]").forEach((node) => node.remove());
      return copy.textContent ?? "";
    }

    const states: [string, unknown][] = [
      ["running", running],
      ["a check failing", modemDown],
      ["switched off", off],
      ["no sessions or counters folder", { ...running, activity: null }],
      ["a part not reporting", { ...running, activity: { ...activity, modem: null, notes: ["The modem has not reported yet. It may not be running."] } }],
    ];

    it.each(states)("keeps developer notes out of the page while %s", async (_name, state) => {
      vi.mocked(api).mockResolvedValue(state);
      renderWinlink();
      await screen.findByRole("region", { name: "Winlink gateway status" });
      const text = readingText();
      for (const word of DEVELOPER_WORDS) expect(text).not.toMatch(word);
    });

    it("keeps the exact setup steps in one collapsed How to set this up section, with a plain guide link", async () => {
      const user = userEvent.setup();
      vi.mocked(api).mockResolvedValue(off);
      renderWinlink();
      await screen.findByText("Not running");
      expect(document.querySelectorAll("[data-setup-section]")).toHaveLength(1);
      const control = screen.getByRole("button", { name: "How to set this up" });
      expect(control).toHaveAttribute("aria-expanded", "false");
      await user.click(screen.getByRole("button", { name: "Show the steps" }));
      expect(screen.getByRole("button", { name: "How to set this up" })).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByRole("link", { name: "Read the guide", hidden: true })).toHaveAttribute("href", expect.stringMatching(/docs\/winlink\.md$/));
    });
  });

  describe("counters", () => {
    async function counters() {
      vi.mocked(api).mockResolvedValue(running);
      renderWinlink();
      await screen.findByText("Running");
      return within(screen.getByRole("region", { name: "Winlink counters" }));
    }
    const valueOf = (region: ReturnType<typeof within>, label: string) =>
      region.getByText(label).closest("dt")!.nextElementSibling as HTMLElement;

    it("shows the gateway's counters with the right number against each label", async () => {
      const region = await counters();
      expect(valueOf(region, "Sessions today")).toHaveTextContent(/^2$/);
      expect(valueOf(region, "Sessions since it started")).toHaveTextContent(/^5$/);
      expect(valueOf(region, "Messages from stations")).toHaveTextContent(/^3$/);
      expect(valueOf(region, "Messages to stations")).toHaveTextContent(/^4$/);
      expect(valueOf(region, "Connections accepted")).toHaveTextContent(/^7$/);
      expect(valueOf(region, "Connections that never reached Winlink")).toHaveTextContent(/^2$/);
      expect(valueOf(region, "Sessions that ended early")).toHaveTextContent(/^1$/);
    });

    it("writes message sizes in plain units and says they are before compression", async () => {
      const region = await counters();
      expect(valueOf(region, "Message data from stations")).toHaveTextContent("1.5 KB");
      expect(valueOf(region, "Message data to stations")).toHaveTextContent("2.4 MB");
    });

    it("shows what the modem heard and sent, and says it cannot count how often the transmitter was keyed", async () => {
      const region = await counters();
      expect(valueOf(region, "Frames heard")).toHaveTextContent(/^42$/);
      expect(valueOf(region, "Frames sent")).toHaveTextContent(/^9$/);
      expect(region.getByText(/does not count each time it keys the transmitter/)).toBeInTheDocument();
    });

    it("says when the counts began, since a restart starts them again", async () => {
      const region = await counters();
      expect(region.getByText(/Counting since/)).toBeInTheDocument();
    });

    it("says at least when the day's session list was full", async () => {
      const full = structuredClone(running);
      full.activity.gateway.counters.sessions_today_is_minimum = true;
      vi.mocked(api).mockResolvedValue(full);
      renderWinlink();
      await screen.findByText("Running");
      const region = within(screen.getByRole("region", { name: "Winlink counters" }));
      expect(valueOf(region, "Sessions today")).toHaveTextContent("at least 2");
    });

    it("says in words what a part that is not reporting means, and still shows the part that is", async () => {
      const partial = structuredClone(running);
      partial.activity.modem = null as never;
      partial.activity.notes = ["The modem is not reporting: its last report is too old, so it may have stopped."];
      vi.mocked(api).mockResolvedValue(partial);
      renderWinlink();
      await screen.findByText("Running");
      const region = within(screen.getByRole("region", { name: "Winlink counters" }));
      expect(valueOf(region, "Sessions today")).toHaveTextContent(/^2$/);
      expect(region.queryByText("Frames heard")).not.toBeInTheDocument();
      expect(region.getByText(/The modem is not reporting/)).toBeInTheDocument();
    });

    it("says the counters are not set up when this install has no status folder, and shows no zeros in their place", async () => {
      vi.mocked(api).mockResolvedValue({ ...running, activity: null });
      renderWinlink();
      await screen.findByText("Running");
      expect(screen.getByText(/Sessions and counters are not available yet/)).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Winlink counters" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Recent Winlink sessions" })).not.toBeInTheDocument();
    });

    it("shows neither counters nor sessions while the gateway is switched off", async () => {
      vi.mocked(api).mockResolvedValue({ ...off, activity: null });
      renderWinlink();
      await screen.findByText("Not running");
      expect(screen.queryByRole("region", { name: "Winlink counters" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Recent Winlink sessions" })).not.toBeInTheDocument();
    });
  });

  describe("recent sessions", () => {
    async function sessions() {
      vi.mocked(api).mockResolvedValue(running);
      renderWinlink();
      await screen.findByText("Running");
      return within(screen.getByRole("region", { name: "Recent Winlink sessions" }));
    }

    it("lists each session newest first with the station, length, message counts and how it ended in words", async () => {
      const region = await sessions();
      const rows = region.getAllByRole("row").slice(1); // the first row is the headings
      expect(rows).toHaveLength(3);
      const first = within(rows[0]);
      expect(first.getByText("N0CALL-1")).toBeInTheDocument();
      expect(first.getByText("14s")).toBeInTheDocument();
      expect(first.getByText("Ended early")).toBeInTheDocument();
      const second = within(rows[1]);
      expect(second.getByText("K1ABC")).toBeInTheDocument();
      expect(second.getByText("3m 5s")).toBeInTheDocument();
      expect(second.getByText("Completed")).toBeInTheDocument();
      expect(second.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(
        expect.arrayContaining(["1", "2"]), // one message from the station, two to it
      );
    });

    it("has a name for every column, so a screen reader can tell from-station from to-station", async () => {
      const region = await sessions();
      for (const name of ["Station", "Started", "Length", "From station", "To station", "How it ended"]) {
        expect(region.getByRole("columnheader", { name })).toBeInTheDocument();
      }
    });

    it("says a station whose callsign could not be read is unknown, and an hour-long session in hours", async () => {
      const region = await sessions();
      const last = within(region.getAllByRole("row")[3]);
      expect(last.getByText("unknown")).toBeInTheDocument();
      expect(last.getByText("1h 2m")).toBeInTheDocument();
    });

    it("says there are no sessions yet, in plain words, when the gateway has had none", async () => {
      const empty = structuredClone(running);
      empty.activity.gateway.sessions = [];
      empty.activity.gateway.counters.sessions_total = 0;
      vi.mocked(api).mockResolvedValue(empty);
      renderWinlink();
      await screen.findByText("Running");
      const region = within(screen.getByRole("region", { name: "Recent Winlink sessions" }));
      expect(region.getByText(/No sessions yet/)).toBeInTheDocument();
      expect(region.queryByRole("table")).not.toBeInTheDocument();
    });

    it("never shows anything but the callsign of a station: no message text field exists to show", async () => {
      const region = await sessions();
      expect(region.queryByText(/subject|body|password/i)).not.toBeInTheDocument();
    });
  });

  it("has a Winlink entry in the Integrations group of the navigation", () => {
    const group = NAV.find((g) => g.label === "Integrations");
    expect(group?.items.some((item) => item.id === "winlink" && item.label === "Winlink")).toBe(true);
  });
});

describe("Winlink polling", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue(running);
  });
  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  it("polls every 30 seconds, not faster, and keeps quiet while the tab is hidden", async () => {
    renderWinlink();
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_000);
    });
    expect(api).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(api).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("stops polling when the page is closed", async () => {
    const { unmount } = renderWinlink();
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(api).toHaveBeenCalledTimes(1);
  });
});
