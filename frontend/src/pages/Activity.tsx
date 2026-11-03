import {
  Badge,
  Button,
  Card,
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
import { Fragment, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { labeled } from "../components/FieldHint";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

type UsageSummary = {
  rx_seconds: number;
  tx_seconds: number;
  rx_count: number;
  longest_rx_seconds: number;
  kerchunks: number;
  kerchunks_filtered: number;
  timeouts: number;
  lockouts: number;
  ids: number;
  announcements: number;
  by_hour: number[];
  by_day: { date: string; rx_seconds: number; tx_seconds: number; rx_count?: number }[];
};

type Transmission = { started_at: string; duration: number; timed_out: boolean };
type Recording = {
  id: string;
  started_at: string;
  duration: number;
  transcript: string | null;
  callsigns: string[];
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

function formatAirtime(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Card withBorder padding="md" radius="md">
      <Text size="xs" c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>{label}</Text>
      <Text fw={700} style={{ fontSize: "1.5rem", fontVariantNumeric: "tabular-nums" }}>{value}</Text>
      {note ? <Text size="xs" c="dimmed">{note}</Text> : null}
    </Card>
  );
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

export function Activity() {
  const { config } = useConfig();
  const { session } = useSession();
  const canWrite = session?.role !== "viewer";
  const [days, setDays] = useState("7");
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [recent, setRecent] = useState<Transmission[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [source, setSource] = useState("repeater");
  const [searchInput, setSearchInput] = useState("");
  const [search] = useDebouncedValue(searchInput.trim(), 300);
  const [transcriptionStatus, setTranscriptionStatus] = useState<TranscriptionStatus | null>(null);
  const rec = useConfigSlice<RecValues>(["record_transmissions", "recording_retention_days"], {
    record_transmissions: false,
    recording_retention_days: 30,
  });
  const transcription = useConfigSlice<TranscriptionValues>(TRANSCRIPTION_KEYS, TRANSCRIPTION_DEFAULTS);
  const showMonitorSource = Boolean(config?.monitor_enabled || config?.monitor_record);
  const effectiveSource = showMonitorSource ? source : "repeater";

  const loadActivity = useCallback(async () => {
    try {
      const [nextSummary, nextRecent] = await Promise.all([
        api<UsageSummary>(`/api/activity/summary?days=${days}`),
        api<Transmission[]>("/api/activity/transmissions?limit=25"),
      ]);
      setSummary(nextSummary);
      setRecent(nextRecent);
    } catch (error) {
      notifyError(error);
    }
  }, [days]);

  const loadRecordings = useCallback(async () => {
    const query = search ? `&q=${encodeURIComponent(search)}` : "";
    setRecordings(await api<Recording[]>(`/api/recordings?limit=50&source=${effectiveSource}${query}`));
  }, [effectiveSource, search]);

  const loadTranscription = useCallback(async () => {
    setTranscriptionStatus(await api<TranscriptionStatus>("/api/transcription"));
  }, []);

  useEffect(() => {
    void loadActivity();
    const timer = window.setInterval(() => {
      if (!document.hidden) void loadActivity();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [loadActivity]);

  useEffect(() => {
    loadRecordings().catch(notifyError);
    const timer = window.setInterval(() => {
      if (!document.hidden) loadRecordings().catch(() => {});
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [loadRecordings]);

  useEffect(() => {
    loadTranscription().catch(notifyError);
  }, [loadTranscription]);

  const removeRecording = async (recording: Recording) => {
    if (!window.confirm(`Delete the recording from ${new Date(recording.started_at).toLocaleString()}?`)) return;
    try {
      await api(`/api/recordings/${encodeURIComponent(recording.id)}?source=${effectiveSource}`, { method: "DELETE" });
      notifySuccess("Recording deleted");
      await loadRecordings();
    } catch (error) {
      notifyError(error);
    }
  };

  const s = summary;
  const kerchunkNote = s == null ? "" : `under 1.5 s${s.rx_count ? ` · ${Math.round((100 * s.kerchunks) / s.rx_count)}%` : ""}${s.kerchunks_filtered ? ` · ${s.kerchunks_filtered} more filtered out` : ""}`;

  return (
    <Page title="Activity" description="How much the repeater gets used. Recorded automatically and kept for about a year.">
      <Group justify="flex-end">
        <SegmentedControl value={days} onChange={setDays} data={[
          { label: "24 h", value: "1" },
          { label: "7 days", value: "7" },
          { label: "30 days", value: "30" },
          { label: "90 days", value: "90" },
        ]} />
      </Group>

      {s ? (
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm">
          <Stat label="User airtime" value={formatAirtime(s.rx_seconds)} note={`${s.rx_count} transmissions`} />
          <Stat label="Transmitter on" value={formatAirtime(s.tx_seconds)} note="incl. tails, IDs, announcements" />
          <Stat label="Longest" value={formatAirtime(s.longest_rx_seconds)} note="single transmission" />
          <Stat label="Kerchunks" value={String(s.kerchunks)} note={kerchunkNote} />
          <Stat label="Timeouts" value={String(s.timeouts)} note={s.lockouts ? `${s.lockouts} stuck-carrier lockouts` : "timeout timer tripped"} />
          <Stat label="IDs / announcements" value={`${s.ids} / ${s.announcements}`} />
        </SimpleGrid>
      ) : null}

      <Card withBorder padding="lg" radius="md">
        <Text component="h2" size="xs" c="dimmed" tt="uppercase" fw={600} mb="sm" style={{ letterSpacing: "0.06em" }}>Recent transmissions</Text>
        {recent.length ? (
          <Table striped highlightOnHover>
            <Table.Thead><Table.Tr><Table.Th>When</Table.Th><Table.Th>Length</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>
              {recent.map((row) => (
                <Table.Tr key={row.started_at}>
                  <Table.Td>{new Date(row.started_at).toLocaleString()}</Table.Td>
                  <Table.Td>{formatAirtime(row.duration)}</Table.Td>
                  <Table.Td>{row.timed_out ? <Badge color="red" variant="light">timed out</Badge> : row.duration < 1.5 ? <Badge variant="outline">kerchunk</Badge> : null}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : <Text c="dimmed" size="sm">No transmissions recorded yet. Try the Simulator.</Text>}
      </Card>

      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Title order={2}>Recordings</Title>
          <Stack component="form" gap="md" onSubmit={(event) => { event.preventDefault(); void rec.submit(); }}>
            <Switch label={labeled("Record transmissions", "Saves what the repeater hears (needs live audio)")} checked={rec.values.record_transmissions} onChange={(event) => rec.setField("record_transmissions", event.currentTarget.checked)} />
            <NumberInput
              label={labeled("Keep recordings for", "Older recordings are deleted automatically. Parrot playbacks are kept here too.")}
              suffix=" days" min={1} max={365} maw={280} value={rec.values.recording_retention_days}
              onChange={(value) => rec.setField("recording_retention_days", Number(value) || 1)} required
            />
            <ConfigActions dirty={rec.dirty} saving={rec.saving} onReset={rec.reset} />
          </Stack>
          <Group justify="space-between" align="flex-end" wrap="wrap">
            {showMonitorSource ? (
              <SegmentedControl value={effectiveSource} onChange={setSource} data={[
                { label: "Repeater", value: "repeater" },
                { label: "Monitor receiver", value: "monitor" },
              ]} />
            ) : <span />}
            <TextInput
              label="Search the transcripts" type="search" maxLength={100} placeholder="A word, or a callsign like W1AW"
              value={searchInput} onChange={(event) => setSearchInput(event.currentTarget.value)}
              style={{ flex: "1 1 280px", maxWidth: 440 }}
            />
          </Group>
          <Table.ScrollContainer minWidth={680}>
            <Table striped highlightOnHover>
              <Table.Thead><Table.Tr><Table.Th>When</Table.Th><Table.Th>Length</Table.Th><Table.Th>Listen</Table.Th><Table.Th /></Table.Tr></Table.Thead>
              <Table.Tbody>
                {recordings.map((recording) => (
                  <Fragment key={recording.id}>
                    <Table.Tr>
                      <Table.Td>{new Date(recording.started_at).toLocaleString()}</Table.Td>
                      <Table.Td>{formatAirtime(recording.duration)}</Table.Td>
                      <Table.Td><audio controls preload="none" src={`/api/recordings/${encodeURIComponent(recording.id)}/audio?source=${effectiveSource}`} /></Table.Td>
                      <Table.Td>
                        {canWrite ? <Button size="compact-sm" color="red" variant="subtle" onClick={() => void removeRecording(recording)}>Delete</Button> : null}
                      </Table.Td>
                    </Table.Tr>
                    {recording.transcript != null ? (
                      <Table.Tr>
                        <Table.Td colSpan={4}>
                          <Group gap="xs">
                            {recording.callsigns.map((callsign) => <Badge key={callsign} variant="light">{callsign}</Badge>)}
                            <Text size="sm" c={recording.transcript ? undefined : "dimmed"}>{recording.transcript || "Nothing understood."}</Text>
                          </Group>
                        </Table.Td>
                      </Table.Tr>
                    ) : null}
                  </Fragment>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {recordings.length === 0 ? <Text c="dimmed" size="sm">{search ? "No transcripts match." : "No recordings yet."}</Text> : null}
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => { event.preventDefault(); void transcription.submit().then(loadTranscription); }}>
        <Stack gap="md">
          <Title order={2}>Transcripts</Title>
          <Text c="dimmed" size="sm" maw="75ch">Turn recordings and mailbox messages into searchable text, with callsigns picked out. Recordings need to be turned on above.</Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Transcribe with" data={[
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
                  placeholder="http://whisper.local:8000/v1/audio/transcriptions" pattern="https?://\S+"
                  value={transcription.values.transcription_url}
                  onChange={(event) => transcription.setField("transcription_url", event.currentTarget.value)}
                />
                <TextInput
                  label="Model" maxLength={100} placeholder="whisper-1" value={transcription.values.transcription_model}
                  onChange={(event) => transcription.setField("transcription_model", event.currentTarget.value)} required
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
