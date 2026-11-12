import { Group, Text } from "@mantine/core";
import { useStatusConnected } from "../status";

/** Small live/reconnecting indicator for the sidebar footer. */
export function ConnectionStatus() {
  const connected = useStatusConnected();
  return (
    <Group gap={8} wrap="nowrap" role="status" className="mor-conn" data-status={connected ? "live" : "warn"}>
      <span className="mor-conn-dot" aria-hidden="true" />
      <Text size="sm" c="dimmed">
        {connected ? "Connected" : "Reconnecting…"}
      </Text>
    </Group>
  );
}
