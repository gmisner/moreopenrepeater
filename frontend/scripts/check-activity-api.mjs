#!/usr/bin/env node
/**
 * Activity must call the real endpoints (vanilla usage.js contract).
 * `/api/usage` 404s with detail "Not Found" and blanks the page toast.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(join(root, "src/pages/Activity.tsx"), "utf8");

if (source.includes("/api/usage")) {
  console.error("FAIL: Activity.tsx still calls /api/usage (404 Not Found)");
  process.exit(1);
}
for (const path of ["/api/activity/summary", "/api/activity/transmissions"]) {
  if (!source.includes(path)) {
    console.error(`FAIL: Activity.tsx missing ${path}`);
    process.exit(1);
  }
}
console.log("OK activity API paths");
