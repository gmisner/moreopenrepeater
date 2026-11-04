import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext, type Config } from "../config";
import { Identification } from "./Identification";

vi.mock("@mantine/notifications", () => ({
  notifications: {
    show: vi.fn(),
  },
}));

import { notifications } from "@mantine/notifications";

function renderIdentification(config: Config | null, save: (patch: Config) => Promise<Config>) {
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
        <Identification />
      </ConfigContext.Provider>
    </MantineProvider>,
  );
}

describe("Identification page", () => {
  beforeEach(() => {
    vi.mocked(notifications.show).mockClear();
  });

  it("uppercases callsign on save and clears the unsaved banner", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockResolvedValue({ callsign: "W1AW" });

    renderIdentification({ callsign: "N0CALL" }, save);

    const callsign = await screen.findByLabelText("Callsign");
    expect(callsign).toHaveValue("N0CALL");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();

    await user.clear(callsign);
    await user.type(callsign, "w1aw");
    expect(callsign).toHaveValue("W1AW");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith({ callsign: "W1AW" }));
    expect(notifications.show).toHaveBeenCalledWith({ color: "green", message: "Saved" });
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument());
    expect(callsign).toHaveValue("W1AW");
  });

  it("discards edits back to the hydrated baseline", async () => {
    const user = userEvent.setup();

    renderIdentification({ callsign: "N0CALL" }, vi.fn());

    const callsign = await screen.findByLabelText("Callsign");
    await user.clear(callsign);
    await user.type(callsign, "w1aw");
    expect(callsign).toHaveValue("W1AW");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Discard" }));

    expect(callsign).toHaveValue("N0CALL");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });
});
