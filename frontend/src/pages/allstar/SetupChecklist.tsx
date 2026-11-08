import { Button, Code, Text, VisuallyHidden } from "@mantine/core";
import type { SetupStep } from "./model";
import "./allstar.css";

export function SetupChecklist({
  steps,
  canAct,
  onAction,
}: {
  steps: SetupStep[];
  canAct: (action: "node" | "favorites") => boolean;
  onAction: (action: "node" | "favorites") => void;
}) {
  const done = steps.filter((s) => s.done).length;
  const current = steps.find((s) => !s.done)?.id;
  return (
    <div className="mor-as" style={{ display: "grid", gap: "var(--mantine-spacing-md)" }}>
      <div>
        <h2 className="mor-card-title">Get AllStarLink running</h2>
        <Text size="sm" c="dimmed" mt={4}>
          {done} of {steps.length} done. Links, favorites and schedules show up here once the node is set up.
        </Text>
      </div>
      <div className="mor-progress" aria-hidden="true">
        <span style={{ ["--fill" as string]: `${(done / steps.length) * 100}%` }} />
      </div>
      <ol className="mor-steps">
        {steps.map((step, i) => (
          <li
            key={step.id}
            className="mor-step"
            data-done={step.done ? "" : undefined}
            data-current={step.id === current ? "" : undefined}
          >
            <span className="mor-step-mark" aria-hidden="true">
              {step.done ? "✓" : i + 1}
            </span>
            <div>
              <div className="mor-step-title">
                {step.title}
                <VisuallyHidden> ({step.done ? "done" : "to do"})</VisuallyHidden>
              </div>
              {!step.done ? <p className="mor-step-detail">{step.detail}</p> : null}
              {!step.done && step.id === "install" ? (
                <p className="mor-step-detail">
                  <Code>sudo scripts/install-pi.sh --allstar</Code>
                </p>
              ) : null}
            </div>
            {!step.done && step.action && canAct(step.action) ? (
              <Button size="xs" variant={step.id === current ? "filled" : "default"} onClick={() => onAction(step.action!)}>
                {step.action === "node" ? "Set up node" : "Add favorites"}
              </Button>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
