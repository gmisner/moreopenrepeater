import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext } from "../config";
import { theme } from "../theme";
import { AllStar } from "./AllStar";
import { Dmr } from "./Dmr";
import { api } from "../api";
vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../session", () => ({ useSession: () => ({ session: { authenticated: true, role: "admin" } }) }));
vi.mock("@mantine/notifications", () => ({ notifications: { show: vi.fn() } }));
const allstar = { available: true, manual: false, can_restart: false, restart_needed: false, error: null, node: "51000", nodes: [{ number: "51000", rxchannel: "USRP/127.0.0.1", duplex: "0", controlled: true }], identity: { node: "51000", callsign: "W7DEMO", has_password: true }, audio: { keyed: false } };
const echo = { available: true, enabled: true, loaded: true, error: null, settings: { callsign: "W7DEMO-R", name: "Demo", location: "Phoenix", email: "demo@example.invalid", node_number: "123456", has_password: true, lat: 0, lon: 0, frequency_mhz: 146.94, tone_hz: 100, max_stations: 20, astnode: "51000", set_here: true } };
const dmr = { available: true, enabled: true, error: null, settings: { callsign: "W7DEMO", dmr_id: "3100001", repeater_ssid: "01", has_password: true, network: "brandmeister", host: "demo.example.invalid", port: 62031, talkgroup: 3100, slot: 2, color_code: 1, location: "Phoenix", description: "Demo", use_emulator: true }, audio: { configured: true, running: true, keyed: false, last_heard: null }, services: { analog_bridge: true, mmdvm_bridge: true, md380_emu: true }, restart: { available: false, requested: false } };
const editors = [
  { name: "DMR", kind: "dmr", label: /Network password/, endpoint: "/api/dmr" },
  { name: "AllStar node", kind: "allstar", label: /Node password/, endpoint: "/api/allstar/identity" },
  { name: "EchoLink", kind: "echo", label: /^Password/, endpoint: "/api/allstar/echolink" },
] as const;
let hasPassword = true;
beforeEach(() => {
  hasPassword = true;
  window.history.replaceState(null, "", "/");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/api/allstar" || path === "/api/allstar/identity") return { ...allstar, identity: { ...allstar.identity, has_password: hasPassword } };
    if (path === "/api/allstar/echolink") return { ...echo, settings: { ...echo.settings, has_password: hasPassword } };
    if (path === "/api/dmr") return { ...dmr, settings: { ...dmr.settings, has_password: hasPassword } };
    if (path === "/api/links/events?limit=30") return { events: [] };
    return { available: true, node: "51000", links: [], favorites: [], schedules: [] };
  });
});
async function openEditor(editor: typeof editors[number]) {
  const user = userEvent.setup();
  render(<MantineProvider theme={theme}><ConfigContext.Provider value={{ config: {}, loading: false, refresh: vi.fn(), save: vi.fn(async () => ({})), reload: vi.fn() }}>{editor.kind === "dmr" ? <Dmr /> : <AllStar />}</ConfigContext.Provider></MantineProvider>);
  if (editor.kind !== "dmr") await user.click(await screen.findByRole("tab", { name: "Node and EchoLink" }));
  const editName = editor.kind === "allstar" ? "Edit node" : "Edit…";
  await user.click(await screen.findByRole("button", { name: editName }));
  return { user, input: await screen.findByLabelText(editor.label, { selector: "input" }), dialog: screen.getByRole("dialog"), editName };
}
function lastSave(endpoint: string) {
  return vi.mocked(api).mock.calls.filter(([path, options]) => path === endpoint && options?.method === "PUT").at(-1)?.[1]?.json;
}
describe.each(editors)("$name saved password", (editor) => {
  it("keeps the mask out of submissions and clears replacement drafts after save/cancel", async () => {
    const { user, input, dialog, editName } = await openEditor(editor);
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("placeholder", "******");
    expect(input).toHaveValue("");
    expect(input).not.toBeRequired();
    expect(within(dialog).getByText(/Saved password\. Leave blank/)).toBeInTheDocument();
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(lastSave(editor.endpoint)).toBeDefined());
    expect(lastSave(editor.endpoint)).toMatchObject({ password: editor.kind === "dmr" ? undefined : "" });
    expect(JSON.stringify(lastSave(editor.endpoint))).not.toContain("******");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: editName }));
    const replacement = await screen.findByLabelText(editor.label, { selector: "input" });
    await user.type(replacement, "replacement-123");
    fireEvent.submit(replacement.closest("form")!);
    await waitFor(() => expect(lastSave(editor.endpoint)).toMatchObject({ password: "replacement-123" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: editName }));
    const reopened = await screen.findByLabelText(editor.label, { selector: "input" });
    expect(reopened).toHaveValue("");
    await user.type(reopened, "discard-me");
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: editName }));
    const cleared = await screen.findByLabelText(editor.label, { selector: "input" });
    expect(cleared).toHaveValue("");
    await user.type(cleared, "clear-me");
    await user.clear(cleared);
    fireEvent.submit(cleared.closest("form")!);
    await waitFor(() => expect(lastSave(editor.endpoint)).toMatchObject({ password: editor.kind === "dmr" ? undefined : "" }));
  });
  it("requires a password when none has been saved", async () => {
    hasPassword = false;
    const { input, dialog } = await openEditor(editor);
    expect(input).toBeRequired();
    expect(input).not.toHaveAttribute("placeholder", "******");
    expect(within(dialog).queryByText(/Saved password\./)).not.toBeInTheDocument();
  });
});
