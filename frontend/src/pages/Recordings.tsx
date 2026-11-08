import {
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { labeled } from "../components/FieldHint";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";
import { Stat } from "../components/Stat";
import { formatAirtime } from "../format";
import { ActionsHeader } from "../components/ActionsHeader";

type Recording = {
  id: string;
  started_at: string;
  duration: number;
  transcript: string | null;
  callsigns: string[];
};

type RecordingSummary = {
  count: number;
  total_duration: number;
  transcribed: number;
};

type TranscriptionStatus = {
  engine: string;
  vosk_installed: boolean;
  vosk_model: boolean;
  vosk_model_dir: string;
  api_key_set: boolean;
  pending: number;
  transcribed: number;
  last_error: string | null;
};

type RecValues = { record_transmissions: boolean; recording_retention_days: number };
type TranscriptionValues = {
  transcription_engine: string;
  transcription_url: string;
  transcription_model: string;
};

const PAGE_SIZE = 50;
const TRANSCRIPTION_KEYS = [
  "transcription_engine",
  "transcription_url",
  "transcription_model",
] as (keyof TranscriptionValues)[];
const TRANSCRIPTION_DEFAULTS: TranscriptionValues = {
  transcription_engine: "off",
  transcription_url: "https://api.openai.com/v1/audio/transcriptions",
  transcription_model: "whisper-1",
};

function dayStartIso(date: string) {
  return new Date(`${date}T00:00:00`).toISOString();
}

function dayAfterIso(date: string) {
  const next = new Date(`${date}T00:00:00`);
  next.setDate(next.getDate() + 1);
  return next.toISOString();
}

function isOpenAiHost(url: string) {
  try {
    return new URL(url).hostname.toLowerCase() === "api.openai.com";
  } catch {
    return false;
  }
}

function TranscriptionNote({ status, url }: { status: TranscriptionStatus | null; url: string }) {
  if (!status || status.engine === "off") return null;
  if (status.engine === "vosk" && !status.vosk_installed) {
    return (
      <Text c="dimmed" size="sm">
        Vosk is not installed. On the controller, run <Code>sudo /opt/moreopenrepeater/.venv/bin/pip install vosk</Code> and restart.
      </Text>
    );
  }
  if (status.engine === "vosk" && !status.vosk_model) {
    return <Text c="dimmed" size="sm">No Vosk model yet. Unpack a model into <Code>{status.vosk_model_dir}</Code>.</Text>;
  }
  if (status.engine === "openai" && !status.api_key_set && isOpenAiHost(url)) {
    return (
      <Text c="dimmed" size="sm">
        OpenAI needs an API key in <Code>MOREOPENREPEATER_TRANSCRIPTION_API_KEY</Code> in <Code>/etc/moreopenrepeater/env</Code>, followed by a restart.
      </Text>
    );
  }
  const parts = [
    status.last_error ? `Last try failed: ${status.last_error}.` : "",
    status.pending ? `${status.pending} waiting to be transcribed.` : "All caught up.",
    status.api_key_set && status.engine === "openai" ? "An API key is set." : "",
  ].filter(Boolean);
  return <Text c={status.last_error ? "red" : "dimmed"} size="sm">{parts.join(" ")}</Text>;
}

export function Recordings() {
  const { config } = useConfig();
  const { session } = useSession();
  const canWrite = session?.role !== "viewer";
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [summary, setSummary] = useState<RecordingSummary | null>(null);
  const [source, setSource] = useState("repeater");
  const [searchInput, setSearchInput] = useState("");
  const [callsignInput, setCallsignInput] = useState("");
  const [afterDate, setAfterDate] = useState("");
  const [beforeDate, setBeforeDate] = useState("");
  const [sort, setSort] = useState("newest");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [deleting, setDeleting] = useState(false);
  const [search] = useDebouncedValue(searchInput.trim(), 300);
  const [callsign] = useDebouncedValue(callsignInput.trim().toUpperCase(), 300);
  const [transcriptionStatus, setTranscriptionStatus] = useState<TranscriptionStatus | null>(null);
  const rec = useConfigSlice<RecValues>(["record_transmissions", "recording_retention_days"], {
    record_transmissions: false,
    recording_retention_days: 30,
  });
  const transcription = useConfigSlice<TranscriptionValues>(TRANSCRIPTION_KEYS, TRANSCRIPTION_DEFAULTS);
  const showMonitorSource = Boolean(config?.monitor_enabled || config?.monitor_record);
  const effectiveSource = showMonitorSource ? source : "repeater";

  const filterQuery = useMemo(() => {
    const params = new URLSearchParams({ source: effectiveSource, sort });
    if (search) params.set("q", search);
    if (callsign) params.set("callsign", callsign);
    if (afterDate) params.set("after", dayStartIso(afterDate));
    if (beforeDate) params.set("before", dayAfterIso(beforeDate));
    return params;
  }, [afterDate, beforeDate, callsign, effectiveSource, search, sort]);

  const loadSummary = useCallback(async () => {
    setSummary(await api<RecordingSummary>(`/api/recordings/summary?${filterQuery}`));
  }, [filterQuery]);

  const loadRecordings = useCallback(async (nextOffset: number, append: boolean) => {
    const params = new URLSearchParams(filterQuery);
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(nextOffset));
    const rows = await api<Recording[]>(`/api/recordings?${params}`);
    setRecordings((current) => (append ? [...current, ...rows] : rows));
    setOffset(nextOffset);
    if (!append) setSelected(new Set());
  }, [filterQuery]);

  const loadTranscription = useCallback(async () => {
    setTranscriptionStatus(await api<TranscriptionStatus>("/api/transcription"));
  }, []);

  useEffect(() => {
    void loadSummary().catch(notifyError);
    void loadRecordings(0, false).catch(notifyError);
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      loadSummary().catch(() => {});
      loadRecordings(0, false).catch(() => {});
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [loadRecordings, loadSummary]);

  useEffect(() => {
    loadTranscription().catch(notifyError);
  }, [loadTranscription]);

  const removeRecording = async (recording: Recording) => {
    if (!window.confirm(`Delete the recording from ${new Date(recording.started_at).toLocaleString()}?`)) return;
    try {
      await api(`/api/recordings/${encodeURIComponent(recording.id)}?source=${effectiveSource}`, { method: "DELETE" });
      notifySuccess("Recording deleted");
      await Promise.all([loadSummary(), loadRecordings(0, false)]);
    } catch (error) {
      notifyError(error);
    }
  };

  const removeSelected = async () => {
    const ids = [...selected];
    if (!ids.length) return;
    if (!window.confirm(`Delete ${ids.length} recording${ids.length === 1 ? "" : "s"}?`)) return;
    setDeleting(true);
    try {
      for (const id of ids) {
        await api(`/api/recordings/${encodeURIComponent(id)}?source=${effectiveSource}`, { method: "DELETE" });
      }
      notifySuccess(ids.length === 1 ? "Recording deleted" : `${ids.length} recordings deleted`);
      await Promise.all([loadSummary(), loadRecordings(0, false)]);
    } catch (error) {
      notifyError(error);
    } finally {
      setDeleting(false);
    }
  };

  const allVisibleSelected = recordings.length > 0 && recordings.every((row) => selected.has(row.id));
  const hasMore = summary != null && recordings.length < summary.count;
  const filtersActive = Boolean(search || callsign || afterDate || beforeDate);

  return (
    <Page title="Recordings">
      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => { event.preventDefault(); void rec.submit(); }}>
        <Stack gap="md">
          <Title order={2}>Save transmissions</Title>
          <Switch
            label={labeled("Record transmissions", "Saves what the repeater hears (needs live audio)")}
            checked={rec.values.record_transmissions}
            onChange={(event) => rec.setField("record_transmissions", event.currentTarget.checked)}
          />
          <NumberInput
            label={labeled("Keep recordings for", "Older recordings are deleted automatically. Parrot playbacks are kept here too.")}
            suffix=" days"
            min={1}
            max={365}
            maw={280}
            value={rec.values.recording_retention_days}
            onChange={(value) => rec.setField("recording_retention_days", Number(value) || 1)}
            required
          />
          <ConfigActions dirty={rec.dirty} saving={rec.saving} onReset={rec.reset} />
        </Stack>
      </Card>

      <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">
        <Stat
          label="Recordings"
          value={summary ? String(summary.count) : "—"}
          note={filtersActive ? "matching the filters" : "on this source"}
        />
        <Stat
          label="Airtime"
          value={summary ? formatAirtime(summary.total_duration) : "—"}
          note="total length of those clips"
        />
        <Stat
          label="Transcribed"
          value={summary ? String(summary.transcribed) : "—"}
          note={summary && summary.count ? `${Math.round((100 * summary.transcribed) / summary.count)}% of the list` : "with text so far"}
        />
      </SimpleGrid>

      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Group justify="space-between" align="flex-end" wrap="wrap">
            <Title order={2}>Library</Title>
            {canWrite && selected.size > 0 ? (
              <Button color="red" variant="light" loading={deleting} onClick={() => void removeSelected()}>
                Delete selected ({selected.size})
              </Button>
            ) : null}
          </Group>

          <Group align="flex-end" wrap="wrap" gap="md">
            {showMonitorSource ? (
              <SegmentedControl
                value={effectiveSource}
                onChange={setSource}
                data={[
                  { label: "Repeater", value: "repeater" },
                  { label: "Monitor receiver", value: "monitor" },
                ]}
              />
            ) : null}
            <TextInput
              label="From"
              type="date"
              value={afterDate}
              onChange={(event) => setAfterDate(event.currentTarget.value)}
            />
            <TextInput
              label="Through"
              type="date"
              value={beforeDate}
              onChange={(event) => setBeforeDate(event.currentTarget.value)}
            />
            <TextInput
              label="Callsign"
              maxLength={20}
              placeholder="e.g. W1AW"
              value={callsignInput}
              onChange={(event) => setCallsignInput(event.currentTarget.value.toUpperCase())}
              style={{ flex: "0 1 140px" }}
            />
            <TextInput
              label="Search the transcripts"
              type="search"
              maxLength={100}
              placeholder=""
              value={searchInput}
              onChange={(event) => setSearchInput(event.currentTarget.value)}
              style={{ flex: "1 1 240px", maxWidth: 440 }}
            />
            <Select
              label={labeled(
                "Sort by",
                "Callsigns come from the transcript when someone says one. Many clips won't have any — those sort last, and without a radio serial hookup there's no other ID source.",
              )}
              data={[
                { value: "newest", label: "Newest first" },
                { value: "oldest", label: "Oldest first" },
                { value: "callsign", label: "Callsign A–Z" },
                { value: "callsign_desc", label: "Callsign Z–A" },
              ]}
              value={sort}
              onChange={(value) => setSort(value || "newest")}
              allowDeselect={false}
              maw={220}
            />
            {filtersActive ? (
              <Button
                variant="subtle"
                onClick={() => {
                  setSearchInput("");
                  setCallsignInput("");
                  setAfterDate("");
                  setBeforeDate("");
                }}
              >
                Clear filters
              </Button>
            ) : null}
          </Group>

          <Table.ScrollContainer minWidth={780}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  {canWrite ? (
                    <Table.Th w={40}>
                      <Checkbox
                        aria-label="Select all on this page"
                        checked={allVisibleSelected}
                        indeterminate={selected.size > 0 && !allVisibleSelected}
                        onChange={(event) => {
                          if (event.currentTarget.checked) {
                            setSelected(new Set(recordings.map((row) => row.id)));
                          } else {
                            setSelected(new Set());
                          }
                        }}
                      />
                    </Table.Th>
                  ) : null}
                  <Table.Th>When</Table.Th>
                  <Table.Th>Callsign</Table.Th>
                  <Table.Th>Length</Table.Th>
                  <Table.Th>Listen</Table.Th>
                  <ActionsHeader />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {recordings.map((recording) => (
                  <Fragment key={recording.id}>
                    <Table.Tr>
                      {canWrite ? (
                        <Table.Td>
                          <Checkbox
                            aria-label={`Select recording from ${new Date(recording.started_at).toLocaleString()}`}
                            checked={selected.has(recording.id)}
                            onChange={(event) => {
                              setSelected((current) => {
                                const next = new Set(current);
                                if (event.currentTarget.checked) next.add(recording.id);
                                else next.delete(recording.id);
                                return next;
                              });
                            }}
                          />
                        </Table.Td>
                      ) : null}
                      <Table.Td>{new Date(recording.started_at).toLocaleString()}</Table.Td>
                      <Table.Td>
                        {recording.callsigns.length ? (
                          <Group gap="xs">
                            {recording.callsigns.map((value) => (
                              <Badge
                                key={value}
                                variant="light"
                                style={{ cursor: "pointer" }}
                                onClick={() => setCallsignInput(value)}
                              >
                                {value}
                              </Badge>
                            ))}
                          </Group>
                        ) : (
                          <Text size="sm" c="dimmed">—</Text>
                        )}
                      </Table.Td>
                      <Table.Td>{formatAirtime(recording.duration)}</Table.Td>
                      <Table.Td>
                        <audio
                          controls
                          preload="none"
                          src={`/api/recordings/${encodeURIComponent(recording.id)}/audio?source=${effectiveSource}`}
                        />
                      </Table.Td>
                      <Table.Td>
                        {canWrite ? (
                          <Button size="compact-sm" color="red" variant="subtle" onClick={() => void removeRecording(recording)}>
                            Delete
                          </Button>
                        ) : null}
                      </Table.Td>
                    </Table.Tr>
                    {recording.transcript != null ? (
                      <Table.Tr>
                        <Table.Td colSpan={canWrite ? 6 : 5}>
                          <Text size="sm" c={recording.transcript ? undefined : "dimmed"}>
                            {recording.transcript || "Nothing understood."}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : null}
                  </Fragment>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>

          {recordings.length === 0 ? (
            <Text c="dimmed" size="sm">
              {filtersActive ? "No recordings match these filters." : "No recordings yet."}
            </Text>
          ) : null}

          {hasMore ? (
            <Group justify="center">
              <Button
                variant="light"
                onClick={() => void loadRecordings(offset + PAGE_SIZE, true).catch(notifyError)}
              >
                Load more ({recordings.length} of {summary?.count ?? "—"})
              </Button>
            </Group>
          ) : null}
        </Stack>
      </Card>

      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void transcription.submit().then(loadTranscription);
        }}
      >
        <Stack gap="md">
          <Title order={2}>{labeled("Transcripts", <>Turn recordings and mailbox messages into searchable text, with callsigns picked out. Recordings need to be turned on above.</>)}</Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Transcribe with"
              data={[
                { value: "off", label: "Off" },
                { value: "vosk", label: "Vosk, on this controller" },
                { value: "openai", label: "A Whisper server (OpenAI-compatible)" },
              ]}
              value={transcription.values.transcription_engine}
              onChange={(value) => transcription.setField("transcription_engine", value || "off")}
              allowDeselect={false}
            />
            {transcription.values.transcription_engine === "openai" ? (
              <>
                <TextInput
                  label={labeled("Server address", "The full /v1/audio/transcriptions address.")}
                  placeholder="e.g. http://…"
                  pattern="https?://\S+"
                  value={transcription.values.transcription_url}
                  onChange={(event) => transcription.setField("transcription_url", event.currentTarget.value)}
                />
                <TextInput
                  label="Model"
                  maxLength={100}
                  placeholder="e.g. whisper-1"
                  value={transcription.values.transcription_model}
                  onChange={(event) => transcription.setField("transcription_model", event.currentTarget.value)}
                  required
                />
              </>
            ) : null}
          </SimpleGrid>
          <TranscriptionNote status={transcriptionStatus} url={transcription.values.transcription_url} />
          <ConfigActions dirty={transcription.dirty} saving={transcription.saving} onReset={transcription.reset} />
        </Stack>
      </Card>
    </Page>
  );
}
