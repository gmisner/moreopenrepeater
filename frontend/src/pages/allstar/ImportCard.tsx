import { Alert, Badge, Button, Card, Checkbox, Code, Group, List, Radio, Stack, Text, TextInput } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { ErrorCard, LoadingBlock } from "../../components/StateCards";
import { notifyError, notifySuccess } from "../../config";

export type ImportNode = {
  number: string;
  callsign: string;
  allowed_links: number;
  startup_macro: string;
  has_registration: boolean;
  password_usable: boolean;
  radio: string;
  notes: string[];
  suggested: boolean;
};
type ImportFile = { name: string; status: "read" | "missing" | "unreadable"; detail: string; included: string[] };
type ImportTarget = { state: "new" | "same" | "replace" | "blocked"; existing: string | null; reason: string | null };
export type ImportPreview = {
  configured: boolean;
  available: boolean;
  found: boolean;
  source: string | null;
  files: ImportFile[];
  nodes: ImportNode[];
  problems: string[];
  will_take: string[];
  will_not_take: string[];
  target: Record<string, ImportTarget> | null;
};
type ImportResult = { took: string[]; left: string[]; snapshot: string | null };

/**
 * "Found an existing node configuration": what the old AllStarLink node on this machine has, what importing it takes and
 * what it deliberately leaves, and an explicit button to do it. The API never sends a password (only whether the old node
 * has a registration), so there is none to show here.
 */
