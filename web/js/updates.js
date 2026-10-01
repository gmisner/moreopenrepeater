import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const POLL_MS = 2000;

const CHANNELS = {
  stable: { label: "Stable", note: "Tested releases. The one for repeaters on the air." },
  beta: { label: "Beta", note: "The next stable release while it's being tested. Good for a test repeater." },
  dev: { label: "Dev", note: "Every change as soon as it's pushed, before any testing. Expect breakage." },
};

const STATES = {
  requested: ["tag-warn", "starting"],
  running: ["tag-warn", "updating"],
  succeeded: ["tag-on", "updated"],
  rolled_back: ["tag-danger", "rolled back"],
  failed: ["tag-danger", "failed"],
};

const el = (id) => document.getElementById(id);
const installButton = el("updates-install");

let info = null;
let selected = null;
let lastCheck = null;
let polling = null;

const short = (sha) => (sha || "").slice(0, 7);
const busy = (status) => status && (status.state === "requested" || status.state === "running");
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function renderInstalled() {
  const v = info.version;
  el("updates-installed").innerHTML = [
    ["Version", v ? `<code>${short(v.sha)}</code> ${escapeHtml(v.subject)}` : "unknown (not a git checkout)"],
    ["Made", v ? escapeHtml(formatTimestamp(v.date)) : "--"],
    ["Channel", CHANNELS[info.channel].label],
  ]
    .map(([k, value]) => `<dt>${k}</dt><dd>${value}</dd>`)
    .join("");
  el("updates-unavailable").hidden = info.available;
}

function renderChannels() {
  for (const button of el("updates-channels").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.channel === selected));
  }
  const switching = selected !== info.channel ? ` Updating moves this repeater to ${CHANNELS[selected].label}.` : "";
  el("updates-channel-note").textContent = CHANNELS[selected].note + switching;
}

function renderCheck() {
  const check = lastCheck;
  const label = CHANNELS[selected].label;
  const switching = selected !== info.channel;
  const list = el("updates-commits");
  list.innerHTML = "";
  let title = "Checking…";
  let detail = "";
  let action = null;
  if (check?.error) {
    title = "Couldn't check for updates";
    detail = check.error;
    action = switching ? `Switch to ${label}` : "Update anyway";
  } else if (check) {
    const latest = check.latest ? `Latest on ${label}: ${short(check.latest.sha)}, ${formatTimestamp(check.latest.date)}.` : "";
    if (check.relation === "identical") {
      title = switching ? `${label} has the version that's installed` : "Up to date";
      detail = latest;
      action = switching ? `Switch to ${label}` : null;
    } else if (check.relation === "behind") {
      title = `${label} is older than what's installed`;
      detail = `${latest} Switching goes back to it.`;
      action = `Switch to ${label}`;
    } else {
      const count = check.new_commit_count || check.new_commits.length;
      title = check.relation === "diverged" ? `${label} has a different version` : `${plural(count, "new change")} on ${label}`;
      detail = check.relation === "diverged" ? `${latest} It lacks some changes that are installed now.` : latest;
      action = switching ? `Switch to ${label} and update` : "Update";
      for (const commit of check.new_commits) {
        const item = document.createElement("li");
        item.innerHTML = `<code>${short(commit.sha)}</code><span>${escapeHtml(commit.subject)}</span><span class="commit-date">${escapeHtml(formatTimestamp(commit.date))}</span>`;
        list.appendChild(item);
      }
    }
  }
  el("updates-check-title").textContent = title;
  el("updates-check-detail").textContent = detail;
  installButton.textContent = action || "Up to date";
  installButton.disabled = !action || !info.available || busy(info.status);
}

async function renderProgress() {
  const status = info.status;
  el("updates-progress").hidden = !status;
  if (!status) return;
  const [cls, text] = STATES[status.state] || ["", status.state];
  el("updates-state").innerHTML = `<span class="tag ${cls}">${text}</span>`;
  const channel = CHANNELS[status.channel]?.label ?? status.channel ?? "";
  const when = status.started_at ? formatTimestamp(status.started_at * 1000) : "";
  const defaults = {
    requested: "Waiting for the updater to start…",
    running: `Updating to ${channel}, started ${when}.`,
    succeeded: `Installed ${short(status.to_sha)} from ${channel} (${when}).`,
  };
  el("updates-message").textContent = status.message || defaults[status.state] || "";
  const log = await api("/api/updates/log").catch(() => null);
  if (log) {
    const view = el("updates-log");
    const atBottom = view.scrollTop + view.clientHeight >= view.scrollHeight - 4;
    view.textContent = log.join("\n");
    if (atBottom) view.scrollTop = view.scrollHeight;
  }
}

async function check(refresh = false) {
  const channel = selected;
  lastCheck = null;
  renderCheck();
  try {
    const result = await api(`/api/updates/check?channel=${channel}${refresh ? "&refresh=true" : ""}`);
    if (channel === selected) lastCheck = result;
  } catch (error) {
    if (channel === selected) lastCheck = { error: error.message };
  }
  if (channel === selected) renderCheck();
}

async function load() {
  if (session?.role !== "admin") return;
  info = await api("/api/updates");
  selected ??= info.channel;
  renderInstalled();
  renderChannels();
  await renderProgress();
  if (busy(info.status)) startPolling();
  await check();
}

function startPolling() {
  if (polling) return;
  polling = setInterval(async () => {
    try {
      info = await api("/api/updates");
    } catch {
      return; // the service is restarting
    }
    renderInstalled();
    await renderProgress();
    renderCheck();
    if (busy(info.status)) return;
    clearInterval(polling);
    polling = null;
    if (info.status?.state === "succeeded") {
      toast("Update installed; reloading the dashboard");
      setTimeout(() => location.reload(), 1500);
    } else {
      toast(info.status?.message || "The update didn't finish", "error");
      selected = info.channel;
      renderChannels();
      check(true);
    }
  }, POLL_MS);
}

export function initUpdates() {
  for (const button of el("updates-channels").querySelectorAll("button")) {
    button.addEventListener("click", () => {
      selected = button.dataset.channel;
      renderChannels();
      check();
    });
  }
  el("updates-refresh").addEventListener("click", (event) => withBusy(event.currentTarget, () => check(true)));
  installButton.addEventListener("click", async () => {
    const label = CHANNELS[selected].label;
    if (!confirm(`Install the latest ${label} version now? The repeater goes off the air for a minute or two.`)) return;
    installButton.disabled = true;
    try {
      info = await api("/api/updates", { method: "POST", json: { channel: selected } });
      await renderProgress();
      startPolling();
    } catch (error) {
      toastError(error);
    }
    renderCheck(); // stays disabled while the update runs
  });

  router.addEventListener("change", ({ detail }) => {
    if (detail === "updates") load().catch(toastError);
  });
  if (currentView === "updates") load().catch(toastError);
}
