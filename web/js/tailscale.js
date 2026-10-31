import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const POLL_MS = 2000;

const STATES = {
  requested: ["tag-warn", "starting"],
  running: ["tag-warn", "working"],
  needs_login: ["tag-warn", "waiting for login"],
  succeeded: ["tag-on", "done"],
  failed: ["tag-danger", "failed"],
};

const el = (id) => document.getElementById(id);

let info = null;
let polling = null;

const busy = (status) => status && (status.state === "requested" || status.state === "running");
const waitingLogin = (status) => status && status.state === "needs_login";
const connected = () => {
  const state = info?.backend_state;
  return state === "Running" || state === "Starting";
};

async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      toast("Copied");
      return;
    }
  } catch {
    /* fall through — Clipboard API needs a secure context */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.focus();
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } finally {
    area.remove();
  }
  if (ok) toast("Copied");
  else toastError(new Error("Couldn't copy"));
}

function phaseTag() {
  if (!info.available) return ["tag-warn", "not set up"];
  if (!info.installed) return ["", "not installed"];
  if (info.auth_url && !connected()) return ["tag-warn", "needs login"];
  if (connected() && info.serve_enabled) return ["tag-on", "serving"];
  if (connected()) return ["tag-on", "connected"];
  if (info.backend_state === "NeedsLogin") return ["tag-warn", "needs login"];
  if (info.backend_state) return ["tag-warn", escapeHtml(info.backend_state)];
  return ["", "installed"];
}

function renderStatus() {
  el("remote-unavailable").hidden = info.available;
  const [cls, text] = phaseTag();
  el("remote-state").innerHTML = `<span class="tag ${cls}">${text}</span>`;
  const rows = [
    ["Tailscale", info.installed ? "installed" : "not installed"],
    ["Tailnet", connected() ? "connected" : info.backend_state || "—"],
    ["Name", info.dns_name ? `<code>${escapeHtml(info.dns_name)}</code>` : "—"],
    ["Dashboard port", String(info.port)],
    ["Serve", info.serve_enabled ? "on" : "off"],
  ];
  el("remote-kv").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  const err = el("remote-cli-error");
  err.hidden = !info.cli_error;
  err.textContent = info.cli_error || "";
}

function renderCards() {
  const canAct = info.available && !busy(info.status);
  el("remote-install-card").hidden = !info.available || info.installed;
  el("remote-join-card").hidden = !info.available || !info.installed || connected();
  el("remote-serve-card").hidden = !info.available || !connected();
  el("remote-leave-card").hidden = !info.available || !info.installed || (!connected() && !info.auth_url);

  el("remote-install").disabled = !canAct || info.installed;
  el("remote-join").disabled = !canAct;
  el("remote-leave").disabled = !canAct;
  const serve = el("remote-serve");
  serve.disabled = !canAct;
  serve.checked = !!info.serve_enabled;

  const authWrap = el("remote-auth-url-wrap");
  const authUrl = info.auth_url || info.status?.auth_url;
  authWrap.hidden = !authUrl;
  if (authUrl) {
    const link = el("remote-auth-url");
    link.href = authUrl;
    link.textContent = authUrl;
  }

  const serveWrap = el("remote-serve-url-wrap");
  const serveUrl = info.serve_url || (info.serve_enabled && info.dns_name ? `https://${info.dns_name}` : null);
  serveWrap.hidden = !serveUrl;
  if (serveUrl) {
    const link = el("remote-serve-url");
    link.href = serveUrl;
    link.textContent = serveUrl;
  }
}

async function renderProgress() {
  const status = info.status;
  el("remote-progress").hidden = !status;
  if (!status) return;
  const [cls, text] = STATES[status.state] || ["", status.state];
  el("remote-progress-state").innerHTML = `<span class="tag ${cls}">${text}</span>`;
  const when = status.started_at ? formatTimestamp(status.started_at * 1000) : "";
  const defaults = {
    requested: "Waiting for the helper to start…",
    running: `Working${when ? `, started ${when}` : ""}…`,
    needs_login: "Open the login link above to finish joining.",
    succeeded: status.action ? `Finished ${status.action}.` : "Done.",
  };
  el("remote-progress-message").textContent = status.message || defaults[status.state] || "";
  const log = await api("/api/tailscale/log").catch(() => null);
  if (log) {
    const view = el("remote-log");
    const atBottom = view.scrollTop + view.clientHeight >= view.scrollHeight - 4;
    view.textContent = log.join("\n");
    if (atBottom) view.scrollTop = view.scrollHeight;
  }
}

function render() {
  renderStatus();
  renderCards();
  return renderProgress();
}

function shouldPoll() {
  return busy(info?.status) || waitingLogin(info?.status) || (info?.auth_url && !connected());
}

function startPolling() {
  if (polling) return;
  polling = setInterval(async () => {
    try {
      info = await api("/api/tailscale");
    } catch {
      return;
    }
    await render();
    if (shouldPoll()) return;
    clearInterval(polling);
    polling = null;
    if (info.status?.state === "failed") {
      toast(info.status.message || "The Tailscale change failed", "error");
    } else if (info.status?.state === "succeeded") {
      toast(info.status.message || "Done");
    }
  }, POLL_MS);
}

async function load() {
  if (session?.role !== "admin") return;
  info = await api("/api/tailscale");
  await render();
  if (shouldPoll()) startPolling();
}

async function act(button, path, json) {
  await withBusy(button, async () => {
    try {
      info = await api(path, { method: "POST", json: json ?? {} });
      await render();
      startPolling();
    } catch (error) {
      toastError(error);
      await load().catch(toastError);
    }
  });
}

export function initTailscale() {
  el("remote-install").addEventListener("click", (event) => act(event.currentTarget, "/api/tailscale/install"));
  el("remote-join").addEventListener("click", (event) => {
    const auth_key = el("remote-auth-key").value.trim();
    act(event.currentTarget, "/api/tailscale/up", { auth_key: auth_key || null });
  });
  el("remote-serve").addEventListener("change", (event) => {
    const enabled = event.currentTarget.checked;
    act(event.currentTarget, "/api/tailscale/serve", { enabled });
  });
  el("remote-leave").addEventListener("click", (event) => {
    if (!confirm("Leave the tailnet? Serve turns off and this controller logs out of Tailscale.")) return;
    act(event.currentTarget, "/api/tailscale/down");
  });
  el("remote-copy-auth").addEventListener("click", () => {
    const url = info?.auth_url || info?.status?.auth_url;
    if (url) copyText(url);
  });
  el("remote-copy-serve").addEventListener("click", () => {
    const url = info?.serve_url || (info?.dns_name ? `https://${info.dns_name}` : null);
    if (url) copyText(url);
  });

  router.addEventListener("change", ({ detail }) => {
    if (detail === "remote") load().catch(toastError);
  });
  if (currentView === "remote") load().catch(toastError);
}
