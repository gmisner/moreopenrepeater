import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useRef, useState, type FormEvent, type PointerEvent } from "react";
import { api } from "../api";
import { notifyError } from "../config";
import { Page } from "../layout/AppShell";
import { useStatus, type RepeaterStatus } from "../status";

const DTMF_KEYS = ["1", "2", "3", "A", "4", "5", "6", "B", "7", "8", "9", "C", "*", "0", "#", "D"];

async function post(path: string, json: Record<string, unknown>) {
  try {
    await api<RepeaterStatus>(path, { method: "POST", json });
  } catch (error) {
    notifyError(error);
  }
}

export function Simulator() {
  const { status } = useStatus();
  const [ctcss, setCtcss] = useState<number | string>("");
  const [sent, setSent] = useState("");
  const [sequence, setSequence] = useState("");
  const [nodeId, setNodeId] = useState("1999");
  const [holding, setHolding] = useState(false);
  const heldRef = useRef(false);

  const state = status?.state ?? "--";
  const ptt = Boolean(status?.ptt_active);
  const cos = Boolean(status?.cos_active);

  const setCOS = (active: boolean) => post("/api/simulate/cos", { active });

  const sendDigit = async (digit: string) => {
    await post("/api/simulate/dtmf", { digit });
    setSent((prev) => (prev + digit).slice(-24));
  };

  const holdDown = (event: PointerEvent) => {
    event.preventDefault();
    if (heldRef.current) return;
    heldRef.current = true;
    setHolding(true);
    void setCOS(true);
  };

  const holdUp = () => {
    if (!heldRef.current) return;
    heldRef.current = false;
    setHolding(false);
    void setCOS(false);
  };

  const sendSequence = async (event: FormEvent) => {
    event.preventDefault();
    const digits = sequence.trim().toUpperCase();
    for (const digit of digits) await sendDigit(digit);
    setSequence("");
  };

  return (
    <Page title="Simulator" description="Inject synthetic hardware events — useful until real audio_io/link hardware is wired up.">
      <Card withBorder padding="lg" radius="md">
        <Group gap="sm">
          <Text size="xs" c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            State
          </Text>
          <Badge size="lg" variant="light">
            {String(state).replaceAll("_", " ")}
          </Badge>
          <Badge variant={ptt ? "filled" : "outline"} color={ptt ? "red" : "gray"}>
            PTT
          </Badge>
          <Badge variant={cos ? "filled" : "outline"} color={cos ? "green" : "gray"}>
            COS
          </Badge>
        </Group>
      </Card>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Title order={2}>Carrier (COS)</Title>
            <Group>
              <Button onClick={() => void setCOS(true)}>Key up</Button>
              <Button variant="default" onClick={() => void setCOS(false)}>
                Unkey
              </Button>
            </Group>
            <Text size="sm" c="dimmed">
              Or hold to talk:
            </Text>
            <Button
              size="lg"
              color={holding ? "red" : "gray"}
              variant={holding ? "filled" : "light"}
              onPointerDown={holdDown}
              onPointerUp={holdUp}
              onPointerLeave={holdUp}
              onPointerCancel={holdUp}
              style={{ userSelect: "none", touchAction: "none" }}
            >
              Hold to key
            </Button>
          </Stack>
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Title order={2}>CTCSS tone</Title>
            <Group align="flex-end">
              <NumberInput
                label="Tone"
                suffix=" Hz"
                placeholder="100.0"
                min={0}
                step={0.1}
                decimalScale={1}
                value={ctcss === "" ? undefined : Number(ctcss)}
                onChange={(value) => setCtcss(value === "" || value === undefined ? "" : Number(value))}
                style={{ flex: 1 }}
              />
              <Button
                onClick={() => {
                  const value = typeof ctcss === "number" ? ctcss : parseFloat(String(ctcss));
                  if (!Number.isNaN(value)) void post("/api/simulate/ctcss", { tone_hz: value });
                }}
              >
                Apply
              </Button>
              <Button variant="default" onClick={() => void post("/api/simulate/ctcss", { tone_hz: null })}>
                Clear
              </Button>
            </Group>
          </Stack>
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Title order={2}>DTMF</Title>
            <SimpleGrid cols={4} spacing="xs">
              {DTMF_KEYS.map((digit) => (
                <Button key={digit} variant="default" onClick={() => void sendDigit(digit)}>
                  {digit}
                </Button>
              ))}
            </SimpleGrid>
            <form onSubmit={(event) => void sendSequence(event)}>
              <Stack gap="sm">
                <TextInput
                  label="Send a sequence"
                  placeholder="*81"
                  value={sequence}
                  onChange={(event) => setSequence(event.currentTarget.value)}
                  pattern="[0-9A-Da-d*#]+"
                  required
                />
                <Group>
                  <Button type="submit">Send</Button>
                  <Text size="sm" c="dimmed">
                    Sent: <Text span ff="monospace">{sent || "—"}</Text>
                  </Text>
                </Group>
              </Stack>
            </form>
          </Stack>
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Title order={2}>Remote node</Title>
            <Group align="flex-end">
              <TextInput
                label="Node id"
                placeholder="node id"
                value={nodeId}
                onChange={(event) => setNodeId(event.currentTarget.value)}
                style={{ flex: 1 }}
              />
              <Button
                onClick={() =>
                  void post("/api/simulate/remote-keyed", {
                    node_id: nodeId.trim() || "unknown",
                    keyed: true,
                  })
                }
              >
                Key up
              </Button>
              <Button
                variant="default"
                onClick={() =>
                  void post("/api/simulate/remote-keyed", {
                    node_id: nodeId.trim() || "unknown",
                    keyed: false,
                  })
                }
              >
                Unkey
              </Button>
            </Group>
          </Stack>
        </Card>
      </SimpleGrid>
    </Page>
  );
}
