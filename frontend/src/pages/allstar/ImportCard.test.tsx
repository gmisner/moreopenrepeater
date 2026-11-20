import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { theme } from "../../theme";
import { ImportCard, type ImportNode, type ImportPreview } from "./ImportCard";

vi.mock("../../api", () => ({ api: vi.fn(), ApiError: class ApiError extends Error {} }));
vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));

import { notifications } from "@mantine/notifications";
import { api } from "../../api";

const node = (over: Partial<ImportNode> = {}): ImportNode => ({
  number: "99001",
  callsign: "N0CALL",
  allowed_links: 2,
  startup_macro: "*81",
  has_registration: true,
  password_usable: true,
  radio: "SimpleUSB/99001",
  notes: [],
  suggested: true,
  ...over,
});

const preview = (over: Partial<ImportPreview> = {}): ImportPreview => ({
  configured: true,
  available: true,
  found: true,
  source: "/etc/asterisk",
  files: [{ name: "rpt.conf", status: "read", detail: "", included: [] }],
  nodes: [node()],
  problems: [],
  will_take: ["Node number", "Callsign (the CW ID in idrecording)", "Startup macro"],
  will_not_take: ["Radio channel driver settings (rxchannel, txchannel, duplex): the radio here is the controller's core over USRP"],
  target: { "99001": { state: "new", existing: "1999", reason: null } },
  ...over,
});

function show(onApplied = vi.fn()) {
  render(
    <MantineProvider theme={theme}>
      <ImportCard onApplied={onApplied} />
    </MantineProvider>,
  );
  return onApplied;
}

function answer(first: ImportPreview, after?: ImportPreview, posted?: (body: unknown) => void) {
  let current = first;
  vi.mocked(api).mockImplementation(async (path: string, options?: { method?: string; json?: unknown }) => {
    if (path === "/api/allstar/import" && options?.method === "POST") {
      posted?.(options.json);
      if (after) current = after;
      return { status: {}, took: ["Node number 99001", "Callsign N0CALL"], left: [], snapshot: "import-20261120-101010" };
    }
    if (path === "/api/allstar/import") return current;
    return {};
  });
}

