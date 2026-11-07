import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@mantine/notifications", () => ({
  notifications: {
    show: vi.fn(),
  },
}));

vi.mock("../api", () => ({
  api: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

import { api } from "../api";
import { Sites } from "./Sites";

const SITE = {
  id: "abcd1234",
  name: "Production",
  base_url: "https://prod.example.com",
  dashboard_url: "https://prod.example.com",
  token_set: true,
};

const STATUS = {
  id: "abcd1234",
  name: "Production",
  base_url: "https://prod.example.com",
  dashboard_url: "https://prod.example.com",
  reachable: true,
  polled_at: 1,
  error: null,
  callsign: "W1AW",
  state: "idle",
  version: "abcdef0",
  channel: "stable",
  last_activity: 1_700_000_000,
  alerts_summary: "none",
};

function renderSites() {
  return render(
    <MantineProvider>
      <Sites />
    </MantineProvider>,
  );
}

describe("Sites page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("loads sites and status on mount", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/hub/sites") return [SITE];
      if (path === "/api/hub/sites/status") return [STATUS];
      throw new Error(`unexpected ${path}`);
    });

    renderSites();

    expect(await screen.findByText("W1AW")).toBeInTheDocument();
    expect(screen.getByText(/prod\.example\.com/)).toBeInTheDocument();
    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(screen.getByText("abcdef0 · stable")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
    expect(screen.getByText("yes")).toBeInTheDocument();
  });

  it("adds a site through the form", async () => {
    const user = userEvent.setup();
    const added = {
      id: "newsite01",
      name: "Test Pi",
      base_url: "https://test.example.com",
      dashboard_url: "https://test.example.com",
      token_set: true,
    };
    vi.mocked(api).mockImplementation(async (path: string, options?: { method?: string; json?: unknown }) => {
      if (path === "/api/hub/sites" && options?.method === "POST") {
        expect(options.json).toEqual({
          name: "Test Pi",
          base_url: "https://test.example.com",
          api_token: "mor_tok",
        });
        return [added];
      }
      if (path === "/api/hub/sites") return [];
      if (path === "/api/hub/sites/status") return [];
      throw new Error(`unexpected ${path}`);
    });

    renderSites();
    expect(await screen.findByText("No remote sites yet. Add one below.")).toBeInTheDocument();

    const form = screen.getByRole("heading", { name: "Add a site" }).closest("form") as HTMLElement;
    expect(form).toBeTruthy();
    const fields = within(form);
    await user.type(fields.getByRole("textbox", { name: /^Name/ }), "Test Pi");
    await user.type(fields.getByPlaceholderText("e.g. https://…"), "https://test.example.com");
    const token = form.querySelector('input[type="password"]');
    expect(token).toBeTruthy();
    await user.type(token as HTMLElement, "mor_tok");
    await user.click(fields.getByRole("button", { name: "Add site" }));

    await waitFor(() => expect(screen.getByText("Test Pi")).toBeInTheDocument());
    expect(screen.getByText("https://test.example.com")).toBeInTheDocument();
  });

  it("removes a site after confirm", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.mocked(api).mockImplementation(async (path: string, options?: { method?: string }) => {
      if (path === "/api/hub/sites" && options?.method === "DELETE") return [];
      if (path === `/api/hub/sites/${SITE.id}` && options?.method === "DELETE") return [];
      if (path === "/api/hub/sites") return [SITE];
      if (path === "/api/hub/sites/status") return [STATUS];
      throw new Error(`unexpected ${path} ${options?.method}`);
    });

    renderSites();
    const row = (await screen.findByText("W1AW")).closest("tr");
    expect(row).toBeTruthy();
    await user.click(within(row as HTMLElement).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(screen.getByText("No remote sites yet. Add one below.")).toBeInTheDocument());
    expect(api).toHaveBeenCalledWith(`/api/hub/sites/${SITE.id}`, { method: "DELETE" });
  });
});
