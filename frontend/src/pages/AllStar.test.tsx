import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext, type Config } from "../config";
import { theme } from "../theme";
import { AllStar } from "./AllStar";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

let mockRole = "admin";
vi.mock("../session", () => ({
  useSession: () => ({ session: { authenticated: true, role: mockRole } }),
}));

vi.mock("@mantine/notifications", () => ({
  notifications: { show: vi.fn() },
}));

import { api } from "../api";

const allstarStatus = {
  available: true,
  manual: false,
  can_restart: false,
  restart_needed: false,
  error: null,
  nodes: [{ number: "12345", rxchannel: "USRP/127.0.0.1:34001:32001", duplex: "0", controlled: true }],
  node: "12345",
  identity: { node: "12345", callsign: "W1AW", has_password: true },
  audio: { node: "12345", keyed: false, error: null },
};

const populatedConfig = {
  link_favorites: [{ node: "2000", name: "Hub", monitor: false }],
  link_schedules: [{ node: "2000", name: "Net", days: [1], time: "07:05", minutes: 60, monitor: false, enabled: true }],
};

function renderAllStar(config: Config | null = {}) {
  return render(
    <MantineProvider theme={theme}>
      <ConfigContext.Provider
        value={{
          config,
          loading: false,
          refresh: vi.fn(),
          save: vi.fn(async () => config ?? {}),
          reload: vi.fn(),
        }}
      >
        <AllStar />
      </ConfigContext.Provider>
    </MantineProvider>,
  );
}

function mockApi(linksResponse: unknown) {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/api/allstar") return allstarStatus;
    if (path === "/api/allstar/echolink") return { available: true, enabled: false, loaded: null, error: null, settings: null };
    if (path === "/api/links") return linksResponse;
    if (path === "/api/links/events?limit=30") return { events: [] };
    return {};
  });
}

async function openScheduleTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: /Favorites and schedule/i }));
}

