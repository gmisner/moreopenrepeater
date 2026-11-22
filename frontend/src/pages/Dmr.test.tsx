import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext, type Config } from "../config";
import { theme } from "../theme";
import { Dmr } from "./Dmr";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

let mockRole = "viewer";
vi.mock("../session", () => ({
  useSession: () => ({ session: { authenticated: true, role: mockRole } }),
}));

vi.mock("@mantine/notifications", () => ({
  notifications: { show: vi.fn() },
}));

import { api } from "../api";

function renderDmr(config: Config | null = {}) {
  return render(
    <MantineProvider theme={theme}>
      <ConfigContext.Provider
        value={{
          config,
          loading: false,
          refresh: vi.fn(),
          save: vi.fn(async () => config ?? {}),
          reload: vi.fn(),
        }}
      >
        <Dmr />
      </ConfigContext.Provider>
    </MantineProvider>,
  );
}

describe("Dmr role gating", () => {
  beforeEach(() => {
    mockRole = "viewer";
    vi.mocked(api).mockResolvedValue({
      available: true,
      enabled: true,
      error: null,
      settings: {
        callsign: "W1AW",
        dmr_id: "3120001",
        repeater_ssid: "01",
        has_password: true,
        network: "brandmeister",
        host: "3102.repeater.net",
        port: 62031,
        talkgroup: 3100,
        slot: 2,
        color_code: 1,
        location: "Newington",
        description: "test",
        use_emulator: true,
      },
      audio: { configured: true, running: true, error: null, peer: null, listen: null, keyed: false, last_heard: null },
      services: { analog_bridge: true, mmdvm_bridge: true, md380_emu: true },
      restart: { available: false, requested: false, last_action: null, state: null, message: null },
    });
  });

  it("highlights only the favorite matching talkgroup and slot", async () => {
    mockRole = "admin";
    vi.mocked(api).mockResolvedValue({
      available: true,
      enabled: true,
      error: null,
      settings: {
        callsign: "W1AW",
        dmr_id: "3120001",
        repeater_ssid: "01",
        has_password: true,
        network: "brandmeister",
        host: "3102.repeater.net",
        port: 62031,
        talkgroup: 3100,
        slot: 1,
        color_code: 1,
        location: "Newington",
        description: "test",
        use_emulator: true,
      },
      audio: { configured: true, running: true, error: null, peer: null, listen: null, keyed: false, last_heard: null },
      services: { analog_bridge: true, mmdvm_bridge: true, md380_emu: true },
      restart: { available: false, requested: false, last_action: null, state: null, message: null },
    });
    renderDmr({ dmr_talkgroup_favorites: [{ talkgroup: 3100, name: "A", slot: 1 }, { talkgroup: 3100, name: "B", slot: 2 }] });
    await waitFor(() => expect(screen.getByText("DMR")).toBeInTheDocument());
    const buttons = screen.getAllByRole("button", { name: /3100/ });
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveAttribute("data-variant", "filled");
    expect(buttons[1]).toHaveAttribute("data-variant", "light");
  });

  it("does not render retune controls for viewers", async () => {
    renderDmr({ dmr_talkgroup_favorites: [{ talkgroup: 3100, name: "USA", slot: 2 }] });
    await waitFor(() => expect(screen.getByText("DMR")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /3100/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit favorites" })).not.toBeInTheDocument();
  });
});

describe("Dmr draft cancel reset", () => {
  beforeEach(() => {
    mockRole = "admin";
    vi.mocked(api).mockResolvedValue({
      available: true,
      enabled: true,
      error: null,
      settings: {
        callsign: "W1AW",
        dmr_id: "3120001",
        repeater_ssid: "01",
        has_password: true,
        network: "brandmeister",
        host: "3102.repeater.net",
        port: 62031,
        talkgroup: 3100,
        slot: 2,
        color_code: 1,
        location: "Newington",
        description: "test",
        use_emulator: true,
      },
      audio: { configured: true, running: true, error: null, peer: null, listen: null, keyed: false, last_heard: null },
      services: { analog_bridge: true, mmdvm_bridge: true, md380_emu: true },
      restart: { available: false, requested: false, last_action: null, state: null, message: null },
    });
  });

  it("resets an edited favorites draft when the drawer is canceled", async () => {
    const user = userEvent.setup();
    renderDmr({ dmr_talkgroup_favorites: [{ talkgroup: 3100, name: "USA", slot: 2 }] });
    await waitFor(() => expect(screen.getByText("DMR")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Edit favorites" }));
    const talkgroupInput = await screen.findByDisplayValue("3100");
    await user.clear(talkgroupInput);
    await user.type(talkgroupInput, "9999");

    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Edit favorites" }));
    expect(await screen.findByDisplayValue("3100")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("9999")).not.toBeInTheDocument();
  });
});

describe("Dmr mutation pending across poll", () => {
  it("keeps busy during a poll and clears after deferred retune resolves", async () => {
    mockRole = "admin";
    let resolveTalkgroup: ((value: unknown) => void) | undefined;
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/dmr") {
        return {
          available: true,
          enabled: true,
          error: null,
          settings: {
            callsign: "W1AW",
            dmr_id: "3120001",
            repeater_ssid: "01",
            has_password: true,
            network: "brandmeister",
            host: "3102.repeater.net",
            port: 62031,
            talkgroup: 3100,
            slot: 2,
            color_code: 1,
            location: "Newington",
            description: "test",
            use_emulator: true,
          },
          audio: { configured: true, running: true, error: null, peer: null, listen: null, keyed: false, last_heard: null },
          services: { analog_bridge: true, mmdvm_bridge: true, md380_emu: true },
          restart: { available: false, requested: false, last_action: null, state: null, message: null },
        };
      }
      if (path === "/api/dmr/talkgroup") {
        return new Promise((resolve) => {
          resolveTalkgroup = resolve;
        });
      }
      return {};
    });

    const intervalCallbacks: Array<() => void> = [];
    const setIntervalSpy = vi.spyOn(window, "setInterval").mockImplementation(((callback: TimerHandler) => {
      intervalCallbacks.push(callback as () => void);
      return 1 as unknown as number;
    }) as typeof window.setInterval);

    const user = userEvent.setup();
    renderDmr({ dmr_talkgroup_favorites: [{ talkgroup: 3100, name: "USA", slot: 2 }] });
    await waitFor(() => expect(screen.getByText("DMR")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /3100/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /3100/ })).toBeDisabled());

    await act(async () => {
      intervalCallbacks[0]?.();
      await Promise.resolve();
    });

    expect(screen.getByRole("button", { name: /3100/ })).toBeDisabled();
    setIntervalSpy.mockRestore();

    resolveTalkgroup?.({
      available: true,
      enabled: true,
      error: null,
      settings: {
        callsign: "W1AW",
        dmr_id: "3120001",
        repeater_ssid: "01",
        has_password: true,
        network: "brandmeister",
        host: "3102.repeater.net",
        port: 62031,
        talkgroup: 3100,
        slot: 2,
        color_code: 1,
        location: "Newington",
        description: "test",
        use_emulator: true,
      },
      audio: { configured: true, running: true, error: null, peer: null, listen: null, keyed: false, last_heard: null },
      services: { analog_bridge: true, mmdvm_bridge: true, md380_emu: true },
      restart: { available: false, requested: false, last_action: null, state: null, message: null },
    });

    await waitFor(() => expect(screen.getByRole("button", { name: /3100/ })).not.toBeDisabled());
  }, 15000);
});

