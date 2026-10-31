import { Badge } from "@mantine/core";

export function ConnectionPill({ connected }: { connected: boolean }) {
  return (
    <Badge color={connected ? "green" : "amber"} variant="light" fullWidth>
      {connected ? "live" : "reconnecting…"}
    </Badge>
  );
}