describe("ImportCard", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("shows a loading state first", () => {
    vi.mocked(api).mockImplementation(() => new Promise(() => {}));
    show();
    expect(screen.getByRole("status")).toHaveTextContent(/looking for an existing node/i);
  });

  it("renders nothing when no folder was given to import from", async () => {
    answer(preview({ configured: false, found: false, nodes: [], will_take: [], will_not_take: [], target: null }));
    const { container } = render(
      <MantineProvider theme={theme}>
        <ImportCard />
      </MantineProvider>,
    );
    await waitFor(() => expect(api).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[role="status"]')).toBeNull());
    expect(screen.queryByText(/existing node/i)).not.toBeInTheDocument();
  });

  it("says plainly when the preview can't be loaded and offers a retry", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Request failed (500)"));
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Request failed \(500\)/);
    answer(preview());
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Found an existing node configuration")).toBeInTheDocument();
  });

  it("shows what it found, what it takes and what it leaves, and no password", async () => {
    answer(preview());
    show();
    expect(await screen.findByText("Found an existing node configuration")).toBeInTheDocument();
    const found = screen.getByLabelText("Node 99001");
    expect(found).toHaveTextContent("99001");
    expect(found).toHaveTextContent("N0CALL");
    expect(found).toHaveTextContent("2");
    expect(found).toHaveTextContent("*81");
    expect(found).toHaveTextContent("Present");
    expect(screen.getByText("Will be taken")).toBeInTheDocument();
    expect(screen.getByText("Will not be taken")).toBeInTheDocument();
    expect(screen.getByText(/the radio here is the controller's core over USRP/)).toBeInTheDocument();
    expect(screen.getByText("SimpleUSB/99001")).toBeInTheDocument(); // named as not carried over
    expect(document.body).not.toHaveTextContent(/password\s*[:=]/i);
    expect(screen.getByRole("button", { name: "Import node 99001" })).toBeEnabled();
  });

  it("writes nothing until Import is clicked, then posts the chosen node and says what happened", async () => {
    const posted = vi.fn();
    answer(preview(), preview({ target: { "99001": { state: "same", existing: "99001", reason: null } } }), posted);
    const onApplied = show();
    await screen.findByText("Found an existing node configuration");
    expect(vi.mocked(api).mock.calls.filter(([, o]) => o?.method === "POST")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Import node 99001" }));
    await waitFor(() => expect(posted).toHaveBeenCalledWith({ node: "99001", replace: false, callsign: "" }));
    expect(await screen.findByText("Imported")).toBeInTheDocument();
    expect(screen.getByText(/import-20261120-101010/)).toBeInTheDocument();
    expect(onApplied).toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Import again" })).toBeInTheDocument();
  });

  it("doesn't fall over on a reply with no node list", async () => {
    vi.mocked(api).mockResolvedValue({ configured: false });
    const { container } = render(
      <MantineProvider theme={theme}>
        <ImportCard />
      </MantineProvider>,
    );
    await waitFor(() => expect(api).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[role="status"]')).toBeNull());
    expect(container.querySelector("section")).toBeNull();
  });

  it("makes the user pick when the file has several nodes, and imports the one picked", async () => {
    const posted = vi.fn();
    answer(
      preview({
        nodes: [node(), node({ number: "99006", callsign: "N0CALL", suggested: false, startup_macro: "", allowed_links: 0, has_registration: false })],
        target: {
          "99001": { state: "new", existing: "1999", reason: null },
          "99006": { state: "new", existing: "1999", reason: null },
        },
      }),
      undefined,
      posted,
    );
    show();
    await screen.findByText("Found an existing node configuration");
    expect(screen.getByText(/more than one node/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /99001.*most likely/ })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /99006/ }));
    expect(screen.getByLabelText("Node 99006")).toHaveTextContent("Not found");
    await userEvent.click(screen.getByRole("button", { name: "Import node 99006" }));
    await waitFor(() => expect(posted).toHaveBeenCalledWith({ node: "99006", replace: false, callsign: "" }));
  });

  it("won't replace a node that is already set up until the user confirms", async () => {
    const posted = vi.fn();
    answer(preview({ target: { "99001": { state: "replace", existing: "54321", reason: null } } }), undefined, posted);
    show();
    await screen.findByText("Found an existing node configuration");
    const button = screen.getByRole("button", { name: "Import node 99001" });
    expect(screen.getByText("Node 54321 is already set up here")).toBeInTheDocument();
    expect(button).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /Replace node 54321 with 99001/ }));
    expect(button).toBeEnabled();
    await userEvent.click(button);
    await waitFor(() => expect(posted).toHaveBeenCalledWith({ node: "99001", replace: true, callsign: "" }));
  });

  it("asks for a callsign when the old node's ID isn't one, and sends it", async () => {
    const posted = vi.fn();
    answer(
      preview({ nodes: [node({ callsign: "", notes: ["Its ID is a recording (/x), which isn't carried over."] })] }),
      undefined,
      posted,
    );
    show();
    await screen.findByText("Found an existing node configuration");
    const button = screen.getByRole("button", { name: "Import node 99001" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/is a recording/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Callsign for the CW ID"), "W1ABC");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    await waitFor(() => expect(posted).toHaveBeenCalledWith({ node: "99001", replace: false, callsign: "W1ABC" }));
  });

  it("names the files it couldn't read and why, and still previews the rest", async () => {
    answer(
      preview({
        files: [
          { name: "rpt.conf", status: "read", detail: "", included: [] },
          { name: "iax.conf", status: "unreadable", detail: "permission denied (the container runs as user 999, which can't read it)", included: [] },
        ],
      }),
    );
    show();
    expect(await screen.findByText("Some files couldn't be used")).toBeInTheDocument();
    expect(screen.getByText(/permission denied/)).toBeInTheDocument();
    expect(screen.getByText("iax.conf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import node 99001" })).toBeEnabled();
  });

  it("says what was wrong when nothing could be imported", async () => {
    answer(
      preview({
        found: false,
        nodes: [],
        target: null,
        problems: ["rpt.conf couldn't be read: permission denied (the container runs as user 999, which can't read it)."],
        files: [{ name: "rpt.conf", status: "unreadable", detail: "permission denied (the container runs as user 999, which can't read it)", included: [] }],
      }),
    );
    show();
    expect(await screen.findByText("Nothing to import from /etc/asterisk")).toBeInTheDocument();
    expect(screen.getAllByText(/permission denied/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Import node/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Look again" })).toBeInTheDocument();
  });

  it("holds the button off while the container node isn't there, or can't take it", async () => {
    answer(preview({ available: false }));
    show();
    await screen.findByText("The container node isn't ready");
    expect(screen.getByRole("button", { name: "Import node 99001" })).toBeDisabled();
  });

  it("says why when there's no node in the container to turn into this one", async () => {
    answer(preview({ target: { "99001": { state: "blocked", existing: null, reason: "The container node's rpt.conf has no node to turn into this one." } } }));
    show();
    expect(await screen.findByText(/has no node to turn into this one/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import node 99001" })).toBeDisabled();
  });

  it("shows the server's reason when applying fails, and keeps the preview", async () => {
    vi.mocked(api).mockImplementation(async (_path: string, options?: { method?: string }) => {
      if (options?.method === "POST") throw new Error("Couldn't write rpt.conf: Permission denied.");
      return preview();
    });
    show();
    await screen.findByText("Found an existing node configuration");
    await userEvent.click(screen.getByRole("button", { name: "Import node 99001" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Import node 99001" })).toBeEnabled());
    expect(notifications.show).toHaveBeenCalledWith(expect.objectContaining({ color: "red", message: "Couldn't write rpt.conf: Permission denied." }));
    expect(screen.queryByText("Imported")).not.toBeInTheDocument();
    expect(screen.getByText("Found an existing node configuration")).toBeInTheDocument();
  });
});
