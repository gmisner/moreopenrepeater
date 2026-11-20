import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext } from "../../config";

const session = vi.hoisted(() => ({ role: "admin" }));

vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));
vi.mock("../../session", () => ({ useSession: () => ({ session: { role: session.role } }) }));
vi.mock("../../api", () => ({
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
import { api, ApiError } from "../../api";
import { DateTime } from "../DateTime";
import { NtpCard } from "./NtpCard";
import { durationWords, offsetSentence, type TimeContainer, type TimeStatus } from "./ntp";

// jsdom has no scrollIntoView, which the time zone dropdown calls (the page test below renders both cards).
Element.prototype.scrollIntoView = () => {};

const NO_CONTAINER: TimeContainer = {
  running: false,
  synced: null,
  stratum: null,
  offset_ms: null,
  server: null,
  config_error: null,
  updated_at: null,
};

function container(over: Partial<TimeContainer> = {}): TimeContainer {
  return {
    running: true,
    synced: true,
    stratum: 2,
    offset_ms: 0.4,
    server: "pool.ntp.org",
    config_error: null,
    updated_at: 1_790_000_000,
    ...over,
  };
}

function status(over: Partial<TimeStatus> = {}): TimeStatus {
  return {
    server: "pool.ntp.org",
    configured_server: "",
    default_server: "pool.ntp.org",
    state: "ok",
    reachable: true,
    offset_ms: -8,
    round_trip_ms: 23.4,
    stratum: 2,
    limit_ms: 2000,
    checked_at: 1_790_000_000,
    error: null,
    error_text: null,
    container: NO_CONTAINER,
    ...over,
  };
}

/** Answer GET /api/time with `get` and PUT /api/time/ntp with `put` (a function, so it can see the body or refuse). */
function serve(get: TimeStatus | (() => Promise<TimeStatus>), put?: (server: string) => Promise<TimeStatus>) {
  vi.mocked(api).mockImplementation((async (path: string, options?: { method?: string; json?: { server: string } }) => {
    if (path === "/api/time/ntp" && options?.method === "PUT") return put ? put(options.json?.server ?? "") : status();
    if (path === "/api/time") return typeof get === "function" ? get() : get;
    throw new Error(`unexpected request ${path}`);
  }) as never);
}

function renderCard() {
  return render(
    <MantineProvider>
      <NtpCard />
    </MantineProvider>,
  );
}

const field = () => screen.findByRole("textbox", { name: /NTP server/ });
const saveButton = () => screen.getByRole("button", { name: "Save" });
const check = () => within(screen.getByRole("group", { name: "Clock check" }));
const box = () => within(screen.getByRole("group", { name: "Time container" }));
const calls = (path: string) => vi.mocked(api).mock.calls.filter(([p]) => p === path);

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
}

describe("NTP card", () => {
  beforeEach(() => {
    session.role = "admin";
    vi.mocked(api).mockReset();
    vi.mocked(notifications.show).mockClear();
  });
  afterEach(() => {
    vi.useRealTimers();
    setHidden(false);
  });

  describe("loading and failing to load", () => {
    it("shows a loading state until the check arrives", async () => {
      let release: (value: TimeStatus) => void = () => {};
      serve(() => new Promise<TimeStatus>((resolve) => (release = resolve)));
      renderCard();
      expect(screen.getByRole("status")).toHaveTextContent("Loading the clock check");
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
      await act(async () => release(status()));
      expect(await field()).toBeInTheDocument();
    });

    it("says plainly when the check could not be loaded, and tries again on request", async () => {
      const user = userEvent.setup();
      vi.mocked(api).mockRejectedValueOnce(new Error("The repeater didn't answer")).mockResolvedValue(status());
      renderCard();
      expect(await screen.findByRole("alert")).toHaveTextContent("The repeater didn't answer");
      expect(screen.getByText("Couldn't check the clock")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await field()).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  describe("the clock check", () => {
    it("says a good clock is good, in words, with the round trip and what the check does", async () => {
      serve(status());
      renderCard();
      await field();
      expect(check().getByText("Good")).toBeInTheDocument();
      expect(check().getByText("The Pi's clock is 8 ms ahead of pool.ntp.org.")).toBeInTheDocument();
      expect(check().getByText("Round trip 23.4 ms, server stratum 2.")).toBeInTheDocument();
      expect(check().getByText("The check asks pool.ntp.org what time it is, over the network, and sends nothing else.")).toBeInTheDocument();
      expect(screen.queryByText(/station ID times/)).not.toBeInTheDocument();
    });

    it("names the chosen server in the check, not the default", async () => {
      serve(status({ server: "time.example.org", configured_server: "time.example.org" }));
      renderCard();
      await field();
      expect(check().getByText("The Pi's clock is 8 ms ahead of time.example.org.")).toBeInTheDocument();
      expect(check().getByText(/The check asks time\.example\.org what time it is/)).toBeInTheDocument();
    });

    it("says an off clock is off, how far, and what a wrong clock breaks", async () => {
      serve(status({ state: "off", offset_ms: 4200 }));
      renderCard();
      await field();
      expect(check().getByText("Off")).toBeInTheDocument();
      expect(check().queryByText("Good")).not.toBeInTheDocument();
      expect(check().getByText("The Pi's clock is 4.2 seconds behind pool.ntp.org.")).toBeInTheDocument();
      expect(check().getByText(/Being off by 2 seconds or more throws off the station ID times, the spoken time, scheduled announcements and nets, and the times in the logs/)).toBeInTheDocument();
    });

    it("says an unreachable server is unreachable, why, and that it says nothing about the clock", async () => {
      serve(status({ state: "unreachable", reachable: false, offset_ms: null, round_trip_ms: null, stratum: null, error: "timeout", error_text: "No answer within a couple of seconds. The server may be down, or UDP port 123 may be blocked." }));
      renderCard();
      await field();
      expect(check().getByText("Unreachable")).toBeInTheDocument();
      expect(check().getByText("Couldn't get a time from pool.ntp.org.")).toBeInTheDocument();
      expect(check().getByText(/UDP port 123 may be blocked/)).toBeInTheDocument();
      expect(check().getByText("That says nothing about whether the Pi's clock is right.")).toBeInTheDocument();
      expect(screen.queryByText(/Round trip/)).not.toBeInTheDocument();
      expect(screen.queryByText(/station ID times/)).not.toBeInTheDocument();
    });

    it("says so when nothing has been checked yet", async () => {
      serve(status({ state: "not_checked", reachable: null, offset_ms: null, round_trip_ms: null, stratum: null }));
      renderCard();
      await field();
      expect(check().getByText("Not checked")).toBeInTheDocument();
      expect(check().getByText("The clock hasn't been checked yet.")).toBeInTheDocument();
      expect(screen.queryByText(/behind|ahead of/)).not.toBeInTheDocument();
    });

    it("does not make up a clock reading when an answer came back without one", async () => {
      serve(status({ offset_ms: null }));
      renderCard();
      await field();
      expect(check().getByText("pool.ntp.org answered, but no clock reading came back.")).toBeInTheDocument();
    });
  });

  describe("how far off, in words", () => {
    it.each([
      [8, "The Pi's clock is 8 ms behind pool.ntp.org"],
      [-8, "The Pi's clock is 8 ms ahead of pool.ntp.org"],
      [8.4, "The Pi's clock is 8.4 ms behind pool.ntp.org"],
      [0.4, "The Pi's clock matches pool.ntp.org to within a millisecond"],
      [-0.4, "The Pi's clock matches pool.ntp.org to within a millisecond"],
      [0, "The Pi's clock matches pool.ntp.org to within a millisecond"],
      [1, "The Pi's clock is 1 ms behind pool.ntp.org"],
      [-2400, "The Pi's clock is 2.4 seconds ahead of pool.ntp.org"],
      [1000, "The Pi's clock is 1 second behind pool.ntp.org"],
      [90_000, "The Pi's clock is 1.5 minutes behind pool.ntp.org"],
      [-7_200_000, "The Pi's clock is 2 hours ahead of pool.ntp.org"],
      [-86_400_000 * 3.5, "The Pi's clock is 3.5 days ahead of pool.ntp.org"],
    ])("offset %s ms reads %s", (offset, sentence) => {
      expect(offsetSentence(offset, "pool.ntp.org")).toBe(sentence);
    });

    it("shows a positive offset as the Pi being behind and a negative one as ahead, on the card", async () => {
      serve(status({ offset_ms: 120 }));
      renderCard();
      await field();
      expect(check().getByText("The Pi's clock is 120 ms behind pool.ntp.org.")).toBeInTheDocument();
    });

    it("shows an offset under a millisecond as matching, on the card", async () => {
      serve(status({ offset_ms: 0.3 }));
      renderCard();
      await field();
      expect(check().getByText("The Pi's clock matches pool.ntp.org to within a millisecond.")).toBeInTheDocument();
    });

    it("writes durations in the right unit, without a trailing .0", () => {
      expect(durationWords(0.5)).toBe("0.5 ms");
      expect(durationWords(23)).toBe("23 ms");
      expect(durationWords(999)).toBe("999 ms");
      expect(durationWords(2000)).toBe("2 seconds");
      expect(durationWords(-2000)).toBe("2 seconds");
      expect(durationWords(60_000)).toBe("1 minute");
      expect(durationWords(3_600_000)).toBe("1 hour");
      expect(durationWords(86_400_000)).toBe("1 day");
    });
  });

  describe("the time container", () => {
    it("says what it is and how to start it when it is not running", async () => {
      serve(status());
      renderCard();
      await field();
      expect(box().getByText("Not running")).toBeInTheDocument();
      expect(box().getByText(/optional program \(chrony\) in its own container/)).toBeInTheDocument();
      expect(box().getByText("./setup.sh --time")).toBeInTheDocument();
      expect(box().getByText(/in the docker folder/)).toBeInTheDocument();
      expect(box().getByText(/needs a capability \(CAP_SYS_TIME\) that lets it set the Pi's clock/)).toBeInTheDocument();
      const link = box().getByRole("link", { name: /"The time container" in docs\/docker\.md/ });
      expect(link).toHaveAttribute("href", "https://github.com/gmisner/moreopenrepeater/blob/main/docs/docker.md#the-time-container");
      expect(box().queryByText("Running")).not.toBeInTheDocument();
    });

    it("shows its own synced state, stratum and offset when it is running", async () => {
      serve(status({ container: container() }));
      renderCard();
      await field();
      expect(box().getByText("Running")).toBeInTheDocument();
      expect(box().getByText("Synced")).toBeInTheDocument();
      expect(box().getByText("Synced to pool.ntp.org, stratum 2, off by 0.4 ms.")).toBeInTheDocument();
      expect(box().queryByText("Not running")).not.toBeInTheDocument();
      expect(box().queryByText("./setup.sh --time")).not.toBeInTheDocument();
      expect(box().queryByRole("alert")).not.toBeInTheDocument();
    });

    it("says so when it is running but has not got a time yet", async () => {
      serve(status({ container: container({ synced: false, stratum: null, offset_ms: null }) }));
      renderCard();
      await field();
      expect(box().getByText("Running")).toBeInTheDocument();
      expect(box().getByText("Not synced yet")).toBeInTheDocument();
      expect(box().getByText("Running, but it hasn't got a time from pool.ntp.org yet.")).toBeInTheDocument();
      expect(box().queryByText("Synced")).not.toBeInTheDocument();
    });

    it("says so when it has not reported whether it is in sync", async () => {
      serve(status({ container: container({ synced: null, stratum: null, offset_ms: null }) }));
      renderCard();
      await field();
      expect(box().getByText("It hasn't said whether it is in sync.")).toBeInTheDocument();
    });

    it("shows a config error it reports, and what it keeps using", async () => {
      serve(status({ server: "bad host", configured_server: "bad host", container: container({ config_error: "That is not a valid host name.", server: "pool.ntp.org" }) }));
      renderCard();
      await field();
      expect(box().getByText("The time container refused the server setting")).toBeInTheDocument();
      expect(box().getByText(/That is not a valid host name\. It keeps using pool\.ntp\.org until the setting is fixed\./)).toBeInTheDocument();
      // The container is not "following" a setting it refused.
      expect(box().queryByText(/up to a minute/)).not.toBeInTheDocument();
    });

    it("says it can take up to a minute to follow when the server it uses is not the one that is set", async () => {
      serve(status({ server: "time.example.org", configured_server: "time.example.org", container: container({ server: "pool.ntp.org" }) }));
      renderCard();
      await field();
      expect(box().getByText("The container is still using pool.ntp.org. It can take up to a minute to follow a change.")).toBeInTheDocument();
    });

    it("says nothing about following when it already uses the server that is set", async () => {
      serve(status({ container: container() }));
      renderCard();
      await field();
      expect(screen.queryByText(/up to a minute/)).not.toBeInTheDocument();
    });

    it("shows the follow note right after saving a new server while it is running", async () => {
      const user = userEvent.setup();
      serve(status({ container: container() }), async (server) =>
        status({ server, configured_server: server, container: container({ server: "pool.ntp.org" }) }),
      );
      renderCard();
      await user.type(await field(), "time.example.org");
      await user.click(saveButton());
      expect(await screen.findByText(/The container is still using pool\.ntp\.org/)).toBeInTheDocument();
    });
  });

  describe("changing the server", () => {
    it("shows the current server with the default as the placeholder", async () => {
      serve(status({ server: "time.example.org", configured_server: "time.example.org" }));
      renderCard();
      const input = await field();
      expect(input).toHaveValue("time.example.org");
      expect(input).toHaveAttribute("placeholder", "pool.ntp.org");
      expect(screen.getByText("Using time.example.org. Empty goes back to the default, pool.ntp.org.")).toBeInTheDocument();
    });

    it("shows an empty input with the default as the placeholder when none is set", async () => {
      serve(status());
      renderCard();
      const input = await field();
      expect(input).toHaveValue("");
      expect(input).toHaveAttribute("placeholder", "pool.ntp.org");
      expect(screen.getByText("Using the default, pool.ntp.org.")).toBeInTheDocument();
    });

    it("keeps Save disabled until the value changes, and again once it is back to what is saved", async () => {
      const user = userEvent.setup();
      serve(status({ server: "time.example.org", configured_server: "time.example.org" }));
      renderCard();
      const input = await field();
      expect(saveButton()).toBeDisabled();
      await user.type(input, "x");
      expect(saveButton()).toBeEnabled();
      await user.type(input, "{Backspace}");
      expect(saveButton()).toBeDisabled();
    });

    it("does not count spaces around the same value as a change", async () => {
      const user = userEvent.setup();
      serve(status({ server: "time.example.org", configured_server: "time.example.org" }));
      renderCard();
      await user.type(await field(), "  ");
      expect(saveButton()).toBeDisabled();
    });

    it("saves a new server, trimmed, and shows what the server answered", async () => {
      const user = userEvent.setup();
      serve(status(), async (server) => status({ server, configured_server: server, offset_ms: 3 }));
      renderCard();
      await user.type(await field(), "  time.example.org  ");
      await user.click(saveButton());
      await waitFor(() => expect(calls("/api/time/ntp")).toHaveLength(1));
      expect(calls("/api/time/ntp")[0]).toEqual(["/api/time/ntp", { method: "PUT", json: { server: "time.example.org" } }]);
      expect(await screen.findByText("The Pi's clock is 3 ms behind time.example.org.")).toBeInTheDocument();
      expect(notifications.show).toHaveBeenCalledWith({ color: "green", message: "Saved" });
      expect(await field()).toHaveValue("time.example.org");
      expect(saveButton()).toBeDisabled();
    });

    it("saves with the Enter key too", async () => {
      const user = userEvent.setup();
      serve(status(), async (server) => status({ server, configured_server: server }));
      renderCard();
      await user.type(await field(), "time.example.org{Enter}");
      await waitFor(() => expect(calls("/api/time/ntp")).toHaveLength(1));
    });

    it("saves an empty value to go back to the default", async () => {
      const user = userEvent.setup();
      serve(status({ server: "time.example.org", configured_server: "time.example.org" }), async () => status());
      renderCard();
      await user.clear(await field());
      expect(saveButton()).toBeEnabled();
      await user.click(saveButton());
      await waitFor(() => expect(calls("/api/time/ntp")).toHaveLength(1));
      expect(calls("/api/time/ntp")[0][1]).toEqual({ method: "PUT", json: { server: "" } });
      expect(await screen.findByText("Using the default, pool.ntp.org.")).toBeInTheDocument();
      expect(await field()).toHaveValue("");
    });

    it("shows the server's own words when it refuses the value, and keeps the value to fix", async () => {
      const user = userEvent.setup();
      const message = "That is not a valid host name (empty parts, or a part that starts or ends with a hyphen).";
      serve(status(), async () => {
        throw new ApiError(400, message);
      });
      renderCard();
      await user.type(await field(), "-pool.ntp.org");
      await user.click(saveButton());
      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(await field()).toHaveValue("-pool.ntp.org");
      expect(saveButton()).toBeEnabled();
      expect(notifications.show).not.toHaveBeenCalled();
      // What is shown as the server in use did not change.
      expect(screen.getByText("Using the default, pool.ntp.org.")).toBeInTheDocument();
      // Typing again clears the complaint, so it does not hang around next to a new value.
      await user.type(await field(), "x");
      expect(screen.queryByText(message)).not.toBeInTheDocument();
    });

    it("says so when the save could not reach the repeater at all", async () => {
      const user = userEvent.setup();
      serve(status(), async () => {
        throw new Error("Failed to fetch");
      });
      renderCard();
      await user.type(await field(), "time.example.org");
      await user.click(saveButton());
      expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
      expect(await field()).toHaveValue("time.example.org");
    });
  });

  describe("who may change it", () => {
    it.each(["operator", "viewer"])("shows %s the server read-only, with no input and no Save", async (role) => {
      session.role = role;
      serve(status({ server: "time.example.org", configured_server: "time.example.org" }));
      renderCard();
      expect(await screen.findByText("Only an admin can change the time server.")).toBeInTheDocument();
      expect(screen.getByText("time.example.org", { selector: "span" })).toBeInTheDocument();
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
      // They still see the check and the container.
      expect(check().getByText("Good")).toBeInTheDocument();
      expect(box().getByText("Not running")).toBeInTheDocument();
    });

    it("says when the server being shown is the default", async () => {
      session.role = "viewer";
      serve(status());
      renderCard();
      expect(await screen.findByText(/\(the default\)/)).toBeInTheDocument();
    });

    it("gives an admin the input and Save", async () => {
      serve(status());
      renderCard();
      expect(await field()).toBeInTheDocument();
      expect(saveButton()).toBeInTheDocument();
      expect(screen.queryByText("Only an admin can change the time server.")).not.toBeInTheDocument();
    });
  });

  describe("reading the check again", () => {
    function fakeClock() {
      vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setInterval", "clearInterval"] });
    }

    it("reads it again about every 45 seconds, and not before", async () => {
      fakeClock();
      serve(status());
      renderCard();
      await field();
      expect(calls("/api/time")).toHaveLength(1);
      serve(status({ offset_ms: 5000, state: "off" }));
      await act(async () => {
        vi.advanceTimersByTime(30_000);
      });
      expect(calls("/api/time")).toHaveLength(1);
      await act(async () => {
        vi.advanceTimersByTime(16_000);
      });
      expect(await screen.findByText("The Pi's clock is 5 seconds behind pool.ntp.org.")).toBeInTheDocument();
      expect(check().getByText("Off")).toBeInTheDocument();
    });

    it("does not read it while the tab is hidden", async () => {
      fakeClock();
      serve(status());
      renderCard();
      await field();
      setHidden(true);
      await act(async () => {
        vi.advanceTimersByTime(200_000);
      });
      expect(calls("/api/time")).toHaveLength(1);
    });

    it("reads it again when the tab comes back", async () => {
      serve(status());
      renderCard();
      await field();
      setHidden(true);
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
      });
      expect(calls("/api/time")).toHaveLength(1);
      setHidden(false);
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await waitFor(() => expect(calls("/api/time")).toHaveLength(2));
    });

    it("stops reading when the card goes away", async () => {
      fakeClock();
      serve(status());
      const view = renderCard();
      await field();
      view.unmount();
      await act(async () => {
        vi.advanceTimersByTime(200_000);
      });
      expect(calls("/api/time")).toHaveLength(1);
    });

    it("keeps what you typed when the check is read again", async () => {
      fakeClock();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      serve(status());
      renderCard();
      await user.type(await field(), "time.example.org");
      await act(async () => {
        vi.advanceTimersByTime(46_000);
      });
      await waitFor(() => expect(calls("/api/time")).toHaveLength(2));
      expect(await field()).toHaveValue("time.example.org");
      expect(saveButton()).toBeEnabled();
    });

    it("keeps the last reading and says so when a re-read fails", async () => {
      fakeClock();
      serve(status());
      renderCard();
      await field();
      serve(async () => {
        throw new Error("The repeater didn't answer");
      });
      await act(async () => {
        vi.advanceTimersByTime(46_000);
      });
      expect(await screen.findByText(/Couldn't refresh just now, so this is the last reading\. The repeater didn't answer/)).toBeInTheDocument();
      expect(check().getByText("The Pi's clock is 8 ms ahead of pool.ntp.org.")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      // And the note goes away once a read works again.
      serve(status());
      await act(async () => {
        vi.advanceTimersByTime(46_000);
      });
      await waitFor(() => expect(screen.queryByText(/Couldn't refresh just now/)).not.toBeInTheDocument());
    });

    it("does not let a slow read that began before a save undo what the save returned", async () => {
      fakeClock();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      let release: (value: TimeStatus) => void = () => {};
      let slow = false;
      serve(
        () => (slow ? new Promise<TimeStatus>((resolve) => (release = resolve)) : Promise.resolve(status())),
        async (server) => status({ server, configured_server: server }),
      );
      renderCard();
      await user.type(await field(), "time.example.org");
      slow = true;
      await act(async () => {
        vi.advanceTimersByTime(46_000);
      });
      await waitFor(() => expect(calls("/api/time")).toHaveLength(2)); // the slow read is now waiting
      await user.click(saveButton());
      expect(await screen.findByText("The Pi's clock is 8 ms ahead of time.example.org.")).toBeInTheDocument();
      await act(async () => release(status())); // the stale answer finally arrives: still the default server
      expect(screen.getByText("The Pi's clock is 8 ms ahead of time.example.org.")).toBeInTheDocument();
      expect(await field()).toHaveValue("time.example.org");
    });
  });

  describe("on the Date and time page", () => {
    it("sits below the time zone card, and each keeps to its own request", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => {
        if (path === "/api/time") return status();
        if (path === "/api/timezone") {
          return { setting: "", effective: "America/Phoenix", default: "America/Phoenix", default_source: "container", now: "2026-11-20T11:57:38-07:00", zones: ["America/Phoenix"] };
        }
        throw new Error(`unexpected request ${path}`);
      }) as never);
      render(
        <MantineProvider>
          <ConfigContext.Provider value={{ config: { timezone: "" }, loading: false, refresh: vi.fn(), save: vi.fn() }}>
            <DateTime />
          </ConfigContext.Provider>
        </MantineProvider>,
      );
      expect(await screen.findByRole("combobox", { name: /Station time zone/ })).toBeInTheDocument();
      expect(await field()).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Time zone" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Time server" })).toBeInTheDocument();
    });
  });
});
