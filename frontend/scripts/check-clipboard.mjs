#!/usr/bin/env node
/**
 * Clipboard API rejects outside secure contexts (http://pi.local). Copy must
 * fall back to execCommand so Tailscale / token Copy buttons still work.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(join(root, "src/clipboard.ts"), "utf8");

if (!source.includes("execCommand")) {
  console.error("FAIL: clipboard.ts missing execCommand fallback");
  process.exit(1);
}
if (!source.includes("navigator.clipboard")) {
  console.error("FAIL: clipboard.ts missing Clipboard API path");
  process.exit(1);
}

// Runtime shape of the fallback (mirrors clipboard.ts).
async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    /* fall through */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  document.body.appendChild(area);
  area.focus();
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } finally {
    area.remove();
  }
  if (!ok) throw new Error("Couldn't copy");
}

const removed = [];
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    clipboard: {
      writeText: async () => {
        throw new Error("Document is not focused.");
      },
    },
  },
});
Object.defineProperty(globalThis, "document", {
  configurable: true,
  value: {
    createElement(tag) {
      if (tag !== "textarea") throw new Error(tag);
      return {
        value: "",
        style: {},
        setAttribute() {},
        focus() {},
        select() {},
        setSelectionRange() {},
        remove() {
          removed.push("remove");
        },
      };
    },
    body: { appendChild() {} },
    execCommand(cmd) {
      return cmd === "copy";
    },
  },
});

await copyToClipboard("https://example.ts.net/");
if (!removed.includes("remove")) {
  console.error("FAIL: fallback did not clean up textarea");
  process.exit(1);
}

globalThis.document.execCommand = () => false;
let threw = false;
try {
  await copyToClipboard("x");
} catch (e) {
  threw = e instanceof Error && e.message === "Couldn't copy";
}
if (!threw) {
  console.error("FAIL: expected Couldn't copy when both paths fail");
  process.exit(1);
}

for (const file of ["src/pages/Remote.tsx", "src/pages/Users.tsx"]) {
  const page = readFileSync(join(root, file), "utf8");
  if (!page.includes("copyToClipboard")) {
    console.error(`FAIL: ${file} should use copyToClipboard`);
    process.exit(1);
  }
  if (page.includes("navigator.clipboard.writeText")) {
    console.error(`FAIL: ${file} still calls navigator.clipboard directly`);
    process.exit(1);
  }
}

console.log("OK clipboard fallback");
