import { api } from "./api.js";
import { shortTime } from "./link_schedules.js";
import { currentView, router } from "./router.js";
import { store } from "./store.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const REFRESH_MS = 3000;
const MODE_LABELS = { monitor: "monitor", "local monitor": "local monitor", connecting: "connecting" };

const list = document.getElementById("links-list");
const empty = document.getElementById("links-empty");
const favorites = document.getElementById("links-favorites");
const form = document.getElementById("links-connect");
const connectButton = document.getElementById("links-connect-button");
const disconnectAll = document.getElementById("links-disconnect-all");
const note = document.getElementById("links-note");

let timer = null;
let latest = null;
let lastNodes = "";

export function nodeTitle(info) {
  const name = info.name || info.callsign;
  return name ? `${name} (${info.node})` : info.node;
}

function detail(info) {
  const parts = [info.description, info.location].filter(Boolean);
  return parts.length ? ` <span class="muted">${escapeHtml(parts.join(" · "))}</span>` : "";
}

function render(status) {
  latest = status;
  list.innerHTML = status.links
    .map((link) => {
      const mode = MODE_LABELS[link.mode] ? `<span class="tag">${MODE_LABELS[link.mode]}</span>` : "";
      const until = link.until ? `<span class="tag">until ${escapeHtml(shortTime(link.until))}</span>` : "";
      const remove = status.available
        ? `<button type="button" class="btn btn-ghost btn-sm" data-disconnect="${escapeHtml(link.node)}" data-requires-write>Disconnect</button>`
        : "";
      const label = [link.name, link.callsign].filter(Boolean).join(" · ");
      const callsign = label ? `<strong>${escapeHtml(label)}</strong> ` : "";
      return `<li class="link-row${link.keyed ? " keyed" : ""}">
        <span class="indicator-dot" title="${link.keyed ? "Talking" : "Quiet"}"></span>
        <span class="link-name"><code>${escapeHtml(link.node)}</code> ${callsign}${detail(link)}</span>
        ${until}${mode}${remove}
      </li>`;
    })
    .join("");
  empty.hidden = status.links.length > 0;
  disconnectAll.hidden = !status.available || status.links.length < 2;
  form.hidden = !status.available;

  const linked = new Set(status.links.map((link) => link.node));
  favorites.innerHTML = status.available
    ? status.favorites
        .map((fav) => {
          const on = linked.has(fav.node);
          const title = `${fav.description || ""}${fav.monitor ? " (monitor only)" : ""}`.trim();
          return `<button type="button" class="btn btn-sm ${on ? "btn-primary" : "btn-ghost"}" data-favorite="${escapeHtml(fav.node)}"
            ${on ? "disabled" : ""} title="${escapeHtml(title)}">★ ${escapeHtml(nodeTitle(fav))}</button>`;
        })
        .join("")
    : "";

  const message = status.error
    ? status.error
    : !status.available && status.node === null && status.links.length === 0
      ? "Connect the repeater to AllStarLink (see the AllStarLink page) to link nodes from here."
      : !status.available && status.node !== null
        ? "Choose the repeater's AllStarLink node to link nodes from here."
        : "";
  note.hidden = !message;
  note.textContent = message;
}

async function refresh() {
  render(await api("/api/links"));
}

async function change(request, success) {
  try {
    render(await request());
    if (success) toast(success);
    setTimeout(() => refresh().catch(() => {}), 1500);
  } catch (error) {
    toastError(error);
  }
}

function connect(node, monitor) {
  return change(() => api("/api/links", { method: "POST", json: { node, monitor } }), `Connecting node ${node}`);
}

function onClick(event) {
  const disconnect = event.target.closest("[data-disconnect]");
  if (disconnect) {
    const node = disconnect.dataset.disconnect;
    change(() => api(`/api/links/${encodeURIComponent(node)}`, { method: "DELETE" }), `Disconnecting node ${node}`);
    return;
  }
  const favorite = event.target.closest("[data-favorite]");
  if (favorite) {
    const fav = latest?.favorites.find((f) => f.node === favorite.dataset.favorite);
    if (fav) connect(fav.node, fav.monitor);
  }
}

function sync(view) {
  if (view === "dashboard" && !timer) {
    refresh().catch(() => {});
    timer = setInterval(() => {
      if (!document.hidden) refresh().catch(() => {});
    }, REFRESH_MS);
  } else if (view !== "dashboard" && timer) {
    clearInterval(timer);
    timer = null;
  }
}

export function initLinks() {
  list.addEventListener("click", onClick);
  favorites.addEventListener("click", onClick);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const node = form.elements.node.value.trim();
    withBusy(connectButton, async () => {
      await connect(node, form.elements.monitor.checked);
      form.reset();
    });
  });
  disconnectAll.addEventListener("click", () => {
    if (confirm("Disconnect every link?")) change(() => api("/api/links", { method: "DELETE" }), "Disconnecting all links");
  });
  store.addEventListener("status", ({ detail }) => {
    const nodes = JSON.stringify(detail.linked_nodes);
    if (nodes !== lastNodes) {
      lastNodes = nodes;
      if (currentView === "dashboard") refresh().catch(() => {});
    }
  });
  router.addEventListener("change", ({ detail }) => sync(detail));
  sync(currentView);
}
