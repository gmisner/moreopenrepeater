import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../session", () => ({ useSession: () => ({ session: { role: "admin" } }) }));
vi.mock("../clipboard", () => ({ copyToClipboard: vi.fn(async () => undefined) }));
vi.mock("../config", () => ({ notifyError: vi.fn(), notifySuccess: vi.fn() }));

import { api } from "../api";
import { copyToClipboard } from "../clipboard";
import { Updates } from "./Updates";

const SHA = "a".repeat(40);
const NEWER = "b".repeat(40);
const AUTO = { enabled: false, days: [0, 1, 2, 3, 4], start: "02:00", end: "05:00", idle_minutes: 15 };

const container = (over: Record<string, unknown> = {}) => ({
  available: false,
  channel: "dev",
  version: { sha: SHA, date: "2026-11-19T08:30:00+00:00", subject: "" },
  status: null,
  auto: AUTO,
  install: "container",
  container: { branch: "main", channel: "dev", repo: "gmisner/moreopenrepeater" },
  ...over,
});

const native = {
  available: true,
  channel: "stable",
  version: { sha: SHA, date: "2026-11-19T08:30:00+00:00", subject: "Installed" },
  status: null,
  auto: AUTO,
  install: "native",
  container: null,
};

const newer = {
  channel: null,
  branch: "main",
  latest: { sha: NEWER, date: "2026-11-20T09:00:00Z", subject: "Add the thing" },
  relation: "ahead",
  new_commits: [{ sha: NEWER, date: "2026-11-20T09:00:00Z", subject: "Add the thing" }],
  new_commit_count: 1,
  error: null,
};

function serve(updates: unknown, check: unknown | Error) {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/api/updates") return updates as never;
    if (path.startsWith("/api/updates/check")) {
      if (check instanceof Error) throw check;
      return check as never;
    }
    if (path === "/api/restart") return { available: false, requested: false } as never;
    return null as never;
  });
}

function renderPage() {
  return render(
    <MantineProvider>
      <Updates />
    </MantineProvider>,
  );
}

describe("Updates page, container install", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(copyToClipboard).mockClear();
  });

  it("shows the running commit, the branch it follows, and newer commits from GitHub", async () => {
    serve(container(), newer);
    renderPage();
    expect(await screen.findByText("1 new change on main")).toBeInTheDocument();
    expect(screen.getByText("aaaaaaa")).toBeInTheDocument();
    expect(screen.getByText(/Dev channel/)).toBeInTheDocument();
    expect(screen.getByText("Add the thing")).toBeInTheDocument();
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/updates/check");
  });

  it("says up to date when the running commit is the branch's newest", async () => {
    serve(container(), { ...newer, relation: "identical", new_commits: [], new_commit_count: 0, latest: { ...newer.latest, sha: SHA } });
    renderPage();
    expect(await screen.findByText("Up to date")).toBeInTheDocument();
  });

  it("shows the copyable update commands and copies them", async () => {
    serve(container(), newer);
    renderPage();
    expect(await screen.findByText(/cd moreopenrepeater\/docker/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy the update commands" }));
    expect(copyToClipboard).toHaveBeenCalledWith("cd moreopenrepeater/docker\n./update.sh");
    await userEvent.click(screen.getByRole("button", { name: "Copy the channel switch command" }));
    expect(copyToClipboard).toHaveBeenLastCalledWith("git checkout stable && ./update.sh");
  });

  it("has no Update button, no channel picker, no restart and no automatic updates, even when newer commits exist", async () => {
    serve(container(), newer);
    renderPage();
    await screen.findByText("1 new change on main");
    expect(screen.queryByRole("button", { name: /^update/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /switch to/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /restart now/i })).toBeNull();
    expect(screen.queryByText("Automatic updates")).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByText(/install-pi\.sh/)).toBeNull();
  });

  it("says unknown, and to run ./update.sh, when the image doesn't know its commit", async () => {
    serve(container({ version: null, channel: null, container: { branch: null, channel: null, repo: null } }), {
      ...newer,
      relation: null,
      new_commits: [],
      new_commit_count: 0,
    });
    renderPage();
    expect(await screen.findByText("Can't tell if this is up to date")).toBeInTheDocument();
    expect(screen.getByText(/unknown\. Run/)).toBeInTheDocument();
    expect(screen.getByText(/isn't on a branch, or .\/update\.sh hasn't run/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^update/i })).toBeNull();
  });

  it("shows a branch that is not a release channel as such", async () => {
    serve(container({ channel: null, container: { branch: "my-feature", channel: null, repo: "o/r" } }), newer);
    renderPage();
    expect(await screen.findByText(/not a release channel/)).toBeInTheDocument();
  });

  it("says plainly when GitHub can't be reached, and still shows what is running and how to update", async () => {
    serve(container(), { ...newer, latest: null, relation: null, new_commits: [], error: "Couldn't reach GitHub: no route" });
    renderPage();
    expect(await screen.findByText("Couldn't check for updates")).toBeInTheDocument();
    expect(screen.getByText("Couldn't reach GitHub: no route")).toBeInTheDocument();
    expect(screen.getByText("aaaaaaa")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the update commands" })).toBeInTheDocument();
  });

  it("checks again with refresh when asked", async () => {
    serve(container(), newer);
    renderPage();
    await screen.findByText("1 new change on main");
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith("/api/updates/check?refresh=true"));
  });

  it("doesn't flash the native page while it finds out which install this is", async () => {
    let release: (value: unknown) => void = () => undefined;
    const gate = new Promise<unknown>((resolve) => {
      release = resolve;
    });
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/updates") return (await gate) as never;
      return newer as never;
    });
    renderPage();
    expect(screen.queryByRole("button", { name: /up to date|^update/i })).toBeNull();
    expect(screen.queryByText("Automatic updates")).toBeNull();
    release(container());
    expect(await screen.findByText("How to update")).toBeInTheDocument();
  });

  it("does not ask the native updater's questions", async () => {
    serve(container(), newer);
    renderPage();
    await screen.findByText("1 new change on main");
    const paths = vi.mocked(api).mock.calls.map((call) => call[0]);
    expect(paths).not.toContain("/api/restart");
    expect(paths.filter((path) => path.startsWith("/api/updates/check?channel="))).toEqual([]);
  });
});

describe("Updates page, native install", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("still offers Update and the channels, and does not show the container page", async () => {
    serve(native, {
      channel: "stable",
      branch: "stable",
      latest: { sha: NEWER, date: "2026-11-20T09:00:00Z", subject: "x" },
      relation: "ahead",
      new_commits: [{ sha: NEWER, date: "2026-11-20T09:00:00Z", subject: "x" }],
      new_commit_count: 1,
      error: null,
    });
    renderPage();
    expect(await screen.findByText("1 new change on Stable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update" })).toBeEnabled();
    expect(screen.getByText("Automatic updates")).toBeInTheDocument();
    expect(screen.queryByText("How to update")).toBeNull();
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/updates/check?channel=stable");
  });

  it("still shows the page when the install can't be read, rather than loading forever", async () => {
    vi.mocked(api).mockRejectedValue(new Error("offline"));
    renderPage();
    expect(await screen.findByText("Automatic updates")).toBeInTheDocument();
  });

  it("treats a server that doesn't say which install it is as a native one", async () => {
    const { install: _install, container: _container, ...old } = native;
    serve(old, { channel: "stable", branch: "stable", latest: null, relation: "identical", new_commits: [], error: null });
    renderPage();
    expect(await screen.findByText("Automatic updates")).toBeInTheDocument();
    expect(screen.queryByText("How to update")).toBeNull();
  });
});