describe("AllStar role gating", () => {
  beforeEach(() => {
    mockApi({ available: true, node: "12345", error: null, links: [], favorites: [], schedules: [{ node: "2000", next_start: "2026-01-01T07:05:00", until: null }] });
  });

  it("hides favorite and schedule edit actions for viewers with empty data", async () => {
    mockRole = "viewer";
    const user = userEvent.setup();
    renderAllStar({ link_favorites: [], link_schedules: [] });
    await waitFor(() => expect(screen.getByText("AllStarLink")).toBeInTheDocument());
    await openScheduleTab(user);
    expect(screen.queryByRole("button", { name: "Edit favorites" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit schedules" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add a favorite" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add a schedule" })).not.toBeInTheDocument();
  });

  it("hides favorite and schedule edit actions for operators with populated data", async () => {
    mockRole = "operator";
    const user = userEvent.setup();
    renderAllStar(populatedConfig);
    await waitFor(() => expect(screen.getByText("AllStarLink")).toBeInTheDocument());
    await openScheduleTab(user);
    expect(screen.queryByRole("button", { name: "Edit favorites" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit schedules" })).not.toBeInTheDocument();
  });

  it("shows favorite and schedule edit actions for admins", async () => {
    mockRole = "admin";
    const user = userEvent.setup();
    renderAllStar(populatedConfig);
    await waitFor(() => expect(screen.getByText("AllStarLink")).toBeInTheDocument());
    await openScheduleTab(user);
    expect(screen.getByRole("button", { name: "Edit favorites" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit schedules" })).toBeInTheDocument();
  });
});

describe("AllStar links 200-with-error handling", () => {
  it("shows the links error", async () => {
    mockApi({ available: true, node: "12345", error: "app_rpt failed", links: [], favorites: [], schedules: [] });
    renderAllStar({ link_favorites: [], link_schedules: [] });
    await waitFor(() => expect(screen.getByText(/app_rpt failed/)).toBeInTheDocument());
  });

  it("retains nonempty links after a GET returns HTTP 200 with an error", async () => {
    let calls = 0;
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/allstar") return allstarStatus;
      if (path === "/api/allstar/echolink") return { available: true, enabled: false, loaded: null, error: null, settings: null };
      if (path === "/api/links/events?limit=30") return { events: [] };
      if (path === "/api/links") {
        calls += 1;
        if (calls === 1) return { available: true, node: "12345", error: null, links: [{ node: "2000", kind: "allstar", callsign: "WB6NIL", mode: "transceive", keyed: false }], favorites: [], schedules: [] };
        return { available: true, node: "12345", error: "app_rpt failed", links: [], favorites: [], schedules: [] };
      }
      return {};
    });

    renderAllStar({ link_favorites: [], link_schedules: [] });
    await waitFor(() => expect(screen.getAllByText("WB6NIL").length).toBeGreaterThan(0));

    await new Promise((resolve) => setTimeout(resolve, 10500));
    await waitFor(() => expect(screen.getByText(/app_rpt failed|Links may be out of date/)).toBeInTheDocument(), { timeout: 2000 });
    expect(screen.getAllByText("WB6NIL").length).toBeGreaterThan(0);
  }, 15000);


});

describe("AllStar import of an existing node", () => {
  const importPreview = {
    configured: true,
    available: true,
    found: true,
    source: "/etc/asterisk",
    files: [{ name: "rpt.conf", status: "read", detail: "", included: [] }],
    nodes: [
      {
        number: "99001",
        callsign: "N0CALL",
        allowed_links: 1,
        startup_macro: "",
        has_registration: true,
        password_usable: true,
        radio: "SimpleUSB/99001",
        notes: [],
        suggested: true,
      },
    ],
    problems: [],
    will_take: ["Node number"],
    will_not_take: ["Sound card, PTT and COS settings"],
    target: { "99001": { state: "new", existing: "1999", reason: null } },
  };

  function mockWithImport(preview: unknown) {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/allstar") return allstarStatus;
      if (path === "/api/allstar/import") return preview;
      if (path === "/api/allstar/echolink") return { available: true, enabled: false, loaded: null, error: null, settings: null };
      if (path === "/api/links") return { available: true, node: "12345", error: null, links: [], favorites: [], schedules: [] };
      if (path === "/api/links/events?limit=30") return { events: [] };
      return {};
    });
  }

  it("offers the import to an admin when a node folder was given", async () => {
    mockRole = "admin";
    mockWithImport(importPreview);
    const user = userEvent.setup();
    renderAllStar({ link_favorites: [], link_schedules: [] });
    await user.click(await screen.findByRole("tab", { name: /Node and EchoLink/i }));
    expect(await screen.findByText("Found an existing node configuration")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import node 99001" })).toBeInTheDocument();
  });

  it("shows nothing about it when no folder was given", async () => {
    mockRole = "admin";
    mockWithImport({ ...importPreview, configured: false, found: false, nodes: [], target: null });
    const user = userEvent.setup();
    renderAllStar({ link_favorites: [], link_schedules: [] });
    await user.click(await screen.findByRole("tab", { name: /Node and EchoLink/i }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/allstar/import"));
    expect(screen.queryByText("Found an existing node configuration")).not.toBeInTheDocument();
    expect(screen.queryByText(/existing node/i)).not.toBeInTheDocument();
  });

  it.each(["operator", "viewer"])("does not even ask for it as %s", async (role) => {
    mockRole = role;
    mockWithImport(importPreview);
    const user = userEvent.setup();
    renderAllStar({ link_favorites: [], link_schedules: [] });
    await user.click(await screen.findByRole("tab", { name: /Node and EchoLink/i }));
    await waitFor(() => expect(screen.getByText("AllStarLink node")).toBeInTheDocument());
    expect(api).not.toHaveBeenCalledWith("/api/allstar/import");
    expect(screen.queryByText("Found an existing node configuration")).not.toBeInTheDocument();
  });
});
