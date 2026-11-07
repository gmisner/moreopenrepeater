import { Select, type SelectProps } from "@mantine/core";
import { CTCSS_TONES } from "./constants";

const TONE_DATA = CTCSS_TONES.map((hz) => ({ value: String(hz), label: `${hz.toFixed(1)} Hz` }));

/** Shared TX CTCSS picker. Native overflow so wheel scrolls the long tone list
 * instead of the page (which closes the dropdown and blocks typing). */
export function CtcssToneSelect({
  value,
  onChange,
  label = "Transmit CTCSS tone",
  placeholder = "off",
  ...rest
}: Omit<SelectProps, "data" | "value" | "onChange" | "searchable" | "clearable" | "withScrollArea"> & {
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
      // ScrollArea.Autosize over a portaled dropdown on a long page lets the
      // document steal the wheel; the list stays put, the page moves, and the
      // popover closes — so typing/filtering feels broken too. Native overflow
      // keeps the wheel on the tone list (Styles API required when ScrollArea is off).
      withScrollArea={false}
      styles={{
        dropdown: {
          maxHeight: 220,
          overflowY: "auto",
          overscrollBehavior: "contain",
        },
      }}
      comboboxProps={{ withinPortal: true, zIndex: 400 }}
    />
  );
}
