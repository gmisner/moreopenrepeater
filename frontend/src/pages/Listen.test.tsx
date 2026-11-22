import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Listen } from "./Listen";

function answer(body: object) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 200, json: async () => body }));
}

function show() {
  return render(
    <MantineProvider>
      <Listen />
    </MantineProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the public listening page", () => {
  it("says it can't tell, rather than Quiet, when the core can't be heard", async () => {
    answer({ callsign: "W1AW", text: "", on_air: false, receiving: false, unavailable: true, audio: false });
    show();
    await waitFor(() => expect(screen.getByRole("heading", { name: "Can't tell right now" })).toBeTruthy());
    expect(screen.queryByText("Quiet")).toBeNull();
  });

  it("still says Quiet for a repeater that is heard and idle", async () => {
    answer({ callsign: "W1AW", text: "", on_air: false, receiving: false, unavailable: false, audio: false });
    show();
    await waitFor(() => expect(screen.getByRole("heading", { name: "Quiet" })).toBeTruthy());
  });
});
