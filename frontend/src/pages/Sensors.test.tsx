import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext, type Config } from "../config";

vi.mock("@mantine/notifications", () => ({
  notifications: {
    show: vi.fn(),
  },
}));

vi.mock("../api", () => ({
  api: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

import { api } from "../api";
import { notifications } from "@mantine/notifications";
import { Sensors } from "./Sensors";

const SITE_SENSOR = {
  id: "battery",
  name: "Battery",
  type: "ads1115" as const,
  units: "V",
  scale: 11,
  offset: 0,
  device: "",
  bus: 1,
  address: 0x48,
  channel: 0,
  high: 14.5,
  low: 11.5,
  hysteresis: 0.3,
  alert: true,
  speak_on_alert: false,
};

const STATUS = {
  enabled: true,
  poll_interval: 30,
  sensors: [
    {
      ...SITE_SENSOR,
      value: 13.2,
      at: 1_700_000_000,
      alert_state: null,
      error: null,
      history: [
        { at: 1_700_000_000 - 60, value: 13.1 },
        { at: 1_700_000_000, value: 13.2 },
      ],
    },
  ],
};

function renderSensors(config: Config | null, save: (patch: Config) => Promise<Config> = vi.fn()) {
  return render(
    <MantineProvider>
      <ConfigContext.Provider
        value={{
          config,
          loading: false,
          refresh: vi.fn(),
          save,
        }}
      >
        <Sensors />
      </ConfigContext.Provider>
    </MantineProvider>,
  );
}

describe("Sensors page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(notifications.show).mockClear();
  });

  it("loads status with history and shows the reading", async () => {
    vi.mocked(api).mockResolvedValue(STATUS);

    renderSensors({
      sensors_enabled: true,
      sensors_poll_interval: 30,
      site_sensors: [SITE_SENSOR],
    });

    expect(await screen.findByDisplayValue("battery")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Battery")).toBeInTheDocument();
    expect(screen.getAllByText(/13\.2 V/).length).toBeGreaterThanOrEqual(1);
    expect(api).toHaveBeenCalledWith("/api/sensors?history_hours=6");
    expect(screen.getByText("Recent (6 h)")).toBeInTheDocument();
    expect(screen.getByText("13.1 V")).toBeInTheDocument();
  });

  it("saves a new sensor through the list form", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockResolvedValue({
      sensors_enabled: false,
      sensors_poll_interval: 30,
      site_sensors: [{ ...SITE_SENSOR, id: "sensor1", name: "Sensor 1" }],
    });
    vi.mocked(api).mockResolvedValue({ enabled: false, poll_interval: 30, sensors: [] });

    renderSensors({ sensors_enabled: false, sensors_poll_interval: 30, site_sensors: [] }, save);

    expect(await screen.findByText(/No sensors yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add sensor" }));
    expect(screen.getByDisplayValue("sensor1")).toBeInTheDocument();

    const sensorsHeading = screen.getByRole("heading", { name: "Sensors" });
    const sensorsForm = sensorsHeading.closest("form");
    expect(sensorsForm).toBeTruthy();
    await user.click(within(sensorsForm as HTMLElement).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        site_sensors: [
          expect.objectContaining({
            id: "sensor1",
            name: "Sensor 1",
            type: "ds18b20",
            units: "C",
          }),
        ],
      }),
    );
  });
});
