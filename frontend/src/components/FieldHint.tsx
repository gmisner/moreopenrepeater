import { Tooltip, UnstyledButton } from "@mantine/core";
import type { ReactNode } from "react";
import { Icon } from "../icons/Icon";

/** Label + (i) tip — same idea as vanilla `web/js/hints.js`. */
export function FieldHint({ label, hint }: { label: ReactNode; hint: ReactNode }) {
  const about = typeof label === "string" ? label : "this field";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span>{label}</span>
      <Tooltip label={hint} multiline maw={320} withArrow events={{ hover: true, focus: true, touch: true }}>
        <UnstyledButton
          type="button"
          aria-label={`About ${about}`}
          style={{
            display: "inline-flex",
            color: "var(--mor-muted, var(--mantine-color-dimmed))",
            lineHeight: 0,
          }}
        >
          <Icon name="info" size={15} />
        </UnstyledButton>
      </Tooltip>
    </span>
  );
}

/** Drop-in for Mantine `label` / `description` pairs. */
export function labeled(label: string, hint?: ReactNode): ReactNode {
  if (hint === undefined || hint === null || hint === "") return label;
  return <FieldHint label={label} hint={hint} />;
}
