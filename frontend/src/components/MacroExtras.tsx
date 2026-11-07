import { Box, Button, Card, Code, Group, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { notifyError, notifySuccess } from "../config";
import { useSession } from "../session";
import { labeled } from "./FieldHint";

type HomeAssistantStatus = { token_set: boolean };
type ControlCodeStatus = { enrolled: boolean; since: number | null; pending: boolean };
type ControlCodeSetup = { secret: string; uri: string; qr_svg: string };
type ControlCodeUser = { username: string; since: number };

export function HomeAssistantTest() {
  const [status, setStatus] = useState<HomeAssistantStatus | null>(null);
  const [target, setTarget] = useState("");
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    api<HomeAssistantStatus>("/api/homeassistant").then(setStatus).catch(notifyError);
  }, []);

  const sendTest = async () => {
    if (!target.trim()) return;
    setTesting(true);
    try {
      await api("/api/homeassistant/test", { method: "POST", json: { target: target.trim() } });
      notifySuccess(`Home Assistant took the test for ${target.trim()}`);
    } catch (error) {
      notifyError(error);
    } finally {
      setTesting(false);
    }
  };

  return (
    <Stack gap="sm">
      {status ? (
        status.token_set ? (
          <Text c="dimmed" size="sm">An access token is set, so macros can fire events too.</Text>
        ) : (
          <Text c="dimmed" size="sm">
            No access token is set, so only webhooks work. To fire events, put a long-lived access token in{" "}
            <Code>MOREOPENREPEATER_HOMEASSISTANT_TOKEN</Code> in <Code>/etc/moreopenrepeater/env</Code> and restart.
          </Text>
        )
      ) : null}
      <Group align="flex-end" wrap="wrap">
        <TextInput
          label={labeled("Test webhook ID or event", "Use event: followed by an event type to test an event.")}
          placeholder="e.g. webhook_id"
          pattern="(event:)?[A-Za-z0-9_.\-]{1,100}"
          value={target}
          onChange={(event) => setTarget(event.currentTarget.value)}
          style={{ flex: "1 1 280px" }}
        />
        <Button type="button" variant="default" loading={testing} disabled={!target.trim()} onClick={() => void sendTest()}>
          Send a test
        </Button>
      </Group>
    </Stack>
  );
}

