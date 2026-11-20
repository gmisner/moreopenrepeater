import { Button, Card, Code, Group, Stack, Text } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { copyToClipboard } from "../clipboard";
import { notifyError, notifySuccess } from "../config";
import { Page } from "../layout/AppShell";

// The Updates page for the container install. The dashboard runs in its own container with no Docker access and no git,
// on purpose, so it can say what is running and whether GitHub has more, but it cannot update anything. There is no
// "Update now" here: the update is a command on the host (docker/update.sh).

export type ContainerInfo = {
  install: "container";
  version: { sha: string; date: string; subject: string } | null;
  container: { branch: string | null; channel: string | null; repo: string | null } | null;
};

type Commit = { sha: string; date: string; subject: string };
type Check = {
  branch?: string;
  latest?: Commit | null;
  relation?: "identical" | "ahead" | "behind" | "diverged" | null;
  new_commits?: Commit[];
  new_commit_count?: number;
  error?: string | null;
};

const CHANNEL_LABELS: Record<string, string> = { stable: "Stable", beta: "Beta", dev: "Dev" };

export const UPDATE_COMMANDS = "cd moreopenrepeater/docker\n./update.sh";
export const SWITCH_COMMANDS = "git checkout stable && ./update.sh";

function short(sha: string | null | undefined) {
  return (sha || "").slice(0, 7);
}

function plural(count: number, word: string) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

async function copyText(text: string) {
  try {
    await copyToClipboard(text);
    notifySuccess("Copied");
  } catch {
    notifyError(new Error("Couldn't copy"));
  }
}

function CopyBlock({ text, label }: { text: string; label: string }) {
  return (
    <Group align="flex-start" wrap="nowrap" gap="sm">
      <Code block style={{ flex: 1 }}>
        {text}
      </Code>
      <Button size="compact-sm" variant="default" aria-label={label} onClick={() => void copyText(text)}>
        Copy
      </Button>
    </Group>
  );
}

function summarize(check: Check | null, branch: string | null, versionKnown: boolean) {
  if (!check) return { title: "Checking…", detail: "" };
  if (check.error) return { title: "Couldn't check for updates", detail: check.error };
  const name = check.branch || branch || "the branch";
  const latest = check.latest ? `Latest on ${name}: ${short(check.latest.sha)}, ${formatTimestamp(check.latest.date)}.` : "";
  if (!versionKnown) {
    return {
      title: "Can't tell if this is up to date",
      detail: `${latest} This install doesn't know its own version yet. Run ./update.sh once and this will say.`.trim(),
    };
  }
  if (check.relation === "identical") return { title: "Up to date", detail: latest };
  if (check.relation === "behind") return { title: `${name} is older than what's running`, detail: latest };
  if (check.relation === "diverged") {
    return { title: `${name} has a different version`, detail: `${latest} It lacks some changes that are running now.`.trim() };
  }
  const count = check.new_commit_count || check.new_commits?.length || 0;
  return { title: `${plural(count, "new change")} on ${name}`, detail: latest };
}

export function ContainerUpdates({ info }: { info: ContainerInfo }) {
  const [check, setCheck] = useState<Check | null>(null);
  const [checking, setChecking] = useState(false);
  const branch = info.container?.branch ?? null;
  const channel = info.container?.channel ?? null;

  const runCheck = useCallback(async (refresh: boolean) => {
    setChecking(true);
    setCheck(null);
    try {
      setCheck(await api<Check>(`/api/updates/check${refresh ? "?refresh=true" : ""}`));
    } catch (error) {
      setCheck({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void runCheck(false);
  }, [runCheck]);

  const version = info.version;
  const { title, detail } = summarize(check, branch, version != null);
  const commits = check?.new_commits ?? [];

  return (
    <Page title="Updates">
      <Card withBorder padding="lg" radius="md" component="section" aria-label="Running version">
        <Stack gap="xs">
          <h2 className="mor-card-title">Running</h2>
          <Group gap="sm" align="baseline">
            <Text size="sm" c="dimmed" w={80}>
              Version
            </Text>
            <Text size="sm">
              {version ? (
                <Code>{short(version.sha)}</Code>
              ) : (
                <>
                  unknown. Run <Code>./update.sh</Code> on the host once and this fills in.
                </>
              )}
            </Text>
          </Group>
          <Group gap="sm" align="baseline">
            <Text size="sm" c="dimmed" w={80}>
              Made
            </Text>
            <Text size="sm">{version?.date ? formatTimestamp(version.date) : "--"}</Text>
          </Group>
          <Group gap="sm" align="baseline">
            <Text size="sm" c="dimmed" w={80}>
              Follows
            </Text>
            <Text size="sm">
              {branch ? (
                <>
                  <Code>{branch}</Code> ({channel ? `${CHANNEL_LABELS[channel] ?? channel} channel` : "not a release channel"})
                </>
              ) : (
                "unknown (the checkout on the host isn't on a branch, or ./update.sh hasn't run)"
              )}
            </Text>
          </Group>
          <Text size="xs" c="dimmed" maw="75ch">
            This is what the checkout on the host was at when <Code>./update.sh</Code> or <Code>setup.sh</Code> last ran. If you updated
            with plain <Code>git</Code> and <Code>docker compose</Code> commands, run <Code>./update.sh</Code> once so it is right.
          </Text>
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="md" component="section" aria-label="Update check">
        <Group justify="space-between" mb="sm">
          <h2 className="mor-card-title">{title}</h2>
          <Button size="compact-sm" variant="subtle" loading={checking} onClick={() => void runCheck(true)}>
            Check again
          </Button>
        </Group>
        {detail ? (
          <Text size="sm" c="dimmed" mb="sm">
            {detail}
          </Text>
        ) : null}
        {commits.length > 0 ? (
          <Stack gap={6}>
            {commits.map((commit) => (
              <Group key={commit.sha} gap="sm" wrap="nowrap">
                <Code>{short(commit.sha)}</Code>
                <Text size="sm" style={{ flex: 1 }}>
                  {commit.subject}
                </Text>
                <Text size="xs" c="dimmed">
                  {formatTimestamp(commit.date)}
                </Text>
              </Group>
            ))}
          </Stack>
        ) : null}
      </Card>

      <Card withBorder padding="lg" radius="md" component="section" aria-label="How to update">
        <Stack gap="sm">
          <h2 className="mor-card-title">How to update</h2>
          <Text size="sm" maw="75ch">
            The dashboard can't update itself here: it runs in its own container with no access to Docker or the host, on purpose. On
            the computer the containers run on, in the docker folder of your checkout:
          </Text>
          <CopyBlock text={UPDATE_COMMANDS} label="Copy the update commands" />
          <Text size="sm" c="dimmed" maw="75ch">
            That pulls the new code (it only ever moves forward, and stops with a plain message if your checkout has changes of its own),
            notes which version it is, then rebuilds and restarts the containers. The repeater is off the air for a minute or two while
            the core restarts. Reload this page afterward.
          </Text>
          <Text size="sm" maw="75ch">
            To follow another channel, switch the checkout&apos;s branch first. Use <Code>beta</Code> or <Code>main</Code> (the dev
            channel) in place of <Code>stable</Code>:
          </Text>
          <CopyBlock text={SWITCH_COMMANDS} label="Copy the channel switch command" />
        </Stack>
      </Card>
    </Page>
  );
}
