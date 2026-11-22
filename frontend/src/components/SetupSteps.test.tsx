import { Button, MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SetupSteps, type SetupStepItem } from "./SetupSteps";
import { currentStepIndex } from "./stepProgress";

function steps(done: boolean[]): SetupStepItem[] {
  return done.map((isDone, i) => ({
    id: `s${i}`,
    title: `Step ${i + 1}`,
    detail: `Detail ${i + 1}`,
    done: isDone,
    actions: <Button>{`Act ${i + 1}`}</Button>,
  }));
}

function show(done: boolean[]) {
  return render(
    <MantineProvider>
      <SetupSteps title="Get it running" intro="More shows up after." steps={steps(done)} />
    </MantineProvider>,
  );
}

describe("currentStepIndex", () => {
  it("is the first step that is not done", () => {
    expect(currentStepIndex([{ done: false }, { done: false }])).toBe(0);
    expect(currentStepIndex([{ done: true }, { done: false }, { done: false }])).toBe(1);
  });

  it("is the length when everything is done, and when there are no steps", () => {
    expect(currentStepIndex([{ done: true }, { done: true }])).toBe(2);
    expect(currentStepIndex([])).toBe(0);
  });

  it("stops at the first gap even if a later step is already done", () => {
    expect(currentStepIndex([{ done: true }, { done: false }, { done: true }])).toBe(1);
  });
});

describe("SetupSteps", () => {
  it("counts what is done and marks the first undone step as current", () => {
    show([true, false, false]);
    expect(screen.getByText("Get it running")).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 done\. More shows up after\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Step 1, done/ })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: /Step 2, to do/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: /Step 3, to do/ })).not.toHaveAttribute("aria-current");
  });

  it("says what to do for steps that are not done, and drops that text once they are", () => {
    show([true, false]);
    expect(screen.queryByText("Detail 1")).not.toBeInTheDocument();
    expect(screen.getByText("Detail 2")).toBeInTheDocument();
  });

  it("shows buttons for the current step only", () => {
    show([true, false, false]);
    expect(screen.getByRole("button", { name: "Act 2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Act 1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Act 3" })).not.toBeInTheDocument();
  });

  it("has no current step and no buttons when everything is done", () => {
    show([true, true]);
    expect(screen.getByText(/2 of 2 done/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Act/ })).not.toBeInTheDocument();
    for (const step of screen.getAllByRole("button")) expect(step).not.toHaveAttribute("aria-current");
  });

  it("does not let a step be clicked, since the page follows what is really done", () => {
    show([false, false]);
    for (const step of screen.getAllByRole("button", { name: /Step \d/ })) expect(step).toHaveAttribute("tabindex", "-1");
  });
});
