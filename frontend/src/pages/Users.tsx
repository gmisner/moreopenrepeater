import {
  Badge,
  Button,
  Card,
  Code,
  Group,
  PasswordInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

const ROLES = [
  { value: "viewer", label: "Viewer" },
  { value: "listener", label: "Listener (listening page only)" },
  { value: "operator", label: "Operator" },
  { value: "admin", label: "Admin" },
] as const;

const ROLE_LABELS: Record<string, string> = {
  admin: "Admin",
  operator: "Operator",
  viewer: "Viewer",
  listener: "Listener",
};

type User = { username: string; role: string; builtin: boolean };
type Token = {
  id: string;
  name: string;
  username: string;
  role: string | null;
  created_at: string;
  last_used_at: string | null;
};
type AgentAccess = { transmit: boolean; mcp_available: boolean };

function formatTimestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function Users() {
  const { session } = useSession();
  const [users, setUsers] = useState<User[]>([]);
  const [tokens, setTokens] = useState<Token[]>([]);
  const [agent, setAgent] = useState<AgentAccess | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [adding, setAdding] = useState(false);
  const [tokenName, setTokenName] = useState("");
  const [tokenUsername, setTokenUsername] = useState("");
  const [creatingToken, setCreatingToken] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);

  const signInOn = session?.auth_required !== false;
  const mcpUrl = `${location.origin}/mcp`;
  const mcpConfig = JSON.stringify(
    { mcpServers: { repeater: { url: mcpUrl, headers: { Authorization: "Bearer <your API token>" } } } },
    null,
    2,
  );

  const load = async () => {
    setUsers(await api<User[]>("/api/users"));
    if (signInOn) {
      setTokens(await api<Token[]>("/api/tokens"));
      setAgent(await api<AgentAccess>("/api/agent-access"));
    }
  };

  useEffect(() => {
    load().catch(notifyError);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- load once on mount

  const tokenUserOptions = users.filter((u) => u.role !== "listener");
  const effectiveTokenUser =
    tokenUsername && tokenUserOptions.some((u) => u.username === tokenUsername)
      ? tokenUsername
      : tokenUserOptions.find((u) => u.username === session?.username)?.username ||
        tokenUserOptions[0]?.username ||
        "";

  const updateUser = async (user: User, changes: { role?: string; password?: string }) => {
    try {
      setUsers(await api<User[]>(`/api/users/${encodeURIComponent(user.username)}`, { method: "PUT", json: changes }));
      notifySuccess(
        changes.password ? `Password reset for ${user.username}` : `${user.username} is now ${ROLE_LABELS[changes.role || ""]}`,
      );
    } catch (error) {
      notifyError(error);
      await load().catch(notifyError);
    }
  };

  const removeUser = async (user: User) => {
    if (!window.confirm(`Delete ${user.username}? They'll be signed out immediately.`)) return;
    try {
      setUsers(await api<User[]>(`/api/users/${encodeURIComponent(user.username)}`, { method: "DELETE" }));
      notifySuccess(`Deleted ${user.username}`);
    } catch (error) {
      notifyError(error);
    }
  };

  const revokeToken = async (token: Token) => {
    if (!window.confirm(`Revoke "${token.name}"? Anything using it stops working immediately.`)) return;
    try {
      setTokens(await api<Token[]>(`/api/tokens/${encodeURIComponent(token.id)}`, { method: "DELETE" }));
      notifySuccess(`Revoked ${token.name}`);
    } catch (error) {
      notifyError(error);
    }
  };

  const addUser = async (event: FormEvent) => {
    event.preventDefault();
    setAdding(true);
    try {
      setUsers(await api<User[]>("/api/users", { method: "POST", json: { username, password, role } }));
      notifySuccess(`Added ${username}`);
      setUsername("");
      setPassword("");
      setRole("viewer");
      if (!session?.auth_required) location.replace("/login");
    } catch (error) {
      notifyError(error);
    } finally {
      setAdding(false);
    }
  };

  const createToken = async (event: FormEvent) => {
    event.preventDefault();
    setCreatingToken(true);
    try {
      const result = await api<{ token: string; tokens: Token[] }>("/api/tokens", {
        method: "POST",
        json: { name: tokenName, username: effectiveTokenUser },
      });
      setTokens(result.tokens);
      setNewToken(result.token);
      setTokenName("");
      setTokenUsername(effectiveTokenUser);
      notifySuccess("Token created");
    } catch (error) {
      notifyError(error);
    } finally {
      setCreatingToken(false);
    }
  };

  const setTransmit = async (transmit: boolean) => {
    if (
      transmit &&
      !window.confirm("Let API tokens make the repeater transmit? You're responsible for what goes out on the air.")
    ) {
      return;
    }
    try {
      setAgent(await api<AgentAccess>("/api/agent-access", { method: "PUT", json: { transmit } }));
      notifySuccess(transmit ? "Agents may now transmit" : "Agents can no longer transmit");
    } catch (error) {
      notifyError(error);
    }
  };

  const copyToken = async () => {
    if (!newToken) return;
    try {
      await navigator.clipboard.writeText(newToken);
      notifySuccess("Token copied");
    } catch {
      notifyError(new Error("Couldn't copy to clipboard"));
    }
  };

  return (
    <Page
      title="Users"
      description="Admins manage everything, operators change repeater settings, viewers can only look and listen. Listeners only get the listening page at /listen, none of the dashboard."
    >
      {!signInOn ? (
        <Card withBorder padding="md" radius="md">
          <Text c="dimmed">
            Sign-in is currently off, so anyone who can reach this page has full control. Add an admin below to turn it on
            — you&apos;ll be asked to sign in straight away.
          </Text>
        </Card>
      ) : null}

      <Card withBorder padding="lg" radius="md">
        <Table.ScrollContainer minWidth={480}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>User</Table.Th>
                <Table.Th>Role</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.map((user) => (
                <Table.Tr key={user.username}>
                  <Table.Td>
                    <Group gap="xs">
                      <Text span>{user.username}</Text>
                      {session?.username === user.username ? (
                        <Badge size="sm" variant="light">
                          you
                        </Badge>
                      ) : null}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    {user.builtin ? (
                      <Text size="sm">
                        Admin{" "}
                        <Text span c="dimmed" size="xs">
                          (set in the service&apos;s environment file)
                        </Text>
                      </Text>
                    ) : (
                      <Select
                        data={ROLES.map((r) => ({ value: r.value, label: ROLE_LABELS[r.value] }))}
                        value={user.role}
                        onChange={(value) => {
                          if (value && value !== user.role) void updateUser(user, { role: value });
                        }}
                        allowDeselect={false}
                        w={160}
                      />
                    )}
                  </Table.Td>
                  <Table.Td>
                    {user.builtin ? null : (
                      <Group gap="xs" justify="flex-end">
                        <Button
                          size="compact-sm"
                          variant="subtle"
                          onClick={() => {
                            const next = window.prompt(`New password for ${user.username} (at least 8 characters):`);
                            if (next) void updateUser(user, { password: next });
                          }}
                        >
                          Reset password
                        </Button>
                        <Button size="compact-sm" color="red" variant="subtle" onClick={() => void removeUser(user)}>
                          Delete
                        </Button>
                      </Group>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>

      <Card withBorder padding="lg" radius="md" component="form" onSubmit={addUser}>
        <Title order={2} c="dimmed" tt="uppercase" mb="md" style={{ letterSpacing: "0.06em" }}>
          Add a user
        </Title>
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
          <TextInput
            label="Username"
            required
            maxLength={64}
            pattern="[A-Za-z0-9._@\-]+"
            autoComplete="off"
            value={username}
            onChange={(event) => setUsername(event.currentTarget.value)}
          />
          <PasswordInput
            label="Password"
            description="At least 8 characters."
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.currentTarget.value)}
          />
          <Select
            label="Role"
            data={[...ROLES]}
            value={role}
            onChange={(value) => setRole(value || "viewer")}
            allowDeselect={false}
          />
        </SimpleGrid>
        <Group justify="flex-end" mt="md">
          <Button type="submit" loading={adding}>
            Add user
          </Button>
        </Group>
      </Card>

      {signInOn ? (
        <>
          <Card withBorder padding="lg" radius="md">
            <Title order={2} c="dimmed" tt="uppercase" mb="sm" style={{ letterSpacing: "0.06em" }}>
              API tokens
            </Title>
            <Text size="sm" c="dimmed" mb="md" maw="75ch">
              For scripts and AI agents. A token acts as its user, with that user&apos;s role, and stops working when
              it&apos;s revoked or the user is deleted. Send it as{" "}
              <Code>Authorization: Bearer &lt;token&gt;</Code>. Tokens can&apos;t manage users or other tokens.
            </Text>
            {tokens.length > 0 ? (
              <Table.ScrollContainer minWidth={560} mb="md">
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Name</Table.Th>
                      <Table.Th>User</Table.Th>
                      <Table.Th>Created</Table.Th>
                      <Table.Th>Last used</Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {tokens.map((token) => (
                      <Table.Tr key={token.id}>
                        <Table.Td>{token.name}</Table.Td>
                        <Table.Td>
                          {token.username}{" "}
                          <Text span size="xs" c="dimmed">
                            {token.role ? ROLE_LABELS[token.role] : "user deleted"}
                          </Text>
                        </Table.Td>
                        <Table.Td>{formatTimestamp(token.created_at)}</Table.Td>
                        <Table.Td>
                          {token.last_used_at ? (
                            formatTimestamp(token.last_used_at)
                          ) : (
                            <Text span c="dimmed">
                              never
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Button size="compact-sm" color="red" variant="subtle" onClick={() => void revokeToken(token)}>
                            Revoke
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : null}
            {newToken ? (
              <Card withBorder padding="md" radius="md" mb="md" bg="var(--mantine-color-amber-light)">
                <Text size="sm" mb="xs">
                  Copy this token now. It isn&apos;t stored, so it can&apos;t be shown again.
                </Text>
                <Group gap="sm" wrap="nowrap">
                  <Code style={{ flex: 1, wordBreak: "break-all" }}>{newToken}</Code>
                  <Button size="compact-sm" variant="subtle" onClick={() => void copyToken()}>
                    Copy
                  </Button>
                </Group>
              </Card>
            ) : null}
            <form onSubmit={createToken}>
              <Group align="flex-end" gap="sm" wrap="wrap">
                <TextInput
                  label="Name"
                  required
                  maxLength={60}
                  placeholder="Claude on my laptop"
                  autoComplete="off"
                  value={tokenName}
                  onChange={(event) => setTokenName(event.currentTarget.value)}
                  style={{ flex: "1 1 12rem" }}
                />
                <Select
                  label="Acts as"
                  data={tokenUserOptions.map((u) => ({
                    value: u.username,
                    label: `${u.username} (${ROLE_LABELS[u.role]})`,
                  }))}
                  value={effectiveTokenUser || null}
                  onChange={(value) => setTokenUsername(value || "")}
                  allowDeselect={false}
                  w={220}
                />
                <Button type="submit" loading={creatingToken}>
                  Create token
                </Button>
              </Group>
            </form>
          </Card>

          <Card withBorder padding="lg" radius="md">
            <Title order={2} c="dimmed" tt="uppercase" mb="sm" style={{ letterSpacing: "0.06em" }}>
              Agent access (MCP)
            </Title>
            <Text size="sm" c="dimmed" mb="md" maw="75ch">
              AI agents such as Claude, Cursor or ChatGPT can connect to the repeater&apos;s MCP server with an API
              token. They can read status, audio levels, settings, activity and logs, and an operator&apos;s token can
              change settings. Every change is in the audit log under the token&apos;s name.
            </Text>
            {agent && !agent.mcp_available ? (
              <Text size="sm" mb="md">
                MCP support isn&apos;t installed on this controller. Update to the latest version to get it.
              </Text>
            ) : null}
            <Stack gap="sm" mb="md">
              <Text size="sm">
                Server URL: <Code>{mcpUrl}</Code>
              </Text>
              <Code block>{mcpConfig}</Code>
            </Stack>
            <Switch
              label="Let API tokens make the repeater transmit"
              description="Sending the ID, dialing DTMF macros, playing announcements, linking, starting nets and phone calls. Off, a token can still change settings but can't key up. You're still the control operator for whatever goes out."
              checked={Boolean(agent?.transmit)}
              onChange={(event) => void setTransmit(event.currentTarget.checked)}
            />
          </Card>
        </>
      ) : null}
    </Page>
  );
}
