import { afterEach, describe, expect, it, vi } from "vitest";
import { copyToClipboard } from "./clipboard";

describe("copyToClipboard", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses the Clipboard API when writeText succeeds", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn(),
    });

    await copyToClipboard("https://example.ts.net/");

    expect(writeText).toHaveBeenCalledWith("https://example.ts.net/");
    expect(document.execCommand).not.toHaveBeenCalled();
  });

  it("falls back to execCommand when Clipboard API throws", async () => {
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: vi.fn().mockRejectedValue(new Error("Document is not focused.")),
      },
    });
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    await copyToClipboard("token-value");

    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("falls back when clipboard is missing", async () => {
    vi.stubGlobal("navigator", {});
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    await copyToClipboard("plain");

    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("throws when both paths fail", async () => {
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: vi.fn().mockRejectedValue(new Error("blocked")),
      },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn().mockReturnValue(false),
    });

    await expect(copyToClipboard("x")).rejects.toThrow("Couldn't copy");
  });
});
