import { useState } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  ConfigContext,
  buildConfigPatch,
  notifyError,
  notifySuccess,
  useConfigSlice,
  type Config,
} from "./config";

vi.mock("@mantine/notifications", () => ({
  notifications: {
    show: vi.fn(),
  },
}));

import { notifications } from "@mantine/notifications";

type SliceValues = { callsign: string; require_ctcss_hz: string; transmitter_enabled: boolean };

const KEYS = ["callsign", "require_ctcss_hz", "transmitter_enabled"] as const;
const DEFAULTS: SliceValues = {
  callsign: "",
  require_ctcss_hz: "",
  transmitter_enabled: false,
};

function SliceProbe() {
  const slice = useConfigSlice<SliceValues>([...KEYS], DEFAULTS);
  return (
    <div>
      <span data-testid="callsign">{slice.values.callsign}</span>
      <span data-testid="transmitter">{String(slice.values.transmitter_enabled)}</span>
      <span data-testid="dirty">{String(slice.dirty)}</span>
      <span data-testid="saving">{String(slice.saving)}</span>
      <button type="button" onClick={() => slice.setField("callsign", "W1AW")}>
        edit-callsign
      </button>
      <button type="button" onClick={() => slice.setField("callsign", "K1ABC")}>
        revise-callsign
      </button>
      <button type="button" onClick={() => slice.setField("require_ctcss_hz", "")}>
        clear-ctcss
      </button>
      <button type="button" onClick={() => slice.reset()}>
        reset
      </button>
      <button type="button" onClick={() => void slice.submit()}>
        submit
      </button>
    </div>
  );
}

function renderWithConfig(config: Config | null, save: (patch: Config) => Promise<Config>) {
  function Provider() {
    const [current, setCurrent] = useState(config);
    return (
      <ConfigContext.Provider value={{
        config: current,
        loading: false,
        refresh: vi.fn(),
        save: async (patch) => {
          const next = await save(patch);
          setCurrent(next);
          return next;
        },
      }}>
        <SliceProbe />
      </ConfigContext.Provider>
    );
  }
  return render(<Provider />);
}

describe("buildConfigPatch", () => {
  it("includes booleans and non-empty values for edited keys only", () => {
    expect(
      buildConfigPatch(
        { callsign: "W1AW", transmitter_enabled: true, ignored: "x" },
        ["callsign", "transmitter_enabled"],
      ),
    ).toEqual({ callsign: "W1AW", transmitter_enabled: true });
  });

  it("emits clear_<name> for empty clearable fields", () => {
    expect(buildConfigPatch({ require_ctcss_hz: "" }, ["require_ctcss_hz"])).toEqual({
      clear_require_ctcss_hz: true,
    });
    expect(buildConfigPatch({ require_ctcss_hz: null }, ["require_ctcss_hz"])).toEqual({
      clear_require_ctcss_hz: true,
    });
  });

  it("keeps empty string for non-clearable string fields", () => {
    expect(buildConfigPatch({ callsign: "" }, ["callsign"])).toEqual({ callsign: "" });
  });

  it("skips keys missing from values", () => {
    expect(buildConfigPatch({ callsign: "W1AW" }, ["missing"])).toEqual({});
  });
});

describe("notify helpers", () => {
  beforeEach(() => {
    vi.mocked(notifications.show).mockClear();
  });

  it("shows ApiError and Error messages in red", () => {
    notifyError(new ApiError(400, "bad radio"));
    expect(notifications.show).toHaveBeenCalledWith({ color: "red", message: "bad radio" });

    notifyError(new Error("boom"));
    expect(notifications.show).toHaveBeenCalledWith({ color: "red", message: "boom" });
  });

  it("shows success in green", () => {
    notifySuccess("Saved");
    expect(notifications.show).toHaveBeenCalledWith({ color: "green", message: "Saved" });
  });
});

