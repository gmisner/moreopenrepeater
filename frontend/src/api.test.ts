import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiBlob } from "./api";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api", () => {
  const fetchMock = vi.fn();
  const replaceMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    replaceMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("location", { replace: replaceMock });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns parsed JSON on success", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { callsign: "W1AW" }));
    await expect(api("/api/config")).resolves.toEqual({ callsign: "W1AW" });
    expect(fetchMock).toHaveBeenCalledWith("/api/config", expect.objectContaining({ method: "GET" }));
  });

  it("sends JSON body with Content-Type", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    await api("/api/config", { method: "PUT", json: { callsign: "N0CALL" } });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/config",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ callsign: "N0CALL" }),
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
  });

  it("surfaces FastAPI string detail as ApiError message", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(400, { detail: "bad radio" }));
    const error = await api("/api/config").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, message: "bad radio" });
  });

  it("formats validation detail arrays", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, {
        detail: [
          { loc: ["body", "callsign"], msg: "required" },
          { loc: ["body", "timeout"], msg: "too small" },
        ],
      }),
    );
    await expect(api("/api/config")).rejects.toMatchObject({
      message: "callsign: required; timeout: too small",
    });
  });

  it("falls back when error payload has no detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));
    await expect(api("/api/config")).rejects.toMatchObject({
      message: "Request failed (500)",
    });
  });

  it("redirects to login on 401 except for /api/login", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    await expect(api("/api/config")).rejects.toMatchObject({
      status: 401,
      message: "Your session has expired",
    });
    expect(replaceMock).toHaveBeenCalledWith("/login");

    replaceMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "bad password" }));
    await expect(api("/api/login", { method: "POST", json: {} })).rejects.toMatchObject({
      status: 401,
      message: "bad password",
    });
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("returns a blob via apiBlob", async () => {
    const blob = new Blob(["zip"]);
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      blob: async () => blob,
    });
    await expect(apiBlob("/api/backup")).resolves.toBe(blob);
  });
});
