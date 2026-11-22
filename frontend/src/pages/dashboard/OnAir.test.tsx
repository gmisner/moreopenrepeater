import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveStatus } from "../../status";

let live: LiveStatus | null = null;
vi.mock("../../status", () => ({ useStatusLive: () => live }));

import { OnAir, stateLabel, stateTone } from "./OnAir";

const config = { tot_duration: 180, hang_time: 3, id_interval: 600 } as never;

function show() {
  return render(
    <MantineProvider>
      <MemoryRouter>
        <OnAir config={config} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

function stage(name: string) {
  const node = screen.getByText(name, { selector: ".mor-path-name" }).closest("li") as HTMLElement;
  return { node, value: within(node).getByText(/.+/, { selector: ".mor-path-value" }).textContent, lit: node.getAttribute("data-lit") };
}

// What the API sends for a core it can't hear (RemoteCore.snapshot once stale): nothing keyed, nothing receiving.
const OUTAGE: LiveStatus = {
  state: "core_unavailable",
  ptt_active: false,
  cos_active: false,
  ctcss_hz: null,
  transmitter_enabled: true,
  locked_out: false,
  net_active: false,
  tot_remaining: null,
  hang_remaining: null,
  id_due_in: null,
  callsign: "W1AW",
};

beforeEach(() => {
  live = null;
});

describe("the on-air panel when the core can't be heard", () => {
  it("says 'Core unavailable' in a warning tone, not as a transmission", () => {
    live = OUTAGE;
    const { container } = show();
    const heading = screen.getByRole("heading", { name: "Core unavailable" });
    expect(heading.getAttribute("data-tone")).toBe("unavailable");
    expect(container.querySelector(".mor-onair")?.getAttribute("data-tone")).toBe("unavailable");
    expect(screen.getByText(/can't reach the repeater's core/i)).toBeTruthy();
  });

  it("lights no stage of the signal path", () => {
    live = OUTAGE;
    show();
    for (const name of ["Receiver", "Tone", "Controller", "Transmitter"]) {
      expect(stage(name).lit).toBeNull();
    }
    expect(document.querySelectorAll(".mor-path-node[data-wire]").length).toBe(0);
  });

  it("shows Unknown, not Quiet, Off or Not running", () => {
    live = OUTAGE;
    show();
    expect(stage("Receiver").value).toBe("Unknown");
    expect(stage("Tone").value).toBe("Unknown");
    expect(stage("Transmitter").value).toBe("Unknown");
    expect(stage("Controller").value).toBe("Core unavailable");
    const timers = within(screen.getByLabelText("Timers"));
    expect(timers.getAllByText("Unknown")).toHaveLength(3);
    for (const placeholder of ["Quiet", "Off", "Not running", "None owed"]) {
      expect(screen.queryByText(placeholder)).toBeNull();
    }
    expect(screen.queryByText(/limit$/)).toBeNull();
  });

  it("is not overridden by a lockout kept from the last status", () => {
    live = { ...OUTAGE, locked_out: true };
    const { container } = show();
    expect(container.querySelector(".mor-onair")?.getAttribute("data-tone")).toBe("unavailable");
  });
});

describe("the on-air panel with a live core", () => {
  it("still shows the real receiver, controller and timers", () => {
    live = { ...OUTAGE, state: "idle", callsign: "W1AW" };
    show();
    expect(stage("Receiver").value).toBe("Quiet");
    expect(stage("Transmitter").value).toBe("Off");
    expect(screen.getByRole("heading", { name: "Idle" }).getAttribute("data-tone")).toBe("idle");
    expect(screen.getByText("3:00 limit")).toBeTruthy();
    expect(screen.getAllByText("Not running").length).toBeGreaterThan(0);
  });

  it("lights the receiver and the controller while repeating", () => {
    live = { ...OUTAGE, state: "receiving", cos_active: true, ctcss_hz: 100 };
    show();
    expect(stage("Receiver").lit).toBe("rx");
    expect(stage("Controller").lit).toBe("rx");
  });

  it("lights the transmitter while keyed", () => {
    live = { ...OUTAGE, state: "hang_time", ptt_active: true, hang_remaining: 2 };
    show();
    expect(stage("Transmitter").value).toBe("Keyed");
    expect(stage("Transmitter").lit).toBe("tx");
    expect(stage("Controller").lit).toBe("busy");
  });
});

describe("state tone and label", () => {
  it("keeps the tone of every known state", () => {
    expect(stateTone("idle")).toBe("idle");
    expect(stateTone("receiving")).toBe("rx");
    expect(stateTone("timeout")).toBe("alert");
    expect(stateTone("lockout")).toBe("alert");
    for (const state of ["transmitting_id", "courtesy_tone", "hang_time", "announcing", "patch"]) {
      expect(stateTone(state)).toBe("tx");
    }
  });

  it("draws the outage and a state it doesn't know as neither idle nor transmitting", () => {
    expect(stateTone("core_unavailable")).toBe("unavailable");
    expect(stateTone("something_new")).toBe("unavailable");
    expect(stateLabel("core_unavailable")).toBe("Core unavailable");
    expect(stateLabel("something_new")).toBe("something new");
  });
});
