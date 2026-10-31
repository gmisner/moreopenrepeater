export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function describeError(status: number, payload: unknown): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const loc = (d.loc || []).filter((p) => p !== "body").join(".");
        return `${loc}: ${d.msg}`;
      })
      .join("; ");
  }
  return `Request failed (${status})`;
}

async function request(path: string, { method = "GET", json, body }: { method?: string; json?: unknown; body?: BodyInit } = {}) {
  const init: RequestInit = { method, headers: {} };
  if (json !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (body !== undefined) {
    init.body = body;
  }
  const response = await fetch(path, init);
  if (response.status === 401 && path !== "/api/login") {
    location.replace("/login");
    throw new ApiError(401, "Your session has expired");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(response.status, describeError(response.status, payload));
  }
  return response;
}

export async function api<T = unknown>(path: string, options?: Parameters<typeof request>[1]): Promise<T> {
  const response = await request(path, options);
  return response.json().catch(() => null) as Promise<T>;
}

export async function apiBlob(path: string, options?: Parameters<typeof request>[1]): Promise<Blob> {
  return (await request(path, options)).blob();
}
