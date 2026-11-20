import { Stack } from "@mantine/core";
import { Page } from "../layout/AppShell";
import { NtpCard } from "./datetime/NtpCard";
import { TimeZoneCard } from "./datetime/TimeZoneCard";

/**
 * Date and time: the station's clock. One card per concern, each saving only its own settings, so they sit side by
 * side without touching each other: the time zone, and the time server with the check of the Pi's clock. Another card
 * goes below.
 */
export function DateTime() {
  return (
    <Page title="Date and time" description="The station's time zone, and whether the Pi's clock is right.">
      <Stack gap="md">
        <TimeZoneCard />
        <NtpCard />
      </Stack>
    </Page>
  );
}
