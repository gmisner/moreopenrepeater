import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { Fragment, type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { notifyError, notifySuccess, useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { useSession } from "../session";

type MailboxBox = {
  box: string;
  name: string;
  messages: number;
  pin_set: boolean;
};

type MailboxMessage = {
  id: string;
  box: string;
  left_at: string;
  duration: number;
  transcript?: string | null;
  callsigns?: string[];
};

type MailboxData = {
  boxes: MailboxBox[];
  messages: MailboxMessage[];
};

type MailboxValues = {
  mailbox_enabled: boolean;
  mailbox_leave_code: string;
  mailbox_play_code: string;
  mailbox_delete_code: string;
  mailbox_max_seconds: number;
  mailbox_retention_days: number;
  mailbox_reminder_minutes: number;
};

const KEYS = [
  "mailbox_enabled",
  "mailbox_leave_code",
  "mailbox_play_code",
  "mailbox_delete_code",
  "mailbox_max_seconds",
  "mailbox_retention_days",
  "mailbox_reminder_minutes",
] as (keyof MailboxValues)[];

const DEFAULTS: MailboxValues = {
  mailbox_enabled: false,
  mailbox_leave_code: "*7",
  mailbox_play_code: "*8",
  mailbox_delete_code: "*9",
  mailbox_max_seconds: 60,
  mailbox_retention_days: 14,
  mailbox_reminder_minutes: 60,
};

function formatTimestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatLength(seconds: number) {
  return seconds < 60 ? `${Math.round(seconds)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function Mailbox() {
  const { session } = useSession();
  const isAdmin = session?.role === "admin";
  const { values, setField, dirty, reset, submit, saving } = useConfigSlice<MailboxValues>(KEYS, DEFAULTS);

  const [data, setData] = useState<MailboxData>({ boxes: [], messages: [] });
  const [editing, setEditing] = useState<string | null>(null);
  const [boxNumber, setBoxNumber] = useState("");
  const [boxName, setBoxName] = useState("");
  const [boxPin, setBoxPin] = useState("");
  const [pinNote, setPinNote] = useState("4 to 8 digits.");
  const [boxSaving, setBoxSaving] = useState(false);

  const load = useCallback(async () => {
    if (!isAdmin) return;
    setData(await api<MailboxData>("/api/mailbox"));
  }, [isAdmin]);

  useEffect(() => {
    load().catch(notifyError);
  }, [load]);

  const stopEdit = () => {
    setEditing(null);
    setBoxNumber("");
    setBoxName("");
    setBoxPin("");
    setPinNote("4 to 8 digits.");
  };

  const startEdit = (box: MailboxBox) => {
    setEditing(box.box);
    setBoxNumber(box.box);
    setBoxName(box.name);
    setBoxPin("");
    setPinNote(box.pin_set ? "Leave blank to keep the current PIN." : "4 to 8 digits.");
  };

  const saveBox = async (event: FormEvent) => {
    event.preventDefault();
    const box = boxNumber.trim();
    const pin = boxPin.trim();
    if (!editing && !pin) {
      notifyError(new Error("A new mailbox needs a PIN"));
      return;
    }
    const json: { name: string; pin?: string } = { name: boxName.trim() };
    if (pin) json.pin = pin;
    setBoxSaving(true);
    try {
      setData(await api<MailboxData>(`/api/mailbox/boxes/${encodeURIComponent(box)}`, { method: "PUT", json }));
      notifySuccess(editing ? `Mailbox ${box} saved` : `Mailbox ${box} added`);
      stopEdit();
    } catch (error) {
      notifyError(error);
    } finally {
      setBoxSaving(false);
    }
  };

  const removeBox = async (box: MailboxBox) => {
    const lost = box.messages ? ` Its ${box.messages === 1 ? "message goes" : `${box.messages} messages go`} too.` : "";
    if (!window.confirm(`Delete mailbox ${box.box}?${lost}`)) return;
    try {
      setData(await api<MailboxData>(`/api/mailbox/boxes/${encodeURIComponent(box.box)}`, { method: "DELETE" }));
      if (editing === box.box) stopEdit();
      notifySuccess(`Mailbox ${box.box} deleted`);
    } catch (error) {
      notifyError(error);
    }
  };

  const removeMessage = async (message: MailboxMessage) => {
    if (!window.confirm(`Delete the message for mailbox ${message.box} from ${formatTimestamp(message.left_at)}?`)) {
      return;
    }
    try {
      setData(await api<MailboxData>(`/api/mailbox/messages/${encodeURIComponent(message.id)}`, { method: "DELETE" }));
      notifySuccess("Message deleted");
    } catch (error) {
      notifyError(error);
    }
  };

  const names = Object.fromEntries(data.boxes.map((b) => [b.box, b.name]));

  return (
    <Page
      title="Mailbox"
      description="Leave a short voice message for another station, over the air. They hear a reminder that messages are waiting, and play them back with their PIN."
    >
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Stack gap="md">
          <Switch
            label="Turn on the mailbox"
            checked={values.mailbox_enabled}
            onChange={(event) => setField("mailbox_enabled", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Leave a message"
              description="Then the mailbox number and #. Key up again to record."
              required
              maxLength={8}
              pattern="[0-9A-D*]{1,8}"
              value={values.mailbox_leave_code}
              onChange={(event) => setField("mailbox_leave_code", event.currentTarget.value)}
            />
            <TextInput
              label="Play messages"
              description="Then the mailbox number, *, the PIN and #."
              required
              maxLength={8}
              pattern="[0-9A-D*]{1,8}"
              value={values.mailbox_play_code}
              onChange={(event) => setField("mailbox_play_code", event.currentTarget.value)}
            />
            <TextInput
              label="Delete messages"
              description="Then the mailbox number, *, the PIN and #."
              required
              maxLength={8}
              pattern="[0-9A-D*]{1,8}"
              value={values.mailbox_delete_code}
              onChange={(event) => setField("mailbox_delete_code", event.currentTarget.value)}
            />
            <NumberInput
              label="Longest message"
              suffix=" seconds"
              min={5}
              max={180}
              step={5}
              required
              value={values.mailbox_max_seconds}
              onChange={(value) => setField("mailbox_max_seconds", Number(value) || 0)}
            />
            <NumberInput
              label="Keep messages for"
              suffix=" days"
              min={1}
              max={365}
              required
              value={values.mailbox_retention_days}
              onChange={(value) => setField("mailbox_retention_days", Number(value) || 0)}
            />
            <NumberInput
              label="Remind every"
              description={'"Messages waiting for mailbox 1 2." 0 turns reminders off. Skipped during a net.'}
              suffix=" minutes"
              min={0}
              max={1440}
              step={5}
              required
              value={values.mailbox_reminder_minutes}
              onChange={(value) => setField("mailbox_reminder_minutes", Number(value) || 0)}
            />
          </SimpleGrid>
          <Text c="dimmed" size="sm" maw="75ch">
            Messages need live audio. Anyone listening hears a PIN when it&apos;s keyed, so a mailbox is for convenience, not secrets. Five wrong PINs lock a mailbox for ten minutes.
          </Text>
          <ConfigActions dirty={dirty} saving={saving} onReset={reset} />
        </Stack>
      </Card>

      {isAdmin ? (
        <>
          <Card withBorder padding="lg" radius="md">
            <Stack gap="md">
              <Title order={2}>Mailboxes</Title>
              <Table.ScrollContainer minWidth={480}>
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Number</Table.Th>
                      <Table.Th>Whose</Table.Th>
                      <Table.Th>Messages</Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.boxes.map((box) => (
                      <Table.Tr key={box.box}>
                        <Table.Td>
                          <Group gap="xs">
                            {box.box}
                            {!box.pin_set ? (
                              <Badge color="red" size="sm" variant="light">
                                no PIN
                              </Badge>
                            ) : null}
                          </Group>
                        </Table.Td>
                        <Table.Td>{box.name}</Table.Td>
                        <Table.Td>{box.messages}</Table.Td>
                        <Table.Td>
                          <Group gap="xs" justify="flex-end" wrap="nowrap">
                            <Button size="compact-sm" variant="subtle" onClick={() => startEdit(box)}>
                              Edit
                            </Button>
                            <Button size="compact-sm" color="red" variant="subtle" onClick={() => void removeBox(box)}>
                              Delete
                            </Button>
                          </Group>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
              {data.boxes.length === 0 ? (
                <Text c="dimmed" size="sm">
                  No mailboxes yet.
                </Text>
              ) : null}

              <Title order={3}>{editing ? `Change mailbox ${editing}` : "Add a mailbox"}</Title>
              <form onSubmit={(event) => void saveBox(event)}>
                <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
                  <TextInput
                    label="Number"
                    required
                    inputMode="numeric"
                    maxLength={6}
                    pattern="[0-9]{1,6}"
                    placeholder="12"
                    readOnly={Boolean(editing)}
                    value={boxNumber}
                    onChange={(event) => setBoxNumber(event.currentTarget.value)}
                  />
                  <TextInput
                    label="Whose"
                    maxLength={60}
                    placeholder="W1AW"
                    value={boxName}
                    onChange={(event) => setBoxName(event.currentTarget.value)}
                  />
                  <TextInput
                    label="PIN"
                    description={pinNote}
                    inputMode="numeric"
                    maxLength={8}
                    pattern="[0-9]{4,8}"
                    autoComplete="off"
                    placeholder="4 to 8 digits"
                    value={boxPin}
                    onChange={(event) => setBoxPin(event.currentTarget.value)}
                  />
                </SimpleGrid>
                <Group justify="flex-end" mt="md" gap="sm">
                  {editing ? (
                    <Button type="button" variant="subtle" onClick={stopEdit}>
                      Cancel edit
                    </Button>
                  ) : null}
                  <Button type="submit" loading={boxSaving}>
                    {editing ? "Save mailbox" : "Add mailbox"}
                  </Button>
                </Group>
              </form>
            </Stack>
          </Card>

          <Card withBorder padding="lg" radius="md">
            <Title order={2} mb="md">
              Messages
            </Title>
            <Table.ScrollContainer minWidth={640}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Mailbox</Table.Th>
                    <Table.Th>Left</Table.Th>
                    <Table.Th>Length</Table.Th>
                    <Table.Th>Listen</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.messages.map((message) => (
                    <Fragment key={message.id}>
                      <Table.Tr>
                        <Table.Td>
                          {message.box}
                          {names[message.box] ? (
                            <Text span c="dimmed" size="sm" ml={6}>
                              {names[message.box]}
                            </Text>
                          ) : null}
                        </Table.Td>
                        <Table.Td>{formatTimestamp(message.left_at)}</Table.Td>
                        <Table.Td>{formatLength(message.duration)}</Table.Td>
                        <Table.Td>
                          <audio controls preload="none" src={`/api/mailbox/messages/${encodeURIComponent(message.id)}/audio`} />
                        </Table.Td>
                        <Table.Td>
                          <Button size="compact-sm" color="red" variant="subtle" onClick={() => void removeMessage(message)}>
                            Delete
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                      {message.transcript != null ? (
                        <Table.Tr>
                          <Table.Td colSpan={5}>
                            <Group gap="xs">
                              {(message.callsigns ?? []).map((c) => (
                                <Badge key={c} size="sm" variant="light">
                                  {c}
                                </Badge>
                              ))}
                              <Text size="sm" c={message.transcript ? undefined : "dimmed"}>
                                {message.transcript || "Nothing understood."}
                              </Text>
                            </Group>
                          </Table.Td>
                        </Table.Tr>
                      ) : null}
                    </Fragment>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {data.messages.length === 0 ? (
              <Text c="dimmed" size="sm" mt="sm">
                No messages waiting.
              </Text>
            ) : null}
          </Card>
        </>
      ) : null}
    </Page>
  );
}
