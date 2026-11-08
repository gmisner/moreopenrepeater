import { Tooltip, UnstyledButton } from "@mantine/core";
import { useId, type ReactNode } from "react";
import { Icon } from "../icons/Icon";

/** Label + (i) tip — same idea as vanilla `web/js/hints.js`. */
export function FieldHint({ label, hint }: { label: ReactNode; hint: ReactNode }) {
  const about = typeof label === "string" ? label : "this field";
  const descId = useId();
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span>{label}</span>
      <Tooltip label={hint} multiline maw={320} withArrow events={{ hover: true, focus: true, touch: true }}>
        <UnstyledButton
          type="button"
          aria-label={`About ${about}`}
          aria-describedby={descId}
          style={{
            display: "inline-flex",
            color: "var(--mor-muted, var(--mantine-color-dimmed))",
            lineHeight: 0,
          }}
        >
          <Icon name="info" size={15} />
        </UnstyledButton>
      </Tooltip>
      <span id={descId} hidden>
        {hint}
      </span>
    </span>
  );
}

/** Drop-in for Mantine `label` / `description` pairs. */
export function labeled(label: string, hint?: ReactNode): ReactNode {
  if (hint === undefined || hint === null || hint === "") return label;
  return <FieldHint label={label} hint={hint} />;
}

/** A lone explanation that has no label to hang off: a quiet "How this works" trigger. */
export function InfoNote({ children, label = "How this works" }: { children: ReactNode; label?: string }) {
  const descId = useId();
  return (
    <>
      <Tooltip label={children} multiline maw={340} withArrow events={{ hover: true, focus: true, touch: true }}>
        <UnstyledButton type="button" className="mor-infonote" aria-describedby={descId}>
          <Icon name="info" size={15} />
          <span>{label}</span>
        </UnstyledButton>
      </Tooltip>
      <span id={descId} hidden>
        {children}
      </span>
    </>
  );
}
