import { Group, Text } from "@mantine/core";

/** Small live/reconnecting indicator for the sidebar footer. */
export function ConnectionStatus({ connected }: { connected: boolean }) {
  return (
    <Group gap={8} wrap="nowrap" role="status" className="mor-conn" data-status={connected ? "live" : "warn"}>
      <span className="mor-conn-dot" aria-hidden="true" />
      <Text size="sm" c="dimmed">
        {connected ? "Connected" : "Reconnecting…"}
      </Text>
    </Group>
  );
}
