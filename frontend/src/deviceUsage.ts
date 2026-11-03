/** Which sound card or serial port is already assigned to a radio role. */

export type DeviceAssignment = { device: string; role: string };

export type SerialPortInfo = { path: string; target: string };

export function soundAssignments(cfg: {
  audio_input_device?: string;
  audio_output_device?: string;
  link_radio_input_device?: string;
  link_radio_output_device?: string;
  monitor_input_device?: string;
}): DeviceAssignment[] {
  const out: DeviceAssignment[] = [];
  const add = (device: string | undefined, role: string) => {
    if (device) out.push({ device, role });
  };
  add(cfg.audio_input_device, "repeater RX");
  add(cfg.audio_output_device, "repeater TX");
  add(cfg.link_radio_input_device, "link RX");
  add(cfg.link_radio_output_device, "link TX");
  add(cfg.monitor_input_device, "monitor");
  return out;
}

export function serialAssignments(cfg: {
  cos_serial_device?: string;
  ptt_serial_device?: string;
  link_radio_cos_serial_device?: string;
  link_radio_ptt_serial_device?: string;
}): DeviceAssignment[] {
  const out: DeviceAssignment[] = [];
  const add = (device: string | undefined, role: string) => {
    if (device) out.push({ device, role });
  };
  add(cfg.cos_serial_device, "repeater COS");
  add(cfg.ptt_serial_device, "repeater PTT");
  add(cfg.link_radio_cos_serial_device, "link COS");
  add(cfg.link_radio_ptt_serial_device, "link PTT");
  return out;
}

function serialSame(a: string, b: string, ports: SerialPortInfo[]): boolean {
  if (a === b) return true;
  const left = ports.find((port) => port.path === a || port.target === a);
  const right = ports.find((port) => port.path === b || port.target === b);
  if (left && right) return left.target === right.target;
  if (left) return left.path === b || left.target === b;
  if (right) return right.path === a || right.target === a;
  return false;
}

export function rolesForDevice(
  assignments: DeviceAssignment[],
  device: string,
  ports: SerialPortInfo[] = [],
): string[] {
  const roles: string[] = [];
  for (const entry of assignments) {
    const match = ports.length ? serialSame(entry.device, device, ports) : entry.device === device;
    if (match && !roles.includes(entry.role)) roles.push(entry.role);
  }
  return roles;
}

export function formatDeviceLabel(name: string, roles: string[], missing = false): string {
  const bits = [...roles];
  if (missing) bits.push("not connected");
  return bits.length ? `${name} (${bits.join(", ")})` : name;
}

export function soundDeviceOptions(
  devices: { name: string; inputs: number; outputs: number }[],
  side: "inputs" | "outputs",
  saved: string,
  assignments: DeviceAssignment[],
): { value: string; label: string }[] {
  const names = devices.filter((device) => device[side] > 0).map((device) => device.name);
  const options = names.map((name) => ({
    value: name,
    label: formatDeviceLabel(name, rolesForDevice(assignments, name)),
  }));
  if (saved && !names.includes(saved)) {
    options.push({
      value: saved,
      label: formatDeviceLabel(saved, rolesForDevice(assignments, saved), true),
    });
  }
  return options;
}

/** Prefer the stable by-id path when the saved value is a ttyUSBn alias. */
export function serialSelectValue(ports: SerialPortInfo[], saved: string): string | null {
  if (!saved) return null;
  const match = ports.find((port) => port.path === saved || port.target === saved);
  return match?.path ?? saved;
}

export function serialPortOptions(
  ports: SerialPortInfo[],
  saved: string,
  assignments: DeviceAssignment[],
): { value: string; label: string }[] {
  const options = ports.map((port) => ({
    value: port.path,
    label: formatDeviceLabel(port.path, rolesForDevice(assignments, port.path, ports)),
  }));
  const selected = serialSelectValue(ports, saved);
  if (selected && !options.some((option) => option.value === selected)) {
    options.push({
      value: selected,
      label: formatDeviceLabel(selected, rolesForDevice(assignments, selected, ports), true),
    });
  }
  return options;
}