describe("useConfigSlice", () => {
  beforeEach(() => {
    vi.mocked(notifications.show).mockClear();
  });

  it("hydrates from the mocked provider config", async () => {
    renderWithConfig(
      {
        callsign: "N0CALL",
        require_ctcss_hz: "100.0",
        transmitter_enabled: true,
      },
      vi.fn(),
    );

    await waitFor(() => expect(screen.getByTestId("callsign")).toHaveTextContent("N0CALL"));
    expect(screen.getByTestId("dirty")).toHaveTextContent("false");
  });

  it("tracks dirty edits, reset, and clearable submit patches", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockResolvedValue({
      callsign: "W1AW",
      require_ctcss_hz: null,
      transmitter_enabled: true,
    });

    renderWithConfig(
      {
        callsign: "N0CALL",
        require_ctcss_hz: "100.0",
        transmitter_enabled: true,
      },
      save,
    );

    await waitFor(() => expect(screen.getByTestId("callsign")).toHaveTextContent("N0CALL"));

    await user.click(screen.getByRole("button", { name: "edit-callsign" }));
    expect(screen.getByTestId("callsign")).toHaveTextContent("W1AW");
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");

    await user.click(screen.getByRole("button", { name: "reset" }));
    expect(screen.getByTestId("callsign")).toHaveTextContent("N0CALL");
    expect(screen.getByTestId("dirty")).toHaveTextContent("false");

    await user.click(screen.getByRole("button", { name: "edit-callsign" }));
    await user.click(screen.getByRole("button", { name: "clear-ctcss" }));
    await user.click(screen.getByRole("button", { name: "submit" }));

    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith({
      callsign: "W1AW",
      clear_require_ctcss_hz: true,
    });
    expect(notifications.show).toHaveBeenCalledWith({ color: "green", message: "Saved" });
    expect(screen.getByTestId("dirty")).toHaveTextContent("false");
    expect(screen.getByTestId("callsign")).toHaveTextContent("W1AW");
  });

  it("keeps edited fields and refreshes untouched fields when another card saves", async () => {
    const user = userEvent.setup();
    const context = {
      config: { callsign: "N0CALL", transmitter_enabled: false },
      loading: false,
      refresh: vi.fn(),
      save: vi.fn(),
    };
    const view = render(
      <ConfigContext.Provider value={context}><SliceProbe /></ConfigContext.Provider>,
    );
    await user.click(screen.getByRole("button", { name: "edit-callsign" }));

    view.rerender(
      <ConfigContext.Provider value={{ ...context, config: { callsign: "NEW1", transmitter_enabled: true } }}>
        <SliceProbe />
      </ConfigContext.Provider>,
    );
    expect(screen.getByTestId("callsign")).toHaveTextContent("W1AW");
    expect(screen.getByTestId("transmitter")).toHaveTextContent("true");
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");
    await user.click(screen.getByRole("button", { name: "reset" }));
    expect(screen.getByTestId("callsign")).toHaveTextContent("NEW1");
  });

  it("keeps an edit made while the previous version is saving", async () => {
    const user = userEvent.setup();
    let finish!: (config: Config) => void;
    const save = vi.fn(() => new Promise<Config>((resolve) => { finish = resolve; }));
    renderWithConfig({ callsign: "N0CALL" }, save);

    await user.click(screen.getByRole("button", { name: "edit-callsign" }));
    await user.click(screen.getByRole("button", { name: "submit" }));
    expect(save).toHaveBeenCalledWith({ callsign: "W1AW" });
    await user.click(screen.getByRole("button", { name: "revise-callsign" }));
    await act(async () => finish({ callsign: "W1AW" }));

    expect(screen.getByTestId("callsign")).toHaveTextContent("K1ABC");
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");
  });

  it("surfaces save failures without clearing dirty state", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockRejectedValue(new ApiError(500, "save failed"));

    renderWithConfig({ callsign: "N0CALL", require_ctcss_hz: "", transmitter_enabled: false }, save);

    await waitFor(() => expect(screen.getByTestId("callsign")).toHaveTextContent("N0CALL"));
    await user.click(screen.getByRole("button", { name: "edit-callsign" }));

    await act(async () => {
      await user.click(screen.getByRole("button", { name: "submit" }));
    });

    await waitFor(() =>
      expect(notifications.show).toHaveBeenCalledWith({ color: "red", message: "save failed" }),
    );
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");
    expect(screen.getByTestId("saving")).toHaveTextContent("false");
  });
});
