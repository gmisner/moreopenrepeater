export const SPARE_PINS = [1, 2, 4, 5, 6, 7, 8] as const;

export type GpioPinConfig = {
  mode?: "output" | "input";
  name?: string;
  invert?: boolean;
  on_say?: string;
  off_say?: string;
  on_macro?: string;
  off_macro?: string;
};

export type GpioSchedule = {
  pin: number;
  time: string;
  minutes: number;
  days: number[];
  enabled: boolean;
};

export type GpioPinStatus = {
  pin: number;
  name: string;
  mode: "output" | "input" | "fan" | null;
  on: boolean | null;
  until: string | null;
};

export type FanStatus = {
  output: "none" | "cm108" | "gpio";
  pin: number | null;
  on: boolean | null;
  reason: "transmitting" | "run-on" | "hot" | null;
  run_on_left: number | null;
  temperature_c: number | null;
  error: string | null;
};

export type GpioStatus = {
  available: boolean;
  error: string | null;
  pins: GpioPinStatus[];
  schedules: { pin: number; next_start: string | null; until: string | null }[];
  fan: FanStatus | null;
};

export function pinLabel(pin: number | string, name?: string) {
  return name ? `${name} (GPIO${pin})` : `GPIO${pin}`;
}

export function gpioStateText(pin: GpioPinStatus, available: boolean) {
  if (!pin.mode || !available || pin.on == null) return "–";
  return pin.on ? "On" : "Off";
}

export function fanText(fan: FanStatus | null) {
  if (!fan || fan.on == null) return "–";
  if (!fan.on) return "Off";
  if (fan.reason === "run-on" && fan.run_on_left != null) {
    const left = Math.ceil(fan.run_on_left);
    return `On · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left`;
  }
  if (fan.reason === "transmitting") return "On · transmitting";
  if (fan.reason === "hot") return "On · CPU hot";
  return "On";
}

export function shortTime(value: string) {
  const when = new Date(value);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return time;
  const farOff = when.getTime() - Date.now() > 6 * 24 * 60 * 60 * 1000;
  const day = when.toLocaleDateString(
    [],
    farOff ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "short" },
  );
  return `${day} ${time}`;
}
