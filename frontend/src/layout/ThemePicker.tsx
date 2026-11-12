import { SegmentedControl, Stack, Text, useMantineColorScheme } from "@mantine/core";
import { memo, useEffect, useState } from "react";
import {
  applyDensity,
  readDensity,
  readPollAggressiveness,
  writePollAggressiveness,
  type PollAggressiveness,
  type UiDensity,
} from "../prefs";

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

const POLL_OPTIONS: { label: string; value: PollAggressiveness }[] = [
  { label: "Smooth", value: "smooth" },
  { label: "Balanced", value: "balanced" },
  { label: "Light", value: "light" },
];

/** Memoized so live /ws/status updates don't re-animate the highlight. */
export const ThemePicker = memo(function ThemePicker() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const [density, setDensity] = useState<UiDensity>(() => readDensity());
  const [poll, setPoll] = useState<PollAggressiveness>(() => readPollAggressiveness());

  useEffect(() => {
    applyDensity(density);
  }, [density]);

  useEffect(() => {
    writePollAggressiveness(poll);
  }, [poll]);

  return (
    <Stack gap={6}>
      <Text size="sm" fw={600}>
        Theme
      </Text>
      <SegmentedControl
        size="xs"
        fullWidth
        aria-label="Theme"
        value={colorScheme}
        onChange={(value) => setColorScheme(value as "auto" | "light" | "dark")}
        data={[...THEME_OPTIONS]}
      />
      <Text size="sm" fw={600} mt={6}>
        Display size
      </Text>
      <SegmentedControl
        size="xs"
        fullWidth
        aria-label="Display size"
        value={density}
        onChange={(value) => setDensity(value as UiDensity)}
        data={DENSITY_OPTIONS}
      />
      <Text size="sm" fw={600} mt={6}>
        Live meters
      </Text>
      <SegmentedControl
        size="xs"
        fullWidth
        aria-label="Live meter update rate"
        value={poll}
        onChange={(value) => setPoll(value as PollAggressiveness)}
        data={POLL_OPTIONS}
      />
      <Text size="xs" c="dimmed">
        Smooth is finer on a laptop. Light is gentler when Chromium shares the Pi with the radio.
      </Text>
    </Stack>
  );
});
