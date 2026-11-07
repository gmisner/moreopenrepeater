import { SegmentedControl, Stack, Text, useMantineColorScheme } from "@mantine/core";
import { memo, useEffect, useState } from "react";
import { applyDensity, readDensity, type UiDensity } from "../prefs";

const THEME_OPTIONS = [
  { label: "Auto", value: "auto" },
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
] as const;

const DENSITY_OPTIONS: { label: string; value: UiDensity }[] = [
  { label: "Compact", value: "compact" },
  { label: "Normal", value: "comfortable" },
  { label: "Large", value: "large" },
];

/** Memoized so live /ws/status updates don't re-animate the highlight. */
export const ThemePicker = memo(function ThemePicker() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const [density, setDensity] = useState<UiDensity>(() => readDensity());

  useEffect(() => {
    applyDensity(density);
  }, [density]);

  return (
    <Stack gap={6}>
      <SegmentedControl
        size="xs"
        fullWidth
        value={colorScheme}
        onChange={(value) => setColorScheme(value as "auto" | "light" | "dark")}
        data={[...THEME_OPTIONS]}
      />
      <Text size="xs" c="dimmed">
        Display size
      </Text>
      <SegmentedControl
        size="xs"
        fullWidth
        value={density}
        onChange={(value) => setDensity(value as UiDensity)}
        data={DENSITY_OPTIONS}
      />
    </Stack>
  );
});
