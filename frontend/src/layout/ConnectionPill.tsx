import { Badge } from "@mantine/core";

export function ConnectionPill({ connected }: { connected: boolean }) {
  return (
    <Badge
      className="mor-status-pill"
      data-status={connected ? "live" : "warn"}
      variant="filled"
      fullWidth
      tt="uppercase"
      style={{ letterSpacing: "0.04em" }}
    >
      {connected ? "live" : "reconnecting…"}
    </Badge>
  );
}
