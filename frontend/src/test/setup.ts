import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest does not expose Jest's global afterEach unless globals:true, so RTL
// auto-cleanup never registers. Clean the DOM between files explicitly.
afterEach(() => {
  cleanup();
});
