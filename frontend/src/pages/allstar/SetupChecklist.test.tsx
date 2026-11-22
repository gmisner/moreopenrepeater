import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SetupChecklist } from "./SetupChecklist";
import type { SetupStep } from "./model";

const steps = (done: boolean[]): SetupStep[] => [
  { id: "install", title: "Install AllStarLink on this machine", detail: "Not installed.", done: done[0] },
  { id: "node", title: "Register your node", detail: "Pick a node.", done: done[1], action: "node" },
  { id: "favorite", title: "Add a favorite node", detail: "One-click buttons.", done: done[2], action: "favorites" },
];

function show(done: boolean[], canAct = () => true) {
  const onAction = vi.fn();
  const onShowSteps = vi.fn();
  render(
    <MantineProvider>
      <SetupChecklist steps={steps(done)} canAct={canAct} onAction={onAction} onShowSteps={onShowSteps} />
    </MantineProvider>,
  );
  return { onAction, onShowSteps };
}

describe("AllStar SetupChecklist", () => {
  it("offers the install steps while AllStarLink is not installed", async () => {
    const { onShowSteps } = show([false, false, false]);
    expect(screen.getByText(/0 of 3 done/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show the steps" }));
    expect(onShowSteps).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Set up node" })).not.toBeInTheDocument();
  });

  it("moves on to registering the node once it is installed", async () => {
    const { onAction } = show([true, false, false]);
    expect(screen.getByText(/1 of 3 done/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show the steps" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Set up node" }));
    expect(onAction).toHaveBeenCalledWith("node");
  });

  it("hides a button the person is not allowed to use", () => {
    show([true, false, false], () => false);
    expect(screen.queryByRole("button", { name: "Set up node" })).not.toBeInTheDocument();
  });

  it("asks for a favorite last", async () => {
    const { onAction } = show([true, true, false]);
    await userEvent.click(screen.getByRole("button", { name: "Add favorites" }));
    expect(onAction).toHaveBeenCalledWith("favorites");
  });
});
