import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { theme } from "../theme";
import { Remote } from "./Remote";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../session", () => ({
  useSession: () => ({ session: { authenticated: true, role: "admin" } }),
}));
vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));

import { api } from "../api";

const base = {
  available: false,
  installed: false,
  source: null,
  unreachable: false,
  backend_state: null,
  dns_name: null,
  auth_url: null,
  serve_enabled: false,
  serve_url: null,
  port: 8000,
  cli_error: null,
  status: null,
};

function show(info: object) {
  vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/tailscale" ? { ...base, ...info } : []));
  return render(
    <MantineProvider theme={theme}>
      <Remote />
    </MantineProvider>,
  );
}

describe("Remote access in the container install", () => {
  beforeEach(() => vi.mocked(api).mockReset());

  it("shows the host's Tailscale, read only, when it can be read over the socket", async () => {
    show({ installed: true, source: "socket", backend_state: "Running", dns_name: "pi.tail123.ts.net", serve_enabled: true });
    expect(await screen.findByText("connected")).toBeTruthy();
    expect(screen.getByText("installed")).toBeTruthy();
    expect(screen.getByText("pi.tail123.ts.net")).toBeTruthy();
    expect(screen.getByText(/can see Tailscale on the host but not change it/)).toBeTruthy();
    expect(screen.queryByText("Join tailnet")).toBeNull();
    expect(screen.queryByText("Leave the tailnet")).toBeNull();
  });

  it("says why it can't read Tailscale instead of saying it is not installed", async () => {
    const why = "Tailscale's socket at /var/run/tailscale/tailscaled.sock is there, but this dashboard isn't allowed to open it (permission denied).";
    show({ unreachable: true, cli_error: why });
    expect(await screen.findByText(why)).toBeTruthy();
    expect(screen.getByText("can't reach it")).toBeTruthy();
    expect(screen.getByText("can't tell from here")).toBeTruthy();
    expect(screen.queryByText("not installed")).toBeNull();
    expect(screen.queryByText(/needs an install made with/)).toBeNull();
  });

  it("keeps the old words for a plain install without the helper units and no Tailscale", async () => {
    show({});
    expect(await screen.findByText(/needs an install made with/)).toBeTruthy();
    expect(screen.getByText("not set up")).toBeTruthy();
  });
});