export function ControlCodesCard() {
  const { session } = useSession();
  const [status, setStatus] = useState<ControlCodeStatus | null>(null);
  const [users, setUsers] = useState<ControlCodeUser[]>([]);
  const [setup, setSetup] = useState<ControlCodeSetup | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const isAdmin = session?.role === "admin";
  const canWrite = session?.role !== "viewer";

  const load = useCallback(async () => {
    if (!canWrite) return;
    const [nextStatus, nextUsers] = await Promise.all([
      api<ControlCodeStatus>("/api/me/control-code"),
      isAdmin ? api<ControlCodeUser[]>("/api/control-codes") : Promise.resolve([]),
    ]);
    setStatus(nextStatus);
    setUsers(nextUsers);
  }, [canWrite, isAdmin]);

  useEffect(() => {
    load().catch(notifyError);
  }, [load]);

  if (!canWrite) return null;

  const begin = async () => {
    setBusy("begin");
    try {
      setSetup(await api<ControlCodeSetup>("/api/me/control-code", { method: "POST" }));
      setCode("");
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(null);
    }
  };

  const confirm = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("confirm");
    try {
      setStatus(await api<ControlCodeStatus>("/api/me/control-code/confirm", { method: "POST", json: { code } }));
      setSetup(null);
      setCode("");
      notifySuccess("One-time codes are set up");
      if (isAdmin) setUsers(await api<ControlCodeUser[]>("/api/control-codes"));
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(null);
    }
  };

  const removeMine = async () => {
    if (!window.confirm("Remove your one-time codes? Codes from your app will stop working.")) return;
    setBusy("remove-mine");
    try {
      setStatus(await api<ControlCodeStatus>("/api/me/control-code", { method: "DELETE" }));
      setSetup(null);
      notifySuccess("Removed your one-time codes");
      if (isAdmin) setUsers(await api<ControlCodeUser[]>("/api/control-codes"));
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(null);
    }
  };

  const removeUser = async (username: string) => {
    if (!window.confirm(`Remove ${username}'s one-time codes?`)) return;
    setBusy(`remove-${username}`);
    try {
      setUsers(await api<ControlCodeUser[]>(`/api/control-codes/${encodeURIComponent(username)}`, { method: "DELETE" }));
      notifySuccess(`Removed ${username}'s one-time codes`);
      if (username === session?.username) setStatus(await api<ControlCodeStatus>("/api/me/control-code"));
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="md">
        <Title order={2}>One-time control codes</Title>
        <Text c="dimmed" size="sm" maw="75ch">
          Protect sensitive DTMF macros with six-digit codes from an authenticator app. Each code works once.
        </Text>
        <Text size="sm">
          {status?.enrolled
            ? `Your codes are set up${status.since ? ` (since ${new Date(status.since * 1000).toLocaleString()})` : ""}.`
            : "You haven't set up one-time codes."}
        </Text>
        <Group gap="sm">
          <Button loading={busy === "begin"} onClick={() => void begin()}>
            {status?.enrolled ? "Move to a new phone" : "Set up codes"}
          </Button>
          {status?.enrolled ? (
            <Button color="red" variant="subtle" loading={busy === "remove-mine"} onClick={() => void removeMine()}>
              Remove my codes
            </Button>
          ) : null}
        </Group>

        {setup ? (
          <Card withBorder padding="md" radius="sm" bg="var(--mantine-color-default)">
            <Stack gap="md">
              <Text size="sm">
                Scan this QR code with an authenticator app, or enter the secret by hand. Then enter the current code to finish.
              </Text>
              <Group align="flex-start" wrap="wrap">
                <Box
                  bg="white"
                  p="sm"
                  style={{ borderRadius: 8, lineHeight: 0, maxWidth: 240 }}
                  dangerouslySetInnerHTML={{ __html: setup.qr_svg }}
                />
                <Stack gap="xs">
                  <Text size="sm" c="dimmed">Secret</Text>
                  <Code>{setup.secret.match(/.{1,4}/g)?.join(" ") ?? setup.secret}</Code>
                </Stack>
              </Group>
              <Group component="form" onSubmit={(event) => void confirm(event)} align="flex-end" wrap="wrap">
                <TextInput
                  label="Six-digit code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  value={code}
                  onChange={(event) => setCode(event.currentTarget.value.replace(/\D/g, ""))}
                  required
                />
                <Button type="submit" loading={busy === "confirm"}>Confirm</Button>
              </Group>
            </Stack>
          </Card>
        ) : null}

        {isAdmin ? (
          <>
            <Title order={3}>Enrolled users</Title>
            {users.length ? (
              <Table.ScrollContainer minWidth={480}>
                <Table striped highlightOnHover>
                  <Table.Thead><Table.Tr><Table.Th>User</Table.Th><Table.Th>Since</Table.Th><Table.Th /></Table.Tr></Table.Thead>
                  <Table.Tbody>
                    {users.map((user) => (
                      <Table.Tr key={user.username}>
                        <Table.Td>{user.username}</Table.Td>
                        <Table.Td>{new Date(user.since * 1000).toLocaleString()}</Table.Td>
                        <Table.Td>
                          <Button
                            size="compact-sm"
                            color="red"
                            variant="subtle"
                            loading={busy === `remove-${user.username}`}
                            onClick={() => void removeUser(user.username)}
                          >
                            Remove
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : <Text c="dimmed" size="sm">No users have one-time codes set up.</Text>}
          </>
        ) : null}
      </Stack>
    </Card>
  );
}