describe("Dmr plain wording", () => {
  // What a repeater owner reads. The collapsed setup and details sections may hold commands and program names; nothing else may.
  const DEVELOPER_WORDS = [
    /experimental/i,
    /bench/i,
    /#219/,
    /docs\/winlink\.md/,
    /\bADR\b/,
    /\bslice\b/i,
    /Analog_Bridge/,
    /MMDVM/,
    /md380/i,
    /USRP/,
    /DVSwitch/i,
    /SET_INFO/,
    /exportMetadata/,
    /install-pi/,
    /docker/i,
    /\bapt\b/,
    /systemctl/,
    /Debian/,
  ];

  const settings = {
    callsign: "W1AW",
    dmr_id: "3120001",
    repeater_ssid: "01",
    has_password: true,
    network: "brandmeister",
    host: "3102.repeater.net",
    port: 62031,
    talkgroup: 3100,
    slot: 2,
    color_code: 1,
    location: "Newington",
    description: "test",
    use_emulator: true,
  };
  const audio = { configured: true, running: true, error: null, peer: "127.0.0.1:34001", listen: "127.0.0.1:32001", keyed: false, last_heard: null };
  const restart = { available: false, requested: false, last_action: null, state: null, message: null };
  const running = {
    available: true,
    enabled: true,
    error: null,
    settings,
    audio,
    services: { analog_bridge: true, mmdvm_bridge: true, md380_emu: true },
    restart,
  };
  const notInstalled = {
    available: false,
    enabled: false,
    error: "DMR is not installed yet. The steps are in How to set this up below.",
    settings: null,
    audio: { ...audio, configured: false, running: false, peer: null, listen: null },
    services: { analog_bridge: null, mmdvm_bridge: null, md380_emu: null },
    restart,
  };

  const states: [string, unknown][] = [
    ["running with nothing heard", running],
    ["running with a station heard", { ...running, audio: { ...audio, last_heard: { radio_id: 3120002, callsign: "K1ABC", name: "Pat", talkgroup: 3100, slot: 2, started_at: 1_700_000_000, ended_at: null, active: true } } }],
    ["part of the bridge down", { ...running, services: { analog_bridge: true, mmdvm_bridge: false, md380_emu: true } }],
    ["not installed", notInstalled],
    ["not installed, install offered", { ...notInstalled, restart: { ...restart, available: true } }],
    ["bridge not started yet", { ...notInstalled, available: true, error: "The DMR bridge has not started yet, so it has not saved its settings. The steps to start it are in How to set this up below." }],
  ];

  function readingText() {
    const copy = document.body.cloneNode(true) as HTMLElement;
    copy.querySelectorAll("[data-setup-section], [data-details]").forEach((node) => node.remove());
    return copy.textContent ?? "";
  }

  beforeEach(() => {
    mockRole = "admin";
  });

  it.each(states)("keeps developer notes out of the page while %s", async (_name, state) => {
    vi.mocked(api).mockResolvedValue(state);
    renderDmr();
    await waitFor(() => expect(screen.getByText("DMR bridge")).toBeInTheDocument());
    await waitFor(() => expect(api).toHaveBeenCalled());
    const text = readingText();
    for (const word of DEVELOPER_WORDS) expect(text).not.toMatch(word);
  });

  it("keeps the install steps in one collapsed section, and a guide link", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockResolvedValue(notInstalled);
    renderDmr();
    await screen.findByRole("button", { name: "Show the steps" });
    expect(document.querySelectorAll("[data-setup-section]")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "How to set this up" })).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("button", { name: "Show the steps" }));
    expect(screen.getByRole("button", { name: "How to set this up" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("sudo bash scripts/install-pi.sh --dmr")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read the guide", hidden: true })).toHaveAttribute("href", expect.stringMatching(/docs\/dmr\.md$/));
  });

  it("walks through setup as steps that follow what is really done, and goes away once DMR is connected", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockResolvedValue(notInstalled);
    const first = renderDmr();
    expect(await screen.findByText("Get DMR running")).toBeInTheDocument();
    expect(screen.getByText(/0 of 2 done/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Install DMR on this machine, to do/ })).toHaveAttribute("aria-current", "step");
    // Nothing to run yet, so there is no "Set up DMR" button until it is installed.
    expect(screen.queryByRole("button", { name: "Set up DMR" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show the steps" }));
    first.unmount();

    vi.mocked(api).mockResolvedValue({ ...notInstalled, available: true, error: null, restart });
    const second = renderDmr();
    expect(await screen.findByText(/1 of 2 done/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Install DMR on this machine, done/ })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: /Enter your callsign and DMR details, to do/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "Set up DMR" })).toBeInTheDocument();
    second.unmount();

    vi.mocked(api).mockResolvedValue(running);
    renderDmr();
    await waitFor(() => expect(screen.getByText("DMR bridge")).toBeInTheDocument());
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(screen.queryByText("Get DMR running")).not.toBeInTheDocument();
  });

  it("says which kind of trouble it is when part of the bridge is down, and keeps the program names in Details", async () => {
    vi.mocked(api).mockResolvedValue({ ...running, services: { analog_bridge: true, mmdvm_bridge: false, md380_emu: true } });
    renderDmr();
    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText(/Part of the DMR bridge is not running/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Details" }));
    expect(await screen.findByText(/MMDVM_Bridge down/)).toBeInTheDocument();
  });
});
