import { Alert, Anchor, Badge, Button, Card, Divider, Group, Stack, Text, TextInput, Title } from "@mantine/core";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../../api";
import { notifySuccess } from "../../config";
import { labeled } from "../../components/FieldHint";
import { ErrorCard, LoadingBlock } from "../../components/StateCards";
import { useSession } from "../../session";
import { durationWords, offsetSentence, type TimeContainer, type TimeState, type TimeStatus } from "./ntp";

/** How often the check is read again while the page is open and visible. The server reuses a check for about a minute. */
const REFRESH_MS = 45_000;

const DOCS_URL = "https://github.com/gmisner/moreopenrepeater/blob/main/docs/docker.md#the-time-container";

const BADGES: Record<TimeState, { color: string; label: string }> = {
  ok: { color: "green", label: "Good" },
  off: { color: "red", label: "Off" },
  unreachable: { color: "yellow", label: "Unreachable" },
  not_checked: { color: "gray", label: "Not checked" },
};

function ClockCheck({ status }: { status: TimeStatus }) {
  const { state, server, offset_ms: offset } = status;
  const badge = BADGES[state];
  let headline: string;
  if ((state === "ok" || state === "off") && offset !== null) {
    headline = `${offsetSentence(offset, server)}.`;
  } else if (state === "unreachable") {
    headline = `Couldn't get a time from ${server}.`;
  } else if (state === "not_checked") {
    headline = "The clock hasn't been checked yet.";
  } else {
    headline = `${server} answered, but no clock reading came back.`;
  }
  const facts: string[] = [];
  if (status.round_trip_ms !== null) facts.push(`Round trip ${durationWords(status.round_trip_ms)}`);
  if (status.stratum !== null) facts.push(`server stratum ${status.stratum}`);

  return (
    <Stack gap="xs" aria-label="Clock check" role="group">
      <Group gap="sm" align="center">
        <Badge color={badge.color} variant="light" size="lg" aria-label={`Clock check: ${badge.label}`}>
          {badge.label}
        </Badge>
        <Text fw={600} className="mor-num">
          {headline}
        </Text>
      </Group>
      {facts.length ? (
        <Text size="sm" c="dimmed" className="mor-num">
          {`${facts.join(", ")}.`}
        </Text>
      ) : null}
      {state === "unreachable" && status.error_text ? <Text size="sm">{status.error_text}</Text> : null}
      {state === "unreachable" ? (
        <Text size="sm" c="dimmed">
          That says nothing about whether the Pi&apos;s clock is right.
        </Text>
      ) : null}
      {state === "off" ? (
        <Alert color="red" variant="light" title="A clock this far off causes trouble">
          Being off by {durationWords(status.limit_ms)} or more throws off the station ID times, the spoken time,
          scheduled announcements and nets, and the times in the logs and the activity history.
        </Alert>
      ) : null}
      <Text size="sm" c="dimmed">
        The check asks {server} what time it is, over the network, and sends nothing else.
      </Text>
    </Stack>
  );
}

function ContainerSection({ status }: { status: TimeStatus }) {
  const box: TimeContainer = status.container;
  if (!box.running) {
    return (
      <Stack gap="xs" aria-label="Time container" role="group">
        <Group gap="sm">
          <Title order={3} size="h4">
            Time container
          </Title>
          <Badge color="gray" variant="light">
            Not running
          </Badge>
        </Group>
        <Text size="sm">
          This is an optional program (chrony) in its own container. It sets the Pi&apos;s clock from the server above. Without it, the
          check only reports and changes nothing.
        </Text>
        <Text size="sm">
          To start it, run{" "}
          <Text span ff="monospace" fw={600}>
            ./setup.sh --time
          </Text>{" "}
          in the docker folder. This card then shows it.
        </Text>
        <Text size="sm" c="dimmed">
          It needs a capability (CAP_SYS_TIME) that lets it set the Pi&apos;s clock, which is why it is off unless you ask for it. See{" "}
          <Anchor href={DOCS_URL} target="_blank" rel="noreferrer">
            &quot;The time container&quot; in docs/docker.md
          </Anchor>
          .
        </Text>
      </Stack>
    );
  }

  const following = !box.config_error && box.server !== null && box.server !== status.server;
  let syncText: string;
  if (box.synced === true) {
    const parts = [`Synced to ${box.server ?? status.server}`];
    if (box.stratum !== null) parts.push(`stratum ${box.stratum}`);
    if (box.offset_ms !== null) parts.push(`off by ${durationWords(box.offset_ms)}`);
    syncText = `${parts.join(", ")}.`;
  } else if (box.synced === false) {
    syncText = `Running, but it hasn't got a time from ${box.server ?? status.server} yet.`;
  } else {
    syncText = "It hasn't said whether it is in sync.";
  }

  return (
    <Stack gap="xs" aria-label="Time container" role="group">
      <Group gap="sm">
        <Title order={3} size="h4">
          Time container
        </Title>
        <Badge color="green" variant="light">
          Running
        </Badge>
        {box.synced === true ? (
          <Badge color="green" variant="light">
            Synced
          </Badge>
        ) : box.synced === false ? (
          <Badge color="yellow" variant="light">
            Not synced yet
          </Badge>
        ) : null}
      </Group>
      <Text size="sm" className="mor-num">
        {syncText}
      </Text>
      {box.config_error ? (
        <Alert color="red" variant="light" title="The time container refused the server setting">
          {box.config_error} It keeps using {box.server ?? status.default_server} until the setting is fixed.
        </Alert>
      ) : null}
      {following ? (
        <Text size="sm" c="dimmed">
          The container is still using {box.server}. It can take up to a minute to follow a change.
        </Text>
      ) : null}
    </Stack>
  );
}

