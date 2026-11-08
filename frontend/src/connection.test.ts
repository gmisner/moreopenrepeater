import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { isReachable, setReachable } from "./connection";

afterEach(() => {
  vi.unstubAllGlobals();
  setReachable(true);
});

describe("reachability", () => {
  it("goes unreachable when the network drops and recovers on the next answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/api/config")).rejects.toThrow();
    expect(isReachable()).toBe(false);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    await api("/api/config");
    expect(isReachable()).toBe(true);
  });

  it("stays reachable when the controller answers with an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"detail":"nope"}', { status: 500 })));
    await expect(api("/api/config")).rejects.toThrow("nope");
    expect(isReachable()).toBe(true);
  });
});
