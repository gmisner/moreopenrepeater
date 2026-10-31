import {
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, apiBlob } from "../api";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";
import { labeled } from "../components/FieldHint";

type Asset = { id: string; kind: string; filename: string; uploaded_at: string };

type Announcement = {
  id: string;
  name: string;
  message: string;
  asset_id: string | null;
  kind: "interval" | "weekly";
  every_minutes: number;
  times: string[];
  days: number[];
  enabled: boolean;
  next_run: string | null;
};

type FormState = {
  name: string;
  message: string;
  asset_id: string;
  kind: "interval" | "weekly";
  every_minutes: number;
  times: string;
  days: number[];
  enabled: boolean;
};

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MAX_SAYS_LENGTH = 70;

const EMPTY_FORM: FormState = {
  name: "",
  message: "",
  asset_id: "",
  kind: "interval",
  every_minutes: 60,
  times: "",
  days: [0, 1, 2, 3, 4, 5, 6],
  enabled: true,
};

let previewAudio: HTMLAudioElement | null = null;

async function playPreview(clip: string) {
  const blob = await apiBlob("/api/audio/preview", { method: "POST", json: { clip, config: {}, net: false } });
  if (previewAudio) {
    previewAudio.pause();
    URL.revokeObjectURL(previewAudio.src);
  }
  const next = new window.Audio(URL.createObjectURL(blob));
  previewAudio = next;
  await next.play();
}

function describeSchedule(a: Announcement) {
  if (a.kind === "interval") {
    return a.every_minutes % 60 === 0 ? `Every ${a.every_minutes / 60} h` : `Every ${a.every_minutes} min`;
  }
  const days = a.days.length === 7 ? "Daily" : a.days.map((d) => DAY_NAMES[d]).join(", ");
  return `${days} at ${a.times.join(", ")}`;
}

function describeNextRun(a: Announcement) {
  if (!a.enabled) return "paused";
  if (!a.next_run) return "—";
  const when = new Date(a.next_run);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return `Today ${time}`;
  return `${when.toLocaleDateString([], { weekday: "short" })} ${time}`;
}

function describeSays(a: Announcement, assets: Asset[]) {
  if (a.asset_id) {
    const asset = assets.find((item) => item.id === a.asset_id);
    return { kind: "clip" as const, text: asset?.filename ?? "(deleted clip)" };
  }
  const text = a.message.length > MAX_SAYS_LENGTH ? `${a.message.slice(0, MAX_SAYS_LENGTH)}…` : a.message;
  return { kind: "tts" as const, text };
}

function toBody(form: FormState) {
  return {
    name: form.name.trim(),
    message: form.message.trim(),
    asset_id: form.asset_id || null,
    kind: form.kind,
    every_minutes: form.every_minutes || 60,
    times: form.times
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    days: form.days,
    enabled: form.enabled,
  };
}

