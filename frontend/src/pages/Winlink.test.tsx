import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NAV } from "../nav";
import { theme } from "../theme";
import { Winlink } from "./Winlink";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";

const running = {
  enabled: true,
  callsign: "W1AW",
  checks: [
    { id: "gateway_admin", ok: true, reason: "Answering." },
    { id: "gateway_telnet", ok: true, reason: "Accepting connections." },
    { id: "modem_kiss", ok: true, reason: "Accepting connections." },
  ],
  problems: [],
};

const modemDown = {
  ...running,
  checks: [
    running.checks[0],
    running.checks[1],
    { id: "modem_kiss", ok: false, reason: "Connection refused: nothing is listening there." },
  ],
  problems: ["The packet modem is not accepting connections. Look at `docker compose logs direwolf`."],
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
    expect(screen.getByText("Connection refused: nothing is listening there.")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("docker compose logs direwolf");
    expect(screen.queryByText("Turn it on")).not.toBeInTheDocument();
  });

  it("always says it is amateur radio only, not for GMRS, and that sessions are not shown yet", async () => {
    vi.mocked(api).mockResolvedValue(running);
    renderWinlink();
    await screen.findByText("Running");
    expect(screen.getByText(/not for GMRS/)).toBeInTheDocument();
    expect(screen.getByText(/Sessions and message counters are not shown yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /start|stop|restart/i })).not.toBeInTheDocument();
  });

  it("says plainly when the callsign is not known to the dashboard", async () => {
    vi.mocked(api).mockResolvedValue({ ...running, callsign: null });
    renderWinlink();
    expect(await screen.findByText(/not shown here/)).toBeInTheDocument();
  });

  it("shows a failed load with a way to try again", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockRejectedValueOnce(new Error("Server unreachable")).mockResolvedValue(running);
    renderWinlink();
    expect(await screen.findByText("Couldn't load Winlink status")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Running")).toBeInTheDocument();
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
