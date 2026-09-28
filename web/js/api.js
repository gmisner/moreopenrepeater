export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function describeError(status, payload) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors: [{loc: ["body", "field"], msg}, ...]
    return detail.map((d) => `${(d.loc || []).filter((p) => p !== "body").join(".")}: ${d.msg}`).join("; ");
  }
  return `Request failed (${status})`;
}

async function request(path, { method = "GET", json, body } = {}) {
  const init = { method, headers: {} };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
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

export async function api(path, options) {
  const response = await request(path, options);
  return response.json().catch(() => null);
}

export async function apiBlob(path, options) {
  return (await request(path, options)).blob();
}
