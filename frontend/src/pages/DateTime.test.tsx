import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext, type Config } from "../config";

vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));
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

import { notifications } from "@mantine/notifications";
import { api, ApiError } from "../api";
import { DateTime } from "./DateTime";
import { groupZones, stationClock, timeIn, type TimezoneInfo } from "./datetime/zones";

// jsdom has no scrollIntoView, which the dropdown calls to keep the highlighted zone in view.
Element.prototype.scrollIntoView = () => {};

const ZONES = ["America/Chicago", "America/Phoenix", "Asia/Kolkata", "Europe/London", "UTC"];

function info(over: Partial<TimezoneInfo> = {}): TimezoneInfo {
  return {
    setting: "",
    effective: "America/Phoenix",
    default: "America/Phoenix",
    default_source: "container",
    now: "2026-11-20T11:57:38.522556-07:00",
    zones: ZONES,
    ...over,
  };
}

function renderPage(config: Config | null, save: (patch: Config) => Promise<Config>) {
  function Provider() {
    const [current, setCurrent] = useState(config);
    return (
      <MantineProvider>
        <ConfigContext.Provider
          value={{
            config: current,
            loading: false,
            refresh: vi.fn(),
            save: async (patch) => {
              const next = await save(patch);
              setCurrent(next);
              return next;
            },
          }}
        >
          <DateTime />
        </ConfigContext.Provider>
      </MantineProvider>
    );
  }
  return render(<Provider />);
}

async function pick(user: ReturnType<typeof userEvent.setup>, zone: string) {
  await user.click(await screen.findByRole("combobox", { name: /Station time zone/ }));
  await user.click(await screen.findByRole("option", { name: zone, hidden: true }));
}

