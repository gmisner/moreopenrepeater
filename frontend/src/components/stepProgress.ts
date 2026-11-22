/** The first step that is not done. Everything before it shows as passed, and `steps.length` means all done. */
export function currentStepIndex(steps: readonly { done: boolean }[]): number {
  const index = steps.findIndex((step) => !step.done);
  return index === -1 ? steps.length : index;
}