export function ImportCard({ onApplied }: { onApplied?: () => void }) {
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [replace, setReplace] = useState(false);
  const [callsign, setCallsign] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  const load = useCallback(async () => {
    try {
      const got = await api<ImportPreview>("/api/allstar/import");
      const next: ImportPreview = { ...got, nodes: got?.nodes ?? [], files: got?.files ?? [], problems: got?.problems ?? [] };
      setPreview(next);
      setError(null);
      setPicked((prev) => {
        if (prev && next.nodes.some((n) => n.number === prev)) return prev;
        return (next.nodes.find((n) => n.suggested) ?? next.nodes[0])?.number ?? "";
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !preview) {
    return (
      <ErrorCard title="Couldn't look for an existing node" onRetry={() => void load()}>
        {error}
      </ErrorCard>
    );
  }
  if (!preview) return <LoadingBlock label="Looking for an existing node" />;
  // Nothing was asked for (no folder named by setup): the page is as it always was.
  if (!preview.configured) return null;

  const trouble = preview.files.filter((f) => f.status === "unreadable" || (f.detail && f.status === "read"));

  if (!preview.found) {
    return (
      <Card withBorder padding="lg" radius="md" component="section" aria-label="Import an existing node" mb="md">
        <Stack gap="sm">
          <h2 className="mor-card-title">Existing node configuration</h2>
          <Alert color="yellow" title={`Nothing to import from ${preview.source ?? "that folder"}`}>
            <Stack gap={4}>
              {preview.problems.map((p) => (
                <Text size="sm" key={p}>
                  {p}
                </Text>
              ))}
              {trouble.map((f) => (
                <Text size="sm" key={f.name}>
                  <Code>{f.name}</Code>: {f.detail}
                </Text>
              ))}
            </Stack>
          </Alert>
          <Button variant="default" size="xs" onClick={() => void load()} style={{ alignSelf: "flex-start" }}>
            Look again
          </Button>
        </Stack>
      </Card>
    );
  }

  const node = preview.nodes.find((n) => n.number === picked) ?? preview.nodes[0];
  const target = preview.target?.[node.number] ?? null;
  const needsCallsign = !node.callsign;
  const needsConfirm = target?.state === "replace";
  const blocked = !preview.available || target?.state === "blocked";
  const ready = !blocked && (!needsCallsign || callsign.trim().length >= 3) && (!needsConfirm || replace);

  const apply = async () => {
    setBusy(true);
    try {
      const done = await api<ImportResult>("/api/allstar/import", {
        method: "POST",
        json: { node: node.number, replace, callsign: needsCallsign ? callsign.trim() : "" },
      });
      setResult(done);
      setReplace(false);
      notifySuccess(`Imported node ${node.number}`);
      onApplied?.();
      await load();
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card withBorder padding="lg" radius="md" component="section" aria-label="Import an existing node" mb="md">
      <Stack gap="md">
        <div>
          <h2 className="mor-card-title">Found an existing node configuration</h2>
          <Text size="sm" c="dimmed" mt={4}>
            {preview.source ? <Code>{preview.source}</Code> : "A folder"} was given to bring a node over from. Nothing is written until you click Import.
          </Text>
        </div>

        {result ? (
          <Alert color="green" title="Imported" role="status">
            <Stack gap={4}>
              {result.took.map((line) => (
                <Text size="sm" key={line}>
                  {line}
                </Text>
              ))}
              {result.snapshot ? (
                <Text size="sm">
                  A copy of every file from before is in <Code>{result.snapshot}</Code> in the data folder's asterisk-backups.
                </Text>
              ) : null}
            </Stack>
          </Alert>
        ) : null}

        {trouble.length > 0 ? (
          <Alert color="yellow" title="Some files couldn't be used">
            <Stack gap={4}>
              {trouble.map((f) => (
                <Text size="sm" key={f.name}>
                  <Code>{f.name}</Code>: {f.detail}
                </Text>
              ))}
            </Stack>
          </Alert>
        ) : null}

        {preview.nodes.length > 1 ? (
          <Radio.Group value={picked} onChange={(value) => { setPicked(value); setReplace(false); }} label="This file has more than one node. Which one is the repeater's?">
            <Stack gap={6} mt={6}>
              {preview.nodes.map((n) => (
                <Radio
                  key={n.number}
                  value={n.number}
                  label={`${n.number}${n.callsign ? ` (${n.callsign})` : ""}${n.suggested ? " · most likely" : ""}`}
                />
              ))}
            </Stack>
          </Radio.Group>
        ) : null}

        <Stack gap={6} aria-label={`Node ${node.number}`}>
          <Row label="Node number" value={<Text ff="monospace" fw={700}>{node.number}</Text>} />
          <Row label="Callsign" value={node.callsign || <Text c="dimmed">none in its ID</Text>} />
          <Row label="Allowed links" value={`${node.allowed_links}`} />
          <Row label="Startup macro" value={node.startup_macro ? <Code>{node.startup_macro}</Code> : <Text c="dimmed">none</Text>} />
          <Row
            label="Registration"
            value={
              node.has_registration ? (
                <Badge color="green" variant="light">
                  {node.password_usable ? "Present" : "Present, can't be carried"}
                </Badge>
              ) : (
                <Badge color="gray" variant="light">
                  Not found
                </Badge>
              )
            }
          />
        </Stack>

        {node.notes.map((note) => (
          <Text size="sm" c="dimmed" key={note}>
            {note}
          </Text>
        ))}

        <Group align="flex-start" grow wrap="wrap">
          <div>
            <Text fw={600} size="sm">
              Will be taken
            </Text>
            <List size="sm" mt={4} spacing={4}>
              {preview.will_take.map((line) => (
                <List.Item key={line}>{line}</List.Item>
              ))}
            </List>
          </div>
          <div>
            <Text fw={600} size="sm">
              Will not be taken
            </Text>
            <List size="sm" mt={4} spacing={4}>
              {node.radio ? (
                <List.Item>
                  Its radio, <Code>{node.radio}</Code>: the radio here is this repeater's controller
                </List.Item>
              ) : null}
              {preview.will_not_take.map((line) => (
                <List.Item key={line}>{line}</List.Item>
              ))}
            </List>
          </div>
        </Group>

        {!preview.available ? (
          <Alert color="yellow" title="The container node isn't ready">
            Start the allstar container so it has its own files, then look again.
          </Alert>
        ) : null}
        {target?.state === "blocked" ? (
          <Alert color="yellow" title="Can't import yet">
            {target.reason}
          </Alert>
        ) : null}
        {target?.state === "same" ? (
          <Text size="sm" c="dimmed">
            Node {node.number} is already set up here. Importing again changes nothing unless the old files changed.
          </Text>
        ) : null}
        {needsConfirm ? (
          <Alert color="orange" title={`Node ${target?.existing} is already set up here`}>
            <Checkbox
              checked={replace}
              onChange={(event) => setReplace(event.currentTarget.checked)}
              label={`Replace node ${target?.existing} with ${node.number}. Its number, callsign and registration change. A copy of every file is kept first.`}
            />
          </Alert>
        ) : null}
        {needsCallsign ? (
          <TextInput
            label="Callsign for the CW ID"
            description="The old node's ID isn't a callsign this can carry over."
            value={callsign}
            onChange={(event) => setCallsign(event.currentTarget.value)}
            maw={240}
          />
        ) : null}

        <Group>
          <Button onClick={() => void apply()} loading={busy} disabled={!ready}>
            {target?.state === "same" ? "Import again" : `Import node ${node.number}`}
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Group justify="space-between" gap="md" wrap="nowrap">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="sm" component="div" ta="right">
        {value}
      </Text>
    </Group>
  );
}
