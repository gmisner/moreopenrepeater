import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext, type Config } from "../config";
import { theme } from "../theme";
import { SetupWizardProvider, useSetupWizard } from "./SetupWizard";

vi.mock("../api", () => ({ api: vi.fn() }));

let mockRole = "admin";
vi.mock("../session", () => ({
  useSession: () => ({ session: { authenticated: true, role: mockRole, auth_required: true } }),
}));

vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));

import { api } from "../api";

const save = vi.fn(async (patch: Config) => patch);

function routes(path: string) {
  if (path === "/api/boards") return Promise.resolve([]);
  if (path === "/api/audio/devices") return Promise.resolve([]);
  if (path === "/api/audio/engine") return Promise.resolve({ running: false, error: null, cos_open: false, transmitting: false });
  return Promise.resolve({});
}

function OpenButton() {
  const { openWizard } = useSetupWizard();
  return <button onClick={openWizard}>Run setup again</button>;
}

function renderWizard() {
  return render(
    <MantineProvider theme={theme}>
      <ConfigContext.Provider
        value={{ config: { callsign: "W1AW" }, loading: false, refresh: vi.fn(async () => ({ callsign: "W1AW" })), save, reload: vi.fn() }}
      >
        <SetupWizardProvider>
          <OpenButton />
        </SetupWizardProvider>
      </ConfigContext.Provider>
    </MantineProvider>,
  );
}

async function openDialog() {
  const dialog = await screen.findByRole("dialog");
  return within(dialog);
}

describe("SetupWizard progress Stepper", () => {
  beforeEach(() => {
    mockRole = "admin";
    save.mockClear();
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(((path: string) => routes(path)) as never);
  });

  it("shows one step per wizard step and marks the first as current", async () => {
    renderWizard();
    const dialog = await openDialog();
    const steps = dialog.getAllByRole("button", { name: /^(Station|Radio|Sound|Test|Account),/ });
    expect(steps.map((s) => s.textContent)).toEqual(["1Station", "2Radio", "3Sound", "4Test", "5Account"]);
    expect(steps[0]).toHaveAttribute("aria-current", "step");
    expect(steps[1]).not.toHaveAttribute("aria-current");
    expect(steps.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Station, current step",
      "Radio, to do",
      "Sound, to do",
      "Test, to do",
      "Account, to do",
    ]);
    expect(dialog.getByText("Step 1 of 5")).toBeInTheDocument();
  });

  it("follows Next and Back, and leaves earlier steps marked done", async () => {
    const user = userEvent.setup();
    renderWizard();
    const dialog = await openDialog();
    await user.click(dialog.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(dialog.getByText("Step 2 of 5")).toBeInTheDocument());
    expect(save).toHaveBeenCalledWith({ callsign: "W1AW", id_mode: "voice" });
    expect(dialog.getByRole("button", { name: /^Radio,/ })).toHaveAttribute("aria-current", "step");
    expect(dialog.getByRole("button", { name: /^Station,/ })).not.toHaveAttribute("aria-current");
    expect(dialog.getByRole("button", { name: "Station, done" })).toHaveAttribute("data-completed", "true");

    await user.click(dialog.getByRole("button", { name: "Back" }));
    expect(dialog.getByText("Step 1 of 5")).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: /^Station,/ })).toHaveAttribute("aria-current", "step");
  });

  it("does not let a step be clicked, since leaving a step saves it", async () => {
    const user = userEvent.setup();
    renderWizard();
    const dialog = await openDialog();
    await user.click(dialog.getByRole("button", { name: /^Test,/ }));
    expect(dialog.getByText("Step 1 of 5")).toBeInTheDocument();
  });

  it("leaves out the admin account step for someone who is not an admin", async () => {
    mockRole = "operator";
    const user = userEvent.setup();
    renderWizard();
    await user.click(screen.getByRole("button", { name: "Run setup again" }));
    const dialog = await openDialog();
    const steps = dialog.getAllByRole("button", { name: /^(Station|Radio|Sound|Test|Account),/ });
    expect(steps.map((s) => s.textContent)).toEqual(["1Station", "2Radio", "3Sound", "4Test"]);
    expect(dialog.getByText("Step 1 of 4")).toBeInTheDocument();
  });
});
