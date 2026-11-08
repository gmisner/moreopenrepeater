import { Alert, Button, Group, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ackVersionSha, markVersionSeen, readAckedVersionSha, readSeenVersionSha } from "../prefs";

type VersionInfo = { sha: string; date: string; subject: string };

const POLL_MS = 30_000;

export function ServerUpdatedBanner() {
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [reloadNeeded, setReloadNeeded] = useState(false);
  const [dismissedReload, setDismissedReload] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const body = await api<VersionInfo | null>("/api/version");
        if (cancelled || !body?.sha) return;
        setVersion(body);
        const seen = readSeenVersionSha();
        const stale = Boolean(seen && seen !== body.sha);
        if (stale) {
          setReloadNeeded(true);
        } else {
          markVersionSeen(body.sha);
        }
        // A new version is announced once, as a toast, and counts as seen right away.
        if (!stale && readAckedVersionSha() !== body.sha) {
          notifications.show({
            id: "whats-new",
            title: "moreopenrepeater was updated",
            message: body.subject,
            autoClose: 12000,
            withCloseButton: true,
          });
          ackVersionSha(body.sha);
        }
      } catch {
        /* ignore while signed out / offline */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <>
      {reloadNeeded && !dismissedReload && version ? (
        <Alert color="accent" variant="light" radius={0} styles={{ root: { borderRadius: 0 } }}>
          <Group justify="space-between" align="center" wrap="wrap" gap="sm">
            <Text size="sm">
              moreopenrepeater was updated on the server. Reload to get the latest dashboard.
            </Text>
            <Group gap="xs">
              <Button size="xs" onClick={() => window.location.reload()}>
                Reload
              </Button>
              <Button size="xs" variant="subtle" color="gray" onClick={() => setDismissedReload(true)}>
                Dismiss
              </Button>
            </Group>
          </Group>
        </Alert>
      ) : null}
    </>
  );
}
