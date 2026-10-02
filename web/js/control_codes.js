import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const card = document.getElementById("codes-card");
const statusLine = document.getElementById("code-status");
const setup = document.getElementById("code-setup");
const qr = document.getElementById("code-qr");
const secret = document.getElementById("code-secret");
const confirmForm = document.getElementById("code-confirm-form");
const beginButton = document.getElementById("code-begin");
const removeButton = document.getElementById("code-remove");
const tbody = document.getElementById("codes-tbody");
const empty = document.getElementById("codes-empty");

function renderStatus(status) {
  statusLine.textContent = status.enrolled
    ? `Your codes are set up (since ${formatTimestamp(status.since * 1000)}).`
    : "You haven't set up one-time codes.";
  beginButton.textContent = status.enrolled ? "Move to a new phone" : "Set up codes";
  removeButton.hidden = !status.enrolled;
}

function renderUsers(users) {
  empty.hidden = users.length > 0;
  tbody.innerHTML = "";
  for (const user of users) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(user.username)}</td>
      <td>${escapeHtml(formatTimestamp(user.since * 1000))}</td>
      <td class="row-actions"><button type="button" class="btn btn-danger btn-sm">Remove</button></td>
    `;
    row.querySelector("button").addEventListener("click", () => removeUser(user.username));
    tbody.appendChild(row);
  }
}

async function load() {
  if (session?.role === "viewer") return;
  renderStatus(await api("/api/me/control-code"));
  if (session?.role === "admin") renderUsers(await api("/api/control-codes"));
}

async function begin() {
  try {
    const result = await api("/api/me/control-code", { method: "POST" });
    qr.innerHTML = result.qr_svg;
    secret.textContent = result.secret.match(/.{1,4}/g).join(" ");
    setup.hidden = false;
    beginButton.textContent = "Start over";
    confirmForm.reset();
    confirmForm.elements.code.focus();
  } catch (error) {
    toastError(error);
  }
}

async function finish() {
  try {
    renderStatus(await api("/api/me/control-code/confirm", { method: "POST", json: { code: confirmForm.elements.code.value } }));
    setup.hidden = true;
    qr.innerHTML = "";
    secret.textContent = "";
    toast("One-time codes are set up");
    if (session?.role === "admin") renderUsers(await api("/api/control-codes"));
  } catch (error) {
    toastError(error);
  }
}

async function removeMine() {
  if (!confirm("Remove your one-time codes? Codes from your app will stop working.")) return;
  try {
    renderStatus(await api("/api/me/control-code", { method: "DELETE" }));
    toast("Removed your one-time codes");
    if (session?.role === "admin") renderUsers(await api("/api/control-codes"));
  } catch (error) {
    toastError(error);
  }
}

async function removeUser(username) {
  if (!confirm(`Remove ${username}'s one-time codes?`)) return;
  try {
    renderUsers(await api(`/api/control-codes/${encodeURIComponent(username)}`, { method: "DELETE" }));
    toast(`Removed ${username}'s one-time codes`);
    if (username === (session?.username ?? null)) renderStatus(await api("/api/me/control-code"));
  } catch (error) {
    toastError(error);
  }
}

export function initControlCodes() {
  if (!card) return;
  beginButton.addEventListener("click", () => withBusy(beginButton, begin));
  removeButton.addEventListener("click", () => withBusy(removeButton, removeMine));
  confirmForm.addEventListener("submit", (event) => {
    event.preventDefault();
    withBusy(confirmForm.querySelector("button[type=submit]"), finish);
  });
  router.addEventListener("change", ({ detail }) => {
    if (detail === "macros") load().catch(toastError);
  });
  if (currentView === "macros") load().catch(toastError);
}
