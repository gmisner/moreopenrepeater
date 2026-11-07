import { Alert, Button, Group, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ackVersionSha, markVersionSeen, readAckedVersionSha, readSeenVersionSha } from "../prefs";

type VersionInfo = { sha: string; date: string; subject: string };

const POLL_MS = 30_000;

export function ServerUpdatedBanner() {
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [reloadNeeded, setReloadNeeded] = useState(false);
  const [whatsNew, setWhatsNew] = useState<VersionInfo | null>(null);
  const [dismissedReload, setDismissedReload] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const body = await api<VersionInfo | null>("/api/version");
        if (cancelled || !body?.sha) return;
        setVersion(body);
        const seen = readSeenVersionSha();
        if (seen && seen !== body.sha) {
          setReloadNeeded(true);
        } else {
          markVersionSeen(body.sha);
        }
        const acked = readAckedVersionSha();
        if (acked !== body.sha) {
          setWhatsNew(body);
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
      {whatsNew && !reloadNeeded ? (
        <Alert color="accent" variant="light" m="md" radius="md">
          <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
            <div>
              <Text size="sm" fw={600}>
                What&apos;s new
              </Text>
              <Text size="sm" c="dimmed">
                <Text span ff="monospace">
                  {whatsNew.sha.slice(0, 7)}
                </Text>{" "}
                {whatsNew.subject}
              </Text>
            </div>
            <Button
              size="xs"
              onClick={() => {
                ackVersionSha(whatsNew.sha);
                setWhatsNew(null);
              }}
            >
              Got it
            </Button>
          </Group>
        </Alert>
      ) : null}
    </>
  );
}
