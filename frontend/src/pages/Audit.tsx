import { Badge, Button, Card, Group, Table, Text } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { notifyError } from "../config";
import { Page } from "../layout/AppShell";

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

  const load = useCallback(async () => {
    try {
      setEntries(await api<Entry[]>("/api/audit?limit=300"));
    } catch (error) {
      notifyError(error);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Page title="Audit log" description="Who changed what on this controller.">
      <Group justify="flex-end">
        <Button variant="default" onClick={() => void load()}>
          Refresh
        </Button>
      </Group>
      <Card withBorder padding="lg" radius="md">
        {entries.length ? (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>When</Table.Th>
                <Table.Th>Who</Table.Th>
                <Table.Th>Action</Table.Th>
                <Table.Th>Detail</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {entries.map((entry, index) => (
                <Table.Tr key={`${entry.at}-${index}`}>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {new Date(entry.at).toLocaleString()}
                    </Text>
                  </Table.Td>
                  <Table.Td>{entry.actor}</Table.Td>
                  <Table.Td title={entry.action}>{decodeURIComponent(describe(entry))}</Table.Td>
                  <Table.Td>
                    <Text size="sm">{entry.detail}</Text>
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
          <Text c="dimmed">No audit entries yet.</Text>
        )}
      </Card>
    </Page>
  );
}
