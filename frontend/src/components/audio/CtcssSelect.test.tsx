import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { CtcssToneSelect } from "./CtcssToneSelect";

function CtcssSelectHarness() {
  const [value, setValue] = useState<string | null>(null);
  return (
    <MantineProvider>
      <CtcssToneSelect value={value} onChange={setValue} />
    </MantineProvider>
  );
}

describe("CtcssToneSelect", () => {
  it("renders a native-overflow dropdown without ScrollArea", async () => {
    const user = userEvent.setup();
    render(<CtcssSelectHarness />);

    await user.click(screen.getByRole("combobox", { name: "Transmit CTCSS tone" }));

    // jsdom zeros floating-ui layout, so the portaled listbox stays display:none.
    const list = await screen.findByRole("listbox", { hidden: true });
    const dropdown = list.closest(".mantine-Select-dropdown");
    expect(dropdown).not.toBeNull();
    expect(dropdown!.querySelector(".mantine-ScrollArea-root")).toBeNull();
    expect((dropdown as HTMLElement).style.maxHeight).toBe("220px");
    expect((dropdown as HTMLElement).style.overflowY).toBe("auto");
    expect((dropdown as HTMLElement).style.overscrollBehavior).toBe("contain");
  });

  it("lets the operator type to filter and pick a high tone", async () => {
    const user = userEvent.setup();
    render(<CtcssSelectHarness />);

    const input = screen.getByRole("combobox", { name: "Transmit CTCSS tone" });
    await user.click(input);
    await user.keyboard("254");

    const list = await screen.findByRole("listbox", { hidden: true });
    expect(within(list).getByRole("option", { name: "254.1 Hz", hidden: true })).toBeInTheDocument();
    expect(within(list).queryByRole("option", { name: "67.0 Hz", hidden: true })).not.toBeInTheDocument();

    await user.click(within(list).getByRole("option", { name: "254.1 Hz", hidden: true }));
    expect(input).toHaveValue("254.1 Hz");
  });
});