describe("Date and time page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(notifications.show).mockClear();
  });
  afterEach(() => vi.useRealTimers());

  it("shows a loading state until the zones arrive", async () => {
    let release: (value: TimezoneInfo) => void = () => {};
    vi.mocked(api).mockReturnValue(new Promise<TimezoneInfo>((resolve) => (release = resolve)) as never);
    renderPage({ timezone: "" }, vi.fn());
    expect(screen.getByRole("status")).toHaveTextContent("Loading time zones");
    expect(screen.queryByRole("combobox", { name: /Station time zone/ })).not.toBeInTheDocument();
    await act(async () => release(info()));
    expect(await screen.findByRole("combobox", { name: /Station time zone/ })).toBeInTheDocument();
  });

  it("says plainly when the zones could not be loaded, and tries again on request", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockRejectedValueOnce(new Error("The repeater didn't answer")).mockResolvedValue(info());
    renderPage({ timezone: "" }, vi.fn());
    expect(await screen.findByRole("alert")).toHaveTextContent("The repeater didn't answer");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("combobox", { name: /Station time zone/ })).toBeInTheDocument();
  });

  it("shows the default, where it came from, the live time and the repeater's own clock", async () => {
    vi.mocked(api).mockResolvedValue(info());
    renderPage({ timezone: "" }, vi.fn());
    expect(await screen.findByText(/This is the time zone the repeater's container was started with/)).toBeInTheDocument();
    expect(screen.getByText(/MOR_TZ in docker\/\.env/)).toBeInTheDocument();
    expect(screen.getByText(/In America\/Phoenix it is/)).toBeInTheDocument();
    expect(screen.getByText("11:57 (UTC-07:00)")).toBeInTheDocument();
    expect(screen.getByText(/Times on this page are in your browser's time zone\. Announcements and schedules use the station's\./)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Default (America/Phoenix)")).toBeInTheDocument();
  });

  it("names the machine's own setting when that is where the default came from", async () => {
    vi.mocked(api).mockResolvedValue(info({ default: "Europe/London", effective: "Europe/London", default_source: "system" }));
    renderPage({ timezone: "" }, vi.fn());
    expect(await screen.findByText(/This is the time zone this machine is set to/)).toBeInTheDocument();
  });

  it("does not make up a default it cannot name", async () => {
    vi.mocked(api).mockResolvedValue(info({ default: "", effective: "", default_source: "unknown" }));
    renderPage({ timezone: "" }, vi.fn());
    expect(await screen.findByText(/its name couldn't be worked out from here/)).toBeInTheDocument();
    expect(screen.queryByText(/ it is /)).not.toBeInTheDocument();
  });

  it("keeps the live clock moving in the chosen zone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2026-11-20T12:00:00Z"));
    vi.mocked(api).mockResolvedValue(info({ setting: "Asia/Kolkata", effective: "Asia/Kolkata" }));
    renderPage({ timezone: "Asia/Kolkata" }, vi.fn());
    const clock = await screen.findByText(/5:30:0\d PM/);
    const first = clock.textContent;
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText(/5:30:\d\d PM/).textContent).not.toBe(first);
  });

  it("changes the zone, shows the new time at once, and saves only that setting", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockResolvedValue(info());
    const save = vi.fn().mockResolvedValue({ timezone: "Asia/Kolkata" });
    renderPage({ timezone: "" }, save);
    await pick(user, "Asia/Kolkata");
    expect(screen.getByText(/In Asia\/Kolkata it is/)).toBeInTheDocument();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(screen.getByText(/That changes when you save/)).toBeInTheDocument();
    expect(screen.getByText(/Chosen here, so it overrides the default/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ timezone: "Asia/Kolkata" }));
  });

  it("shows saved: a green note, no unsaved bar, and the repeater's clock read again", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockResolvedValue(info());
    renderPage({ timezone: "" }, vi.fn().mockResolvedValue({ timezone: "Asia/Kolkata" }));
    await pick(user, "Asia/Kolkata");
    vi.mocked(api).mockResolvedValue(info({ setting: "Asia/Kolkata", effective: "Asia/Kolkata", now: "2026-11-21T00:27:38+05:30" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("00:27 (UTC+05:30)")).toBeInTheDocument();
    expect(notifications.show).toHaveBeenCalledWith({ color: "green", message: "Saved" });
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument());
    expect(screen.getByText(/The repeater is using Asia\/Kolkata/)).toBeInTheDocument();
  });

  it("shows the refusal when the repeater will not take the zone, and keeps the change to fix", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockResolvedValue(info());
    const message = "timezone: Value error, 'Mars/Base' is not a time zone this station knows. Use a name like America/Phoenix or Europe/London.";
    renderPage({ timezone: "" }, vi.fn().mockRejectedValue(new ApiError(422, message)));
    await pick(user, "Europe/London");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: "red", message }));
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("can go back to the default by clearing the choice", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockResolvedValue(info({ setting: "Asia/Kolkata", effective: "Asia/Kolkata" }));
    const save = vi.fn().mockResolvedValue({ timezone: "" });
    renderPage({ timezone: "Asia/Kolkata" }, save);
    expect(await screen.findByRole("combobox", { name: /Station time zone/ })).toHaveValue("Asia/Kolkata");
    await user.click(screen.getByLabelText("Use the default time zone"));
    expect(screen.getByText(/In America\/Phoenix it is/)).toBeInTheDocument();
    expect(screen.getByText(/Using the default/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ timezone: "" }));
  });

  it("discards an unsaved choice", async () => {
    const user = userEvent.setup();
    vi.mocked(api).mockResolvedValue(info({ setting: "Asia/Kolkata", effective: "Asia/Kolkata" }));
    renderPage({ timezone: "Asia/Kolkata" }, vi.fn());
    await pick(user, "Europe/London");
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByRole("combobox", { name: /Station time zone/ })).toHaveValue("Asia/Kolkata");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("says so when the install has no zone list, rather than offering an empty picker", async () => {
    vi.mocked(api).mockResolvedValue(info({ zones: [] }));
    renderPage({ timezone: "" }, vi.fn());
    expect(await screen.findByText("No time zone data")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Station time zone/ })).toBeDisabled();
  });
});

describe("time zone helpers", () => {
  it("groups zones by region and puts region-less names under Other", () => {
    expect(groupZones(ZONES)).toEqual([
      { group: "America", items: ["America/Chicago", "America/Phoenix"] },
      { group: "Asia", items: ["Asia/Kolkata"] },
      { group: "Europe", items: ["Europe/London"] },
      { group: "Other", items: ["UTC"] },
    ]);
  });

  it("reads a half-hour zone to the minute", () => {
    expect(timeIn("Asia/Kolkata", new Date("2026-11-20T12:00:00Z"))).toMatch(/5:30:00 PM/);
    expect(timeIn("America/Phoenix", new Date("2026-11-20T12:00:00Z"))).toMatch(/5:00:00 AM/);
  });

  it("returns nothing for a zone the browser does not know", () => {
    expect(timeIn("Mars/Base", new Date())).toBeNull();
  });

  it("shows the repeater's clock as written, UTC included", () => {
    expect(stationClock("2026-11-20T11:57:38.5-07:00")).toBe("11:57 (UTC-07:00)");
    expect(stationClock("2026-11-20T18:57:38Z")).toBe("18:57 (UTC+00:00)");
  });
});