export function Announcements() {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [playBusy, setPlayBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [nextAnnouncements, nextAssets] = await Promise.all([
      api<Announcement[]>("/api/announcements"),
      api<Asset[]>("/api/assets"),
    ]);
    setAnnouncements(nextAnnouncements);
    setAssets(nextAssets);
  }, []);

  useEffect(() => {
    load().catch(notifyError);
  }, [load]);

  const setField = <K extends keyof FormState>(name: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const stopEdit = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  const startEdit = (a: Announcement) => {
    setEditingId(a.id);
    setForm({
      name: a.name,
      message: a.message,
      asset_id: a.asset_id ?? "",
      kind: a.kind,
      every_minutes: a.every_minutes,
      times: a.times.join(", "),
      days: [...a.days],
      enabled: a.enabled,
    });
  };

  const playNow = async (a: Announcement) => {
    setPlayBusy(a.id);
    try {
      await api(`/api/announcements/${encodeURIComponent(a.id)}/play`, { method: "POST" });
      notifySuccess(`Queued "${a.name}" — it plays as soon as the channel is clear`);
    } catch (error) {
      notifyError(error);
    } finally {
      setPlayBusy(null);
    }
  };

  const remove = async (a: Announcement) => {
    if (!window.confirm(`Delete announcement "${a.name}"?`)) return;
    try {
      await api(`/api/announcements/${encodeURIComponent(a.id)}`, { method: "DELETE" });
      if (editingId === a.id) stopEdit();
      notifySuccess(`Deleted "${a.name}"`);
      await load();
    } catch (error) {
      notifyError(error);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body = toBody(form);
    if (body.kind === "weekly" && body.days.length === 0) {
      notifyError(new Error("Pick at least one day"));
      return;
    }
    setSaving(true);
    try {
      const saved = editingId
        ? await api<Announcement>(`/api/announcements/${encodeURIComponent(editingId)}`, { method: "PUT", json: body })
        : await api<Announcement>("/api/announcements", { method: "POST", json: body });
      notifySuccess(editingId ? `Updated "${saved.name}"` : `Added "${saved.name}"`);
      stopEdit();
      await load();
    } catch (error) {
      notifyError(error);
    } finally {
      setSaving(false);
    }
  };

  const preview = async () => {
    if (!form.asset_id && !form.message.trim()) {
      notifyError(new Error("Type a message to preview"));
      return;
    }
    setPreviewBusy(true);
    try {
      await playPreview(form.asset_id ? `asset:${form.asset_id}` : `tts:${form.message.trim()}`);
    } catch (error) {
      notifyError(error);
    } finally {
      setPreviewBusy(false);
    }
  };

  const rows = [...announcements].sort((a, b) => a.name.localeCompare(b.name));
  const usesClip = Boolean(form.asset_id);

  return (
    <Page
      title="Announcements"
      description="Messages the repeater transmits on a schedule — net reminders, club news. They wait for the channel to be clear and never interrupt a user or the station ID."
    >
      <Card withBorder padding="lg" radius="md">
        <Table.ScrollContainer minWidth={720}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Says</Table.Th>
                <Table.Th>Schedule</Table.Th>
                <Table.Th>Next</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((a) => {
                const says = describeSays(a, assets);
                return (
                  <Table.Tr key={a.id} bg={editingId === a.id ? "var(--mantine-color-default-hover)" : undefined}>
                    <Table.Td>{a.name}</Table.Td>
                    <Table.Td>
                      {says.kind === "clip" ? (
                        <Group gap="xs">
                          <Badge variant="light">clip</Badge>
                          <Text size="sm">{says.text}</Text>
                        </Group>
                      ) : (
                        says.text
                      )}
                    </Table.Td>
                    <Table.Td>{describeSchedule(a)}</Table.Td>
                    <Table.Td>
                      {!a.enabled ? (
                        <Badge variant="light">paused</Badge>
                      ) : (
                        <Text size="sm">{describeNextRun(a)}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" justify="flex-end" wrap="nowrap">
                        <Button
                          size="compact-sm"
                          variant="subtle"
                          loading={playBusy === a.id}
                          title="Transmit now (waits for a clear channel)"
                          onClick={() => void playNow(a)}
                        >
                          Play now
                        </Button>
                        <Button size="compact-sm" variant="subtle" onClick={() => startEdit(a)}>
                          Edit
                        </Button>
                        <Button size="compact-sm" color="red" variant="subtle" onClick={() => void remove(a)}>
                          Delete
                        </Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed" mt="sm">
            No announcements yet.
          </Text>
        ) : null}
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void submit(event)}>
        <Stack gap="md">
          <Title order={2}>{editingId ? `Edit ${form.name || "announcement"}` : "Add announcement"}</Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Name"
              required
              maxLength={100}
              placeholder="Net reminder"
              value={form.name}
              onChange={(event) => setField("name", event.currentTarget.value)}
            />
            <Select
              label={labeled("Audio", "Or play an uploaded clip (Audio & tones → Library).")}
              data={assets.map((asset) => ({ value: asset.id, label: asset.filename }))}
              value={form.asset_id || null}
              onChange={(value) => setField("asset_id", value || "")}
              clearable
              searchable
              placeholder="Speak the message"
            />
            {!usesClip ? (
              <TextInput
                label={labeled("Message", "Spoken with text-to-speech. {callsign} is replaced with your callsign, spelled out.")}
                style={{ gridColumn: "1 / -1" }}
                maxLength={2000}
                placeholder="The {callsign} net meets tonight at 8 pm on this repeater."
                value={form.message}
                onChange={(event) => setField("message", event.currentTarget.value)}
                required
              />
            ) : null}
            <Select
              label="Repeat"
              data={[
                { value: "interval", label: "Every few minutes" },
                { value: "weekly", label: "At set times of day" },
              ]}
              value={form.kind}
              onChange={(value) => setField("kind", (value as FormState["kind"]) || "interval")}
              allowDeselect={false}
            />
            {form.kind === "interval" ? (
              <NumberInput
                label="Every"
                suffix=" min"
                min={1}
                max={10080}
                value={form.every_minutes}
                onChange={(value) => setField("every_minutes", Number(value) || 60)}
              />
            ) : (
              <TextInput
                label={labeled("Times", "24-hour, station local time, comma-separated.")}
                placeholder="08:00, 19:30"
                value={form.times}
                onChange={(event) => setField("times", event.currentTarget.value)}
                required
              />
            )}
          </SimpleGrid>
          {form.kind === "weekly" ? (
            <Checkbox.Group
              label="Days"
              value={form.days.map(String)}
              onChange={(values) => setField("days", values.map(Number).sort((a, b) => a - b))}
            >
              <Group mt="xs">
                {DAY_NAMES.map((label, day) => (
                  <Checkbox key={day} value={String(day)} label={label} />
                ))}
              </Group>
            </Checkbox.Group>
          ) : null}
          <Switch label="Enabled" checked={form.enabled} onChange={(event) => setField("enabled", event.currentTarget.checked)} />
          <Group justify="space-between">
            <Button type="button" variant="subtle" loading={previewBusy} onClick={() => void preview()}>
              Preview
            </Button>
            <Group>
              {editingId ? (
                <Button type="button" variant="subtle" onClick={stopEdit}>
                  Cancel edit
                </Button>
              ) : null}
              <Button type="submit" loading={saving}>
                {editingId ? "Save changes" : "Add announcement"}
              </Button>
            </Group>
          </Group>
        </Stack>
      </Card>
    </Page>
  );
}
