import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { serialNothingFound } from "../../deviceUsage";
import { theme } from "../../theme";
import { SerialPortSelect } from "./SerialPortSelect";

const PORT = { path: "/dev/serial/by-id/usb-FTDI_FT232R-if00-port0", target: "/dev/ttyUSB0" };

function renderSelect(ports: { path: string; target: string }[], saved = "") {
  const onChange = vi.fn();
  render(
    <MantineProvider theme={theme}>
      <SerialPortSelect label="PTT serial port" ports={ports} saved={saved} usage={[]} onChange={onChange} />
    </MantineProvider>,
  );
  return onChange;
}

describe("the serial port drop-down", () => {
  it("says what is missing and the command to run when the core sees no port", async () => {
    const user = userEvent.setup();
    renderSelect([]);
    await user.click(screen.getByRole("combobox", { name: "PTT serial port" }));
    const message = await screen.findByText(/The core sees no serial port/, undefined, { timeout: 2000 });
    expect(message.textContent).toContain("./setup.sh --serial /dev/serial/by-id/NAME");
    expect(message.textContent).toContain("docker folder");
  });

  it("offers the adapters the core was given", async () => {
    const user = userEvent.setup();
    const onChange = renderSelect([PORT]);
    await user.click(screen.getByRole("combobox", { name: "PTT serial port" }));
    await user.click(await screen.findByRole("option", { name: new RegExp(PORT.path), hidden: true }));
    expect(onChange).toHaveBeenCalledWith(PORT.path);
    expect(screen.queryByText(/The core sees no serial port/)).not.toBeInTheDocument();
  });

  it("does not repeat the setup command when a typed name just matches nothing", () => {
    expect(serialNothingFound([PORT])).toBe("No serial port matches.");
    expect(serialNothingFound([])).toContain("./setup.sh --serial");
  });
});
