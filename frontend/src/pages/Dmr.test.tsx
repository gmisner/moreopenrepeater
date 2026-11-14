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
