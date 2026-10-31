import { Badge, Group } from "@mantine/core";
import { Link } from "react-router-dom";
import type { RepeaterStatus } from "../status";

export function MiniStatus({ status }: { status: RepeaterStatus | null }) {
  const ptt = Boolean(status?.ptt_active);
  const cos = Boolean(status?.cos_active);
  return (
    <Link
      to="/dashboard"
      style={{ textDecoration: "none" }}
      aria-label={`Transmitter ${ptt ? "keyed" : "off"}, receiver ${cos ? "hearing a signal" : "quiet"}. Open the dashboard.`}
    >
      <Group gap="xs">
        <Badge variant={ptt ? "filled" : "outline"} color={ptt ? "red" : "gray"} size="sm">
          TX
        </Badge>
        <Badge variant={cos ? "filled" : "outline"} color={cos ? "green" : "gray"} size="sm">
          RX
        </Badge>
      </Group>
    </Link>
  );
}
