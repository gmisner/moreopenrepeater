import { Alert, Card, Select, Stack, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "../../api";
import { ConfigActions } from "../../components/ConfigActions";
import { labeled } from "../../components/FieldHint";
import { ErrorCard, LoadingBlock } from "../../components/StateCards";
import { useConfigSlice } from "../../config";
import { groupZones, stationClock, timeIn, type TimezoneInfo } from "./zones";

type TimezoneValues = { timezone: string };

const KEYS: (keyof TimezoneValues)[] = ["timezone"];
const DEFAULTS: TimezoneValues = { timezone: "" };

/** How often the repeater's own clock is read again while the page is open. */
const SERVER_CLOCK_REFRESH_MS = 30_000;

function LiveClock({ zone }: { zone: string }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const text = timeIn(zone, now);
  return text ? <time dateTime={now.toISOString()}>{text}</time> : <span>this browser can&apos;t show that zone</span>;
}

function defaultNote(info: TimezoneInfo) {
  if (info.default_source === "container" && info.default) {
    return `${info.default}. This is the time zone the repeater's container was started with (MOR_TZ in docker/.env, which setup copied from this machine).`;
  }
  if (info.default_source === "system" && info.default) {
    return `${info.default}. This is the time zone this machine is set to.`;
  }
  return "whatever this machine's clock is set to; its name couldn't be worked out from here.";
}

/** The station's time zone. A section of the Date and time page: it saves only its own setting. */
export function TimeZoneCard() {
  const { values, setField, dirty, reset, submit, saving, baseline } = useConfigSlice<TimezoneValues>(KEYS, DEFAULTS);
  const [info, setInfo] = useState<TimezoneInfo | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const load = useCallback(() => {
    return api<TimezoneInfo>("/api/timezone")
      .then((next) => {
        setInfo(next);
        setFailed(null);
      })
      .catch((error: unknown) => setFailed(error instanceof Error ? error.message : String(error)));
  }, []);

  const saved = baseline.timezone;
  // Read again when the saved zone changes (and once more shortly after, since the dashboard hears of a change from
  // the repeater a moment later), and every so often so the repeater's own clock stays current.
  useEffect(() => {
    void load();
    const soon = setTimeout(() => void load(), 2000);
    const again = setInterval(() => void load(), SERVER_CLOCK_REFRESH_MS);
    return () => {
      clearTimeout(soon);
      clearInterval(again);
    };
  }, [load, saved]);

  const data = useMemo(() => groupZones(info?.zones ?? []), [info?.zones]);

  if (failed && !info) {
    return (
      <ErrorCard title="Couldn't load the time zone" onRetry={() => void load()}>
        {failed}
      </ErrorCard>
    );
  }
  if (!info) return <LoadingBlock label="Loading time zones" />;

  const shown = values.timezone || info.default;
  const noZoneData = info.zones.length === 0;

  return (
    <Card
      withBorder
      padding="lg"
      radius="md"
      component="form"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void submit();
      }}
    >
      <Stack gap="md">
        <Title order={2}>Time zone</Title>
        {noZoneData ? (
          <Alert color="yellow" variant="light" title="No time zone data">
            This install has no time zone list, so a zone can&apos;t be chosen here. The repeater keeps using its default.
          </Alert>
        ) : null}
        <Select
          label={labeled(
            "Station time zone",
            "The spoken time in announcements, and anything scheduled for a time of day (announcements, nets, link and weather schedules), follows this zone.",
          )}
          maw={420}
          searchable
          clearable
          clearButtonProps={{ "aria-label": "Use the default time zone" }}
          disabled={noZoneData}
          data={data}
          value={values.timezone || null}
          placeholder={info.default ? `Default (${info.default})` : "Default"}
          nothingFoundMessage="No such time zone"
          maxDropdownHeight={320}
          onChange={(value) => setField("timezone", value ?? "")}
        />
        {shown ? (
          <Text>
            In {shown} it is{" "}
            <Text span fw={600} className="mor-num">
              <LiveClock zone={shown} />
            </Text>
            .
          </Text>
        ) : null}
        <Text size="sm" c="dimmed">
          {values.timezone ? "Chosen here, so it overrides the default. " : "Using the default. "}
          Default: {defaultNote(info)}
        </Text>
        <Text size="sm">
          The repeater is using {info.effective || "its default zone"}. Its own clock reads{" "}
          <Text span fw={600} className="mor-num">
            {stationClock(info.now)}
          </Text>
          .{dirty ? " That changes when you save." : ""}
        </Text>
        <Text size="sm" c="dimmed">
          Times on this page are in your browser&apos;s time zone. Announcements and schedules use the station&apos;s.
        </Text>
        <ConfigActions dirty={dirty} saving={saving} onReset={reset} />
      </Stack>
    </Card>
  );
}
