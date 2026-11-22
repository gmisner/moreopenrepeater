import { Group, Stepper, Text, VisuallyHidden } from "@mantine/core";
import type { ReactNode } from "react";
import { currentStepIndex } from "./stepProgress";

export type SetupStepItem = {
  id: string;
  title: string;
  /** One line on what to do. Shown until the step is done. */
  detail: string;
  done: boolean;
  /** Buttons for this step. They show under the list while this is the current step. */
  actions?: ReactNode;
};

/**
 * A page's setup steps as a Mantine Stepper. The current step comes from what is really done (the page's live status),
 * so there is no Next button. The steps cannot be clicked: some of them happen in a terminal on the repeater's computer,
 * and the page notices when they are finished. Buttons for the current step sit under the list, not inside a step, because a
 * step is itself a button and buttons cannot nest.
 */
export function SetupSteps({ title, intro, steps }: { title: string; intro?: string; steps: SetupStepItem[] }) {
  const active = currentStepIndex(steps);
  const done = steps.filter((step) => step.done).length;
  const current = steps[active];
  return (
    <div style={{ display: "grid", gap: "var(--mantine-spacing-md)" }}>
      <div>
        <h2 className="mor-card-title">{title}</h2>
        <Text size="sm" c="dimmed" mt={4}>
          {done} of {steps.length} done.{intro ? ` ${intro}` : ""}
        </Text>
      </div>
      <Stepper
        active={active}
        orientation="vertical"
        size="sm"
        allowNextStepsSelect={false}
        aria-label={title}
        styles={{ stepDescription: { lineHeight: 1.45, marginTop: 2 } }}
      >
        {steps.map((step, index) => (
          <Stepper.Step
            key={step.id}
            allowStepClick={false}
            aria-current={index === active ? "step" : undefined}
            label={
              <>
                {step.title}
                <VisuallyHidden>{step.done ? ", done. " : ", to do. "}</VisuallyHidden>
              </>
            }
            description={step.done ? undefined : step.detail}
          />
        ))}
      </Stepper>
      {current?.actions ? <Group gap="xs">{current.actions}</Group> : null}
    </div>
  );
}
