import { Button } from "@mantine/core";
import { SetupSteps } from "../../components/SetupSteps";
import type { SetupStep } from "./model";

export function SetupChecklist({
  steps,
  canAct,
  onAction,
  onShowSteps,
}: {
  steps: SetupStep[];
  canAct: (action: "node" | "favorites") => boolean;
  onAction: (action: "node" | "favorites") => void;
  /** Opens the page's "How to set this up" section. */
  onShowSteps?: () => void;
}) {
  return (
    <SetupSteps
      title="Get AllStarLink running"
      intro="Links, favorites and schedules show up here once the node is set up."
      steps={steps.map((step) => ({
        id: step.id,
        title: step.title,
        detail: step.detail,
        done: step.done,
        actions: (
          <>
            {step.id === "install" && onShowSteps ? <Button size="xs" onClick={onShowSteps}>Show the steps</Button> : null}
            {step.action && canAct(step.action) ? (
              <Button size="xs" onClick={() => onAction(step.action!)}>
                {step.action === "node" ? "Set up node" : "Add favorites"}
              </Button>
            ) : null}
          </>
        ),
      }))}
    />
  );
}
