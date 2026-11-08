import { useSyncExternalStore } from "react";

/** Whether the last request to the controller got any answer at all. Pages and banners share it. */
let reachable = true;
const listeners = new Set<() => void>();

export function setReachable(next: boolean) {
  if (next === reachable) return;
  reachable = next;
  listeners.forEach((listener) => listener());
}

export function isReachable() {
  return reachable;
}

export function useReachable() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => reachable,
    () => true,
  );
}
