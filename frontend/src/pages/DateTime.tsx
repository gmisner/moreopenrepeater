import { Stack } from "@mantine/core";
import { Page } from "../layout/AppShell";
import { TimeZoneCard } from "./datetime/TimeZoneCard";

/**
 * Date and time: the station's clock. One card per concern, each saving only its own settings, so another can sit
 * beside the time zone (the time server, for one) without touching it: add its card below.
 */
export function DateTime() {
  return (
    <Page title="Date and time">
      <Stack gap="md">
        <TimeZoneCard />
      </Stack>
    </Page>
  );
}
