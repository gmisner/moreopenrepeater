import { Select } from "@mantine/core";
import type { ReactNode } from "react";
import {
  serialNothingFound,
  serialPortOptions,
  serialSelectValue,
  type DeviceAssignment,
} from "../../deviceUsage";
import type { SerialPort } from "./types";

/** One serial port drop-down of the Audio page. With no port to pick it says what to run on the Pi, since in the
 * container install the core only sees the adapters `./setup.sh --serial` gave it. */
export function SerialPortSelect({
  label,
  ports,
  saved,
  usage,
  onChange,
}: {
  label: ReactNode;
  ports: SerialPort[];
  saved: string;
  usage: DeviceAssignment[];
  onChange: (device: string) => void;
}) {
  return (
    <Select
      label={label}
      data={serialPortOptions(ports, saved, usage)}
      nothingFoundMessage={serialNothingFound(ports)}
      value={serialSelectValue(ports, saved)}
      onChange={(value) => onChange(value || "")}
      searchable
      clearable
      placeholder="/dev/serial/by-id/…"
    />
  );
}
