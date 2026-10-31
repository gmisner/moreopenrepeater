import {
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, notifySuccess, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const STATUS_POLL_MS = 3000;

const STATE_LABELS: Record<string, string> = {
  off: "Off",
  connecting: "Connecting",
  streaming: "Streaming",
  retrying: "Retrying",
  error: "Problem",
};

const STATE_COLORS: Record<string, string> = {
  streaming: "green",
  retrying: "amber",
  error: "red",
};

type PublicValues = {
  public_page_mode: string;
  public_page_text: string;
  public_page_audio: boolean;
  public_page_max_listeners: number;
};

type StreamStatus = { state: string; detail: string; since: number };

type StreamSettings = {
  enabled: boolean;
  host: string;
  port: number;
  mount: string;
  username: string;
  password_set: boolean;
  name: string;
  description: string;
  genre: string;
  bitrate: number;
  legacy_icecast: boolean;
  status: StreamStatus;
};

type StreamForm = {
  enabled: boolean;
  host: string;
  port: number;
  mount: string;
  username: string;
  password: string;
  name: string;
  description: string;
  genre: string;
  bitrate: string;
  legacy_icecast: boolean;
};

const PUBLIC_KEYS = [
  "public_page_mode",
  "public_page_text",
  "public_page_audio",
  "public_page_max_listeners",
] as (keyof PublicValues)[];

const PUBLIC_DEFAULTS: PublicValues = {
  public_page_mode: "off",
  public_page_text: "",
  public_page_audio: true,
  public_page_max_listeners: 20,
};

function emptyStreamForm(): StreamForm {
  return {
    enabled: false,
    host: "",
    port: 80,
    mount: "",
    username: "source",
    password: "",
    name: "",
    description: "",
    genre: "",
    bitrate: "16",
    legacy_icecast: false,
  };
}

function streamToForm(stream: StreamSettings): StreamForm {
  return {
    enabled: stream.enabled,
    host: stream.host,
    port: stream.port,
    mount: stream.mount,
    username: stream.username,
    password: "",
    name: stream.name,
    description: stream.description,
    genre: stream.genre,
    bitrate: String(stream.bitrate),
    legacy_icecast: stream.legacy_icecast,
  };
}

export function PublicListening() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const page = useConfigSlice<PublicValues>(PUBLIC_KEYS, PUBLIC_DEFAULTS);

  const [streamForm, setStreamForm] = useState<StreamForm>(emptyStreamForm());
  const [streamStatus, setStreamStatus] = useState<StreamStatus | null>(null);
  const [passwordSet, setPasswordSet] = useState(false);
  const [streamSaving, setStreamSaving] = useState(false);

  const loadStream = useCallback(async () => {
    if (!isAdmin) return;
    const stream = await api<StreamSettings>("/api/stream");
    setStreamForm(streamToForm(stream));
    setPasswordSet(stream.password_set);
    setStreamStatus(stream.status);
  }, [isAdmin]);

  const refreshStatus = useCallback(async () => {
    if (!isAdmin) return;
    const stream = await api<StreamSettings>("/api/stream");
    setStreamStatus(stream.status);
  }, [isAdmin]);

  useEffect(() => {
    loadStream().catch(notifyError);
  }, [loadStream]);

  useEffect(() => {
    if (!isAdmin) return;
    const id = window.setInterval(() => {
      if (!document.hidden) refreshStatus().catch(() => {});
    }, STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [isAdmin, refreshStatus]);

  const setStreamField = <K extends keyof StreamForm>(name: K, value: StreamForm[K]) => {
    setStreamForm((prev) => ({ ...prev, [name]: value }));
  };

  const saveStream = async (event: FormEvent) => {
    event.preventDefault();
    setStreamSaving(true);
    try {
      const body: Record<string, unknown> = {
        host: streamForm.host.trim(),
        port: Number(streamForm.port),
        mount: streamForm.mount.trim(),
        username: streamForm.username.trim(),
        name: streamForm.name.trim(),
        description: streamForm.description.trim(),
        genre: streamForm.genre.trim(),
        bitrate: Number(streamForm.bitrate),
        enabled: streamForm.enabled,
        legacy_icecast: streamForm.legacy_icecast,
      };
      if (streamForm.password) body.password = streamForm.password;
      const stream = await api<StreamSettings>("/api/stream", { method: "PUT", json: body });
      setStreamForm(streamToForm(stream));
      setPasswordSet(stream.password_set);
      setStreamStatus(stream.status);
      notifySuccess("Saved the feed settings");
    } catch (error) {
      notifyError(error);
    } finally {
      setStreamSaving(false);
    }
  };

  const listenUrl = `${window.location.origin}/listen`;

  return (
    <Page
      title="Public listening"
      description="Let people hear the repeater without the dashboard: a listening page of its own, a Broadcastify feed, or both."
    >
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void page.submit();
        }}
      >
        <Stack gap="md">
          <Select
            label="Listening page"
            description='"Signed-in accounts only" asks for a sign-in first. Any account works, and a Listener account (added on the Users page) gets this page and nothing else.'
            data={[
              { value: "off", label: "Off" },
              { value: "signed_in", label: "Signed-in accounts only" },
              { value: "anyone", label: "Anyone, without signing in" },
            ]}
            value={page.values.public_page_mode}
            onChange={(value) => page.setField("public_page_mode", value || "off")}
            allowDeselect={false}
          />
          {page.values.public_page_mode !== "off" ? (
            <Text size="sm">
              The page is at{" "}
              <Anchor href="/listen" target="_blank" rel="noopener">
                {listenUrl}
              </Anchor>
              .
            </Text>
          ) : null}
          <TextInput
            label="Text on the page"
            description="Shown under the callsign. The page also shows whether the repeater is on the air and whether a net is in session."
            maxLength={500}
            placeholder="146.940 MHz, -0.6 MHz, 100.0 Hz tone"
            value={page.values.public_page_text}
            onChange={(event) => page.setField("public_page_text", event.currentTarget.value)}
          />
          <Switch
            label="Visitors can listen live"
            description="Only what's transmitted"
            checked={page.values.public_page_audio}
            onChange={(event) => page.setField("public_page_audio", event.currentTarget.checked)}
          />
          <NumberInput
            label="Listeners at once"
            description="Each listener takes about 256 kbps of this controller's upload. Without a sign-in, at most 3 from one address."
            min={1}
            max={200}
            required
            maw={240}
            value={page.values.public_page_max_listeners}
            onChange={(value) => page.setField("public_page_max_listeners", Number(value) || 0)}
          />
          <ConfigActions dirty={page.dirty} saving={page.saving} onReset={page.reset} />
        </Stack>
      </Card>

      {isAdmin ? (
        <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => void saveStream(event)}>
          <Stack gap="md">
            <Title order={2}>Broadcastify / Icecast feed</Title>
            <Text c="dimmed" size="sm" maw="75ch">
              Sends what&apos;s on the air to an Icecast server as a 16 kbps MP3 stream, the format Broadcastify asks for.
              Needs ffmpeg (<Code>sudo apt install ffmpeg</Code>).
            </Text>
            <Group gap="sm">
              <Badge color={STATE_COLORS[streamStatus?.state ?? ""] ?? "gray"} variant="light">
                {STATE_LABELS[streamStatus?.state ?? ""] ?? streamStatus?.state ?? "off"}
              </Badge>
              <Text c="dimmed" size="sm">
                {streamStatus?.detail}
              </Text>
            </Group>
            <Switch
              label="Stream"
              checked={streamForm.enabled}
              onChange={(event) => setStreamField("enabled", event.currentTarget.checked)}
            />
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <TextInput
                label="Server"
                placeholder="audio9.broadcastify.com"
                value={streamForm.host}
                onChange={(event) => setStreamField("host", event.currentTarget.value)}
              />
              <NumberInput
                label="Port"
                min={1}
                max={65535}
                required
                value={streamForm.port}
                onChange={(value) => setStreamField("port", Number(value) || 80)}
              />
              <TextInput
                label="Mount"
                placeholder="abc123xyz"
                value={streamForm.mount}
                onChange={(event) => setStreamField("mount", event.currentTarget.value)}
              />
              <TextInput
                label="Username"
                required
                value={streamForm.username}
                onChange={(event) => setStreamField("username", event.currentTarget.value)}
              />
              <TextInput
                label="Password"
                type="password"
                autoComplete="new-password"
                description={
                  passwordSet
                    ? "Saved on this controller only, out of backups."
                    : "Kept on this controller only, out of backups."
                }
                placeholder={passwordSet ? "(saved; leave blank to keep)" : undefined}
                value={streamForm.password}
                onChange={(event) => setStreamField("password", event.currentTarget.value)}
              />
              <Select
                label="Bitrate"
                data={[
                  { value: "16", label: "16 kbps (Broadcastify)" },
                  { value: "24", label: "24 kbps" },
                  { value: "32", label: "32 kbps" },
                ]}
                value={streamForm.bitrate}
                onChange={(value) => setStreamField("bitrate", value || "16")}
                allowDeselect={false}
              />
              <TextInput
                label="Stream name"
                maxLength={100}
                placeholder="W1AW repeater"
                value={streamForm.name}
                onChange={(event) => setStreamField("name", event.currentTarget.value)}
              />
              <TextInput
                label="Genre"
                maxLength={100}
                value={streamForm.genre}
                onChange={(event) => setStreamField("genre", event.currentTarget.value)}
              />
              <TextInput
                label="Description"
                maxLength={200}
                value={streamForm.description}
                onChange={(event) => setStreamField("description", event.currentTarget.value)}
                style={{ gridColumn: "1 / -1" }}
              />
            </SimpleGrid>
            <Switch
              label="Older server (Icecast before 2.4)"
              checked={streamForm.legacy_icecast}
              onChange={(event) => setStreamField("legacy_icecast", event.currentTarget.checked)}
            />
            <Group justify="flex-end">
              <Button type="submit" loading={streamSaving}>
                Save
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : null}
    </Page>
  );
}
