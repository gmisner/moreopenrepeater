import { Select, type SelectProps } from "@mantine/core";
import { CTCSS_TONES } from "./constants";

const TONE_DATA = CTCSS_TONES.map((hz) => ({ value: String(hz), label: `${hz.toFixed(1)} Hz` }));

/** Shared TX CTCSS picker — tone list + search/clear; scroll/portal come from theme. */
export function CtcssToneSelect({
  value,
  onChange,
  label = "Transmit CTCSS tone",
  placeholder = "off",
  ...rest
}: Omit<SelectProps, "data" | "value" | "onChange" | "searchable" | "clearable"> & {
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  return (
    <Select
      {...rest}
      label={label}
      data={TONE_DATA}
      value={value}
      onChange={onChange}
      searchable
      clearable
      placeholder={placeholder}
    />
  );
}
