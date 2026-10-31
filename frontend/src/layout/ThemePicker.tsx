import { SegmentedControl, useMantineColorScheme } from "@mantine/core";
import { memo } from "react";

const THEME_OPTIONS = [
  { label: "Auto", value: "auto" },
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
] as const;

/** Memoized so live /ws/status updates don't re-animate the highlight. */
export const ThemePicker = memo(function ThemePicker() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  return (
    <SegmentedControl
      size="xs"
      fullWidth
      value={colorScheme}
      onChange={(value) => setColorScheme(value as "auto" | "light" | "dark")}
      data={[...THEME_OPTIONS]}
    />
  );
});