/** The NTP server, and whether the Pi's clock agrees with it. A section of the Date and time page: it saves only its own setting. */
export function NtpCard() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const [status, setStatus] = useState<TimeStatus | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Only the newest answer counts: a slow read that started before a save must not undo what the save returned.
  const latest = useRef(0);

  const load = useCallback(() => {
    const ticket = ++latest.current;
    return api<TimeStatus>("/api/time")
      .then((next) => {
        if (ticket !== latest.current) return;
        setStatus(next);
        setFailed(null);
      })
      .catch((error: unknown) => {
        if (ticket !== latest.current) return;
        setFailed(error instanceof Error ? error.message : String(error));
      });
  }, []);

  useEffect(() => {
    void load();
    const again = setInterval(() => {
      if (!document.hidden) void load();
    }, REFRESH_MS);
    const back = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", back);
    return () => {
      clearInterval(again);
      document.removeEventListener("visibilitychange", back);
    };
  }, [load]);

  if (failed && !status) {
    return (
      <ErrorCard
        title="Couldn't check the clock"
        onRetry={() => {
          setFailed(null);
          void load();
        }}
      >
        {failed}
      </ErrorCard>
    );
  }
  if (!status) return <LoadingBlock label="Loading the clock check" />;

  const value = draft ?? status.configured_server;
  const dirty = draft !== null && draft.trim() !== status.configured_server;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!dirty || saving) return;
    setSaving(true);
    setSaveError(null);
    latest.current += 1;
    try {
      const next = await api<TimeStatus>("/api/time/ntp", { method: "PUT", json: { server: value.trim() } });
      setStatus(next);
      setFailed(null);
      setDraft(null);
      notifySuccess("Saved");
    } catch (error) {
      // The server's own words, with what was typed kept so it can be fixed.
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event: FormEvent) => void save(event)}>
      <Stack gap="md">
        <Title order={2}>Time server</Title>
        {isAdmin ? (
          <Group align="flex-start" gap="sm" wrap="wrap">
            <TextInput
              label={labeled(
                "NTP server",
                `A host name or an IP address of a time server. Leave it empty to use the default, ${status.default_server}.`,
              )}
              style={{ flex: "1 1 260px" }}
              maw={420}
              value={value}
              placeholder={status.default_server}
              error={saveError}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              onChange={(event) => {
                setDraft(event.currentTarget.value);
                setSaveError(null);
              }}
            />
            <Button type="submit" mt={25} loading={saving} disabled={!dirty} variant={dirty ? "filled" : "default"}>
              Save
            </Button>
          </Group>
        ) : (
          <Stack gap={2}>
            <Text size="sm">
              Time server:{" "}
              <Text span fw={600} className="mor-num">
                {status.server}
              </Text>
              {status.configured_server ? "" : " (the default)"}
            </Text>
            <Text size="sm" c="dimmed">
              Only an admin can change the time server.
            </Text>
          </Stack>
        )}
        {isAdmin ? (
          <Text size="sm" c="dimmed">
            {status.configured_server
              ? `Using ${status.configured_server}. Empty goes back to the default, ${status.default_server}.`
              : `Using the default, ${status.default_server}.`}
          </Text>
        ) : null}
        <Divider />
        <ClockCheck status={status} />
        <Divider />
        <ContainerSection status={status} />
        {failed ? (
          <Text size="sm" c="dimmed">
            Couldn&apos;t refresh just now, so this is the last reading. {failed}
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}
