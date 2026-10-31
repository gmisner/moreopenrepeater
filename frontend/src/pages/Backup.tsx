import {
  Anchor,
  Button,
  Card,
  FileButton,
  Group,
  NumberInput,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { notifyError, notifySuccess, useConfig, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

type BackupContents = {
  macros?: number;
  announcements?: number;
  audio_clips?: number;
  users?: number;
  recordings?: number;
};

type SavedBackup = {
  name: string;
  created_at: string;
  size: number;
  contents: BackupContents;
};

type BackupFolder = {
  enabled: boolean;
  directory: string | null;
  last_error: string | null;
  backups: SavedBackup[];
};

type RestoreResult = {
  macros: number;
  announcements: number;
  users: number | null;
  audio_clips: number;
  history: boolean;
  recordings: number;
};

type BackupConfig = {
  backup_enabled: boolean;
  backup_interval_hours: number;
  backup_keep: number;
  backup_include_recordings: boolean;
};

const CONFIG_KEYS = [
  "backup_enabled",
  "backup_interval_hours",
  "backup_keep",
  "backup_include_recordings",
] as (keyof BackupConfig)[];

const CONFIG_DEFAULTS: BackupConfig = {
  backup_enabled: false,
  backup_interval_hours: 24,
  backup_keep: 7,
  backup_include_recordings: false,
};

const RESTORE_WARNING =
  "This replaces the settings, macros, announcements, audio clips, dashboard users, activity history and audit log " +
  "with the backup's. If your account isn't in the backup, you'll be signed out.";

function formatTimestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function plural(count: number, word: string) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function describeContents(c: BackupContents) {
  const parts = [
    plural(c.macros ?? 0, "macro"),
    plural(c.announcements ?? 0, "announcement"),
    plural(c.audio_clips ?? 0, "clip"),
    plural(c.users ?? 0, "user"),
  ];
  if (c.recordings) parts.push(plural(c.recordings, "recording"));
  return parts.join(", ");
}

function describeRestore(r: RestoreResult) {
  const parts = [
    plural(r.macros, "macro"),
    plural(r.announcements, "announcement"),
    plural(r.audio_clips, "audio clip"),
  ];
  if (r.users !== null) parts.push(plural(r.users, "user"));
  if (r.history) parts.push("activity history");
  if (r.recordings) parts.push(plural(r.recordings, "new recording"));
  const kept = r.users === null ? " The backup had no dashboard users, so the current ones were kept." : "";
  return `Backup restored: ${parts.join(", ")}.${kept}`;
}

function ScheduledBackups({ folder, setFolder }: { folder: BackupFolder | null; setFolder: (f: BackupFolder) => void }) {
  const { values, setField, dirty, reset, submit, saving } = useConfigSlice<BackupConfig>(CONFIG_KEYS, CONFIG_DEFAULTS);
  const { refresh } = useConfig();
  const [busyNow, setBusyNow] = useState(false);
  const [busyRow, setBusyRow] = useState<string | null>(null);

  const afterRestore = async (result: RestoreResult) => {
    notifySuccess(describeRestore(result));
    try {
      await refresh();
    } catch {
      location.reload();
    }
    try {
      setFolder(await api<BackupFolder>("/api/backups"));
    } catch {
      // folder list is best-effort after restore
    }
  };

  const backupNow = async () => {
    setBusyNow(true);
    try {
      setFolder(await api<BackupFolder>("/api/backups", { method: "POST" }));
      notifySuccess("Backup saved");
    } catch (error) {
      notifyError(error);
    } finally {
      setBusyNow(false);
    }
  };

  const restoreNamed = async (backup: SavedBackup) => {
    if (!window.confirm(`Restore the backup from ${formatTimestamp(backup.created_at)}? ${RESTORE_WARNING}`)) return;
    setBusyRow(backup.name);
    try {
      const result = await api<RestoreResult>(`/api/backups/${encodeURIComponent(backup.name)}/restore`, {
        method: "POST",
      });
      await afterRestore(result);
    } catch (error) {
      notifyError(error);
    } finally {
      setBusyRow(null);
    }
  };

  const deleteNamed = async (backup: SavedBackup) => {
    if (!window.confirm(`Delete the backup from ${formatTimestamp(backup.created_at)}?`)) return;
    try {
      setFolder(await api<BackupFolder>(`/api/backups/${encodeURIComponent(backup.name)}`, { method: "DELETE" }));
    } catch (error) {
      notifyError(error);
    }
  };

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
      <Title order={2} c="dimmed" tt="uppercase" mb="sm" style={{ letterSpacing: "0.06em" }}>
        Backups on the repeater
      </Title>
      <Text size="sm" c="dimmed" mb="sm">
        {folder?.enabled
          ? `Saved in ${folder.directory} on the repeater. Point MOREOPENREPEATER_BACKUP_DIR at a USB drive or network share to keep them off the SD card.`
          : "No backup folder is set up."}
      </Text>
      {folder?.last_error ? (
        <Text c="red" size="sm" mb="sm">
          The last scheduled backup failed: {folder.last_error}
        </Text>
      ) : null}
      <Stack gap="md">
        <Switch
          label="Back up automatically"
          checked={values.backup_enabled}
          onChange={(event) => setField("backup_enabled", event.currentTarget.checked)}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <NumberInput
            label="Every"
            suffix=" h"
            min={1}
            max={720}
            step={1}
            value={values.backup_interval_hours}
            onChange={(value) => setField("backup_interval_hours", Number(value) || 1)}
            required
          />
          <NumberInput
            label="Keep the newest"
            suffix=" backups"
            min={1}
            max={100}
            step={1}
            value={values.backup_keep}
            onChange={(value) => setField("backup_keep", Number(value) || 1)}
            required
          />
        </SimpleGrid>
        <Switch
          label="Include recordings"
          checked={values.backup_include_recordings}
          onChange={(event) => setField("backup_include_recordings", event.currentTarget.checked)}
        />
        <Group justify="space-between" align="center" mt="md">
          <Button type="button" variant="subtle" loading={busyNow} disabled={!folder?.enabled} onClick={() => void backupNow()}>
            Back up now
          </Button>
          <Group gap="sm">
            {dirty ? (
              <Text size="sm" c="amber">
                Unsaved changes
              </Text>
            ) : null}
            {dirty ? (
              <Button type="button" variant="subtle" onClick={reset}>
                Discard
              </Button>
            ) : null}
            <Button type="submit" loading={saving}>
              Save
            </Button>
          </Group>
        </Group>
      </Stack>

      <Table.ScrollContainer minWidth={560} mt="md">
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Made</Table.Th>
              <Table.Th>Size</Table.Th>
              <Table.Th>Contents</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(folder?.backups || []).map((backup) => (
              <Table.Tr key={backup.name}>
                <Table.Td>{formatTimestamp(backup.created_at)}</Table.Td>
                <Table.Td>{formatSize(backup.size)}</Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {describeContents(backup.contents)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end" wrap="nowrap">
                    <Anchor href={`/api/backups/${encodeURIComponent(backup.name)}`} download size="sm">
                      Download
                    </Anchor>
                    <Button
                      size="compact-sm"
                      variant="subtle"
                      loading={busyRow === backup.name}
                      onClick={() => void restoreNamed(backup)}
                    >
                      Restore
                    </Button>
                    <Button size="compact-sm" color="red" variant="subtle" onClick={() => void deleteNamed(backup)}>
                      Delete
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {!folder?.backups.length ? (
        <Text size="sm" c="dimmed" mt="sm">
          No backups saved on the repeater yet.
        </Text>
      ) : null}
    </Card>
  );
}

export function Backup() {
  const { session } = useSession();
  const { refresh } = useConfig();
  const isAdmin = session?.role === "admin";
  const [includeRecordings, setIncludeRecordings] = useState(false);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [folder, setFolder] = useState<BackupFolder | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    api<BackupFolder>("/api/backups")
      .then(setFolder)
      .catch(notifyError);
  }, [isAdmin]);

  const downloadSnapshot = async () => {
    setSnapshotBusy(true);
    try {
      const snapshot = await api<{ config: { callsign?: string } }>("/api/snapshot");
      const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const callsign = (snapshot.config.callsign || "repeater").toLowerCase();
      link.href = url;
      link.download = `moreopenrepeater-${callsign}-settings-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      notifyError(error);
    } finally {
      setSnapshotBusy(false);
    }
  };

  const restoreFile = async (file: File | null) => {
    if (!file) return;
    try {
      if (file.name.toLowerCase().endsWith(".zip")) {
        if (!window.confirm(`Restore "${file.name}"? ${RESTORE_WARNING}`)) return;
        const body = new FormData();
        body.append("file", file);
        const result = await api<RestoreResult>("/api/backup/restore", { method: "POST", body });
        notifySuccess(describeRestore(result));
        try {
          await refresh();
        } catch {
          location.reload();
        }
        if (isAdmin) {
          try {
            setFolder(await api<BackupFolder>("/api/backups"));
          } catch {
            // ignore
          }
        }
        return;
      }
      let snapshot: unknown;
      try {
        snapshot = JSON.parse(await file.text());
      } catch {
        notifyError(new Error("That isn't a backup file: choose a .zip full backup or a .json settings file"));
        return;
      }
      const snap = snapshot as { macros?: unknown[]; announcements?: unknown[] };
      const counts = `${plural(snap.macros?.length ?? 0, "macro")} and ${plural(snap.announcements?.length ?? 0, "announcement")} in the backup`;
      if (
        !window.confirm(
          `Restore "${file.name}"? This replaces the current configuration, macros and announcements (${counts}).`,
        )
      ) {
        return;
      }
      await api("/api/snapshot", { method: "POST", json: snapshot });
      try {
        await refresh();
      } catch {
        location.reload();
      }
      notifySuccess("Settings restored");
    } catch (error) {
      notifyError(error);
    }
  };

  return (
    <Page
      title="Backup & restore"
      description="A full backup holds everything needed to rebuild the repeater: settings, DTMF macros, announcements, audio clips, dashboard users, airtime statistics and the audit log, and optionally recordings. The phone line lives in Asterisk and isn't included."
    >
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        {isAdmin ? (
          <Card withBorder padding="lg" radius="md">
            <Title order={2} c="dimmed" tt="uppercase" mb="sm" style={{ letterSpacing: "0.06em" }}>
              Full backup
            </Title>
            <Text size="sm" c="dimmed" mb="md">
              A .zip file. It holds the dashboard users&apos; password hashes, so keep it somewhere private.
            </Text>
            <Switch
              label="Include recordings"
              checked={includeRecordings}
              onChange={(event) => setIncludeRecordings(event.currentTarget.checked)}
              mb="md"
            />
            <Anchor href={includeRecordings ? "/api/backup?recordings=true" : "/api/backup"} download>
              <Button component="span">Download full backup</Button>
            </Anchor>
          </Card>
        ) : null}

        <Card withBorder padding="lg" radius="md">
          <Title order={2} c="dimmed" tt="uppercase" mb="sm" style={{ letterSpacing: "0.06em" }}>
            Settings only
          </Title>
          <Text size="sm" c="dimmed" mb="md">
            The configuration, macros and announcements as a JSON file, without audio clips, users or history.
          </Text>
          <Button loading={snapshotBusy} onClick={() => void downloadSnapshot()}>
            Download settings
          </Button>
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Title order={2} c="dimmed" tt="uppercase" mb="sm" style={{ letterSpacing: "0.06em" }}>
            Restore
          </Title>
          <Text size="sm" c="dimmed" mb="md">
            A full backup (.zip) replaces everything it holds, except that recordings are added to the ones already
            here. A settings file (.json) replaces the configuration, macros and announcements.
          </Text>
          <FileButton onChange={(file) => void restoreFile(file)} accept=".zip,.json,application/zip,application/json">
            {(props) => (
              <Button {...props} variant="default">
                Choose backup file…
              </Button>
            )}
          </FileButton>
        </Card>
      </SimpleGrid>

      {isAdmin ? <ScheduledBackups folder={folder} setFolder={setFolder} /> : null}
    </Page>
  );
}
