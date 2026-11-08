import {
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  PasswordInput,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { labeled } from "../components/FieldHint";
import { EmptyState } from "../components/EmptyState";
import { ErrorCard } from "../components/StateCards";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";
import { ActionsHeader } from "../components/ActionsHeader";

type HubSite = {
  id: string;
  name: string;
  base_url: string;
  dashboard_url: string;
  token_set: boolean;
};

type HubSiteStatus = {
  id: string;
  name: string;
  base_url: string;
  dashboard_url: string;
  reachable: boolean;
  polled_at: number;
  error: string | null;
  callsign: string | null;
  state: string | null;
  version: string | null;
  channel: string | null;
  last_activity: number | null;
  alerts_summary: string | null;
};

function formatActivity(value: number | null) {
  if (value == null) return "—";
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function versionLabel(status: HubSiteStatus) {
  if (!status.reachable) return "—";
  const parts = [status.version, status.channel].filter(Boolean);
  return parts.length ? parts.join(" · ") : "—";
}

export function Sites() {
  const [sites, setSites] = useState<HubSite[]>([]);
  const [statuses, setStatuses] = useState<HubSiteStatus[]>([]);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [adding, setAdding] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const loadSites = useCallback(async () => {
    try {
      setSites(await api<HubSite[]>("/api/hub/sites"));
      setLoadFailed(false);
    } catch (error) {
      setLoadFailed(true);
      throw error;
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    setRefreshing(true);
    try {
      setStatuses(await api<HubSiteStatus[]>("/api/hub/sites/status"));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadSites()
      .then(() => refreshStatus())
      .catch(notifyError);
  }, [loadSites, refreshStatus]);

  const statusById = Object.fromEntries(statuses.map((status) => [status.id, status]));

  const addSite = async (event: FormEvent) => {
    event.preventDefault();
    setAdding(true);
    try {
      setSites(
        await api<HubSite[]>("/api/hub/sites", {
          method: "POST",
          json: { name, base_url: baseUrl, api_token: apiToken },
        }),
      );
      notifySuccess(`Added ${name}`);
      setName("");
      setBaseUrl("");
      setApiToken("");
      await refreshStatus();
    } catch (error) {
      notifyError(error);
    } finally {
      setAdding(false);
    }
  };

  const removeSite = async (site: HubSite) => {
    if (!window.confirm(`Remove ${site.name}? The remote repeater itself is unchanged.`)) return;
    try {
      setSites(await api<HubSite[]>(`/api/hub/sites/${encodeURIComponent(site.id)}`, { method: "DELETE" }));
      setStatuses((prev) => prev.filter((status) => status.id !== site.id));
      notifySuccess(`Removed ${site.name}`);
    } catch (error) {
      notifyError(error);
    }
  };

  return (
    <Page
      title="Sites"
      actions={
        <Button variant="default" loading={refreshing} onClick={() => refreshStatus().catch(notifyError)}>
          Refresh status
        </Button>
      }
    >
      <Stack gap="lg">
        <Title order={2} size="h3">{labeled("Configured sites", <>Create a viewer or admin token on each remote under Users, then add it here.</>)}</Title>

        {loadFailed && sites.length === 0 ? (
          <ErrorCard
            title="Couldn't load your sites"
            onRetry={() =>
              loadSites()
                .then(() => refreshStatus())
                .catch(notifyError)
            }
          >
            The repeater didn't answer, so this isn't the same as having no sites.
          </ErrorCard>
        ) : sites.length === 0 ? (
          <EmptyState>No remote sites yet. Add one below.</EmptyState>
        ) : (
          <Table.ScrollContainer minWidth={720}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Site</Table.Th>
                  <Table.Th>Reachable</Table.Th>
                  <Table.Th>State</Table.Th>
                  <Table.Th>Version</Table.Th>
                  <Table.Th>Last activity</Table.Th>
                  <Table.Th>Alerts</Table.Th>
                  <ActionsHeader />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {sites.map((site) => {
                  const status = statusById[site.id];
                  const label = status?.callsign || site.name;
                  return (
                    <Table.Tr key={site.id}>
                      <Table.Td>
                        <Stack gap={2}>
                          <Text fw={600}>{label}</Text>
                          <Text size="sm" c="dimmed">
                            {status?.callsign && status.callsign !== site.name ? `${site.name} · ` : ""}
                            {site.base_url}
                          </Text>
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        {status == null ? (
                          <Badge color="gray" variant="light">
                            …
                          </Badge>
                        ) : status.reachable ? (
                          <Badge color="green" variant="light">
                            yes
                          </Badge>
                        ) : (
                          <Badge color="red" variant="light" title={status.error || undefined}>
                            no
                          </Badge>
                        )}
                      </Table.Td>
                      <Table.Td>{status?.reachable ? status.state || "—" : status?.error || "—"}</Table.Td>
                      <Table.Td>{status ? versionLabel(status) : "—"}</Table.Td>
                      <Table.Td>{formatActivity(status?.last_activity ?? null)}</Table.Td>
                      <Table.Td>{status?.reachable ? status.alerts_summary || "—" : "—"}</Table.Td>
                      <Table.Td>
                        <Group gap="xs" justify="flex-end" wrap="nowrap">
                          <Anchor href={site.dashboard_url} target="_blank" rel="noreferrer" size="sm">
                            Open
                          </Anchor>
                          <Button size="compact-xs" variant="subtle" color="red" onClick={() => removeSite(site)}>
                            Remove
                          </Button>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}

        <Card withBorder padding="md" radius="md" component="form" onSubmit={addSite}>
          <Stack gap="sm">
            <Title order={2} size="h4">Add a site</Title>
            <TextInput
              label={labeled("Name", "A label for this hub.")}
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
              placeholder="e.g. production"
              required
            />
            <TextInput
              label={labeled("Base URL", "Origin only — no /api path.")}
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.currentTarget.value)}
              placeholder="e.g. https://…"
              required
            />
            <PasswordInput
              label={labeled(
                "API token",
                "A token from the remote's Users page. Admin tokens see version and alerts; viewer tokens still show status and activity.",
              )}
              value={apiToken}
              onChange={(event) => setApiToken(event.currentTarget.value)}
              required
            />
            <Group justify="flex-end">
              <Button type="submit" loading={adding}>
                Add site
              </Button>
            </Group>
          </Stack>
        </Card>
      </Stack>
    </Page>
  );
}
