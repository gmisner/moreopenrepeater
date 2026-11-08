import { Badge, Button, Card, Group, Switch, Table, Text } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorCard } from "../components/StateCards";
import { notifyError } from "../config";
import { Page } from "../layout/AppShell";
import { ActionsHeader } from "../components/ActionsHeader";

type Entry = { at: string; actor: string; action: string; detail: string; status?: number };

const ACTIONS: [RegExp, string][] = [
  [/^PUT \/api\/config$/, "Changed settings"],
  [/^POST \/api\/login$/, "Signed in"],
  [/^POST \/api\/logout$/, "Signed out"],
  [/^POST \/api\/macros$/, "Added a DTMF macro"],
  [/^DELETE \/api\/macros\/(.+)$/, "Deleted DTMF macro $1"],
  [/^POST \/api\/announcements$/, "Added an announcement"],
  [/^POST \/api\/announcements\/.+\/play$/, "Played an announcement"],
  [/^PUT \/api\/announcements\//, "Edited an announcement"],
  [/^DELETE \/api\/announcements\//, "Deleted an announcement"],
  [/^POST \/api\/users$/, "Added a user"],
  [/^DELETE \/api\/users\/(.+)$/, "Deleted user $1"],
  [/^POST \/api\/updates$/, "Started an update"],
  [/^POST \/api\/lockout\/clear$/, "Cleared a stuck-carrier lockout"],
  [/^DTMF (.+)$/, "Over the air: $1"],
];

function describe(entry: Entry) {
  if (entry.action === "POST /api/login" && entry.status === 401) return "Sign-in failed";
  for (const [pattern, label] of ACTIONS) {
    if (pattern.test(entry.action)) return entry.action.replace(pattern, label);
  }
  return entry.action;
}

export function Audit() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [showSimulator, setShowSimulator] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setEntries(await api<Entry[]>("/api/audit?limit=300"));
      setFailed(false);
    } catch (error) {
      setFailed(true);
      notifyError(error);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const isSimulator = (entry: Entry) => entry.action.startsWith("POST /api/simulate/");
  const shown = showSimulator ? entries : entries.filter((entry) => !isSimulator(entry));
  const hidden = entries.length - shown.length;

  return (
    <Page
      title="Audit log"
      actions={
        <Group gap="md">
          <Switch
            label="Show simulator events"
            checked={showSimulator}
            onChange={(event) => setShowSimulator(event.currentTarget.checked)}
          />
          <Button variant="default" onClick={() => void load()}>
            Refresh
          </Button>
        </Group>
      }
    >
      <Card withBorder padding="lg" radius="md">
        {failed && entries.length === 0 ? (
          <ErrorCard title="Couldn't load the audit log" onRetry={() => void load()}>
            The repeater didn't answer, so this isn't the same as an empty log.
          </ErrorCard>
        ) : shown.length ? (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>When</Table.Th>
                <Table.Th>Who</Table.Th>
                <Table.Th>Action</Table.Th>
                <Table.Th>Detail</Table.Th>
                <ActionsHeader />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((entry, index) => (
                <Table.Tr key={`${entry.at}-${index}`}>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {new Date(entry.at).toLocaleString()}
                    </Text>
                  </Table.Td>
                  <Table.Td>{entry.actor}</Table.Td>
                  <Table.Td title={entry.action}>{decodeURIComponent(describe(entry))}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c={entry.detail ? undefined : "dimmed"}>
                      {entry.detail || "—"}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {entry.status && entry.status >= 400 && entry.action !== "POST /api/login" ? (
                      <Badge color="red" variant="light">
                        {entry.status === 401 ? "not signed in" : entry.status === 403 ? "not allowed" : `failed (${entry.status})`}
                      </Badge>
                    ) : null}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <EmptyState>{hidden ? "Only simulator events so far. Turn them on above to see them." : "No audit entries yet."}</EmptyState>
        )}
      </Card>
    </Page>
  );
}
