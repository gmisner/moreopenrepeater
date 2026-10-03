import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const ROLES = ["admin", "operator", "viewer", "listener"];
const ROLE_LABELS = { admin: "Admin", operator: "Operator", viewer: "Viewer", listener: "Listener" };

const tbody = document.getElementById("users-tbody");
const form = document.getElementById("user-form");
const authOffNote = document.getElementById("users-auth-off");
const tokensTable = document.getElementById("tokens-table");
const tokensBody = document.getElementById("tokens-tbody");
const tokenForm = document.getElementById("token-form");
const tokenUser = document.getElementById("token-user");
const tokenCreated = document.getElementById("token-created");
const tokenValue = document.getElementById("token-value");

function roleSelect(user) {
  const options = ROLES.map(
    (r) => `<option value="${r}" ${r === user.role ? "selected" : ""}>${ROLE_LABELS[r]}</option>`,
  ).join("");
  return `<select data-role-select>${options}</select>`;
}

function render(users) {
  const selected = tokenUser.value || session?.username;
  tokenUser.innerHTML = users
    .filter((u) => u.role !== "listener")
    .map((u) => `<option value="${escapeHtml(u.username)}">${escapeHtml(u.username)} (${ROLE_LABELS[u.role]})</option>`)
    .join("");
  if ([...tokenUser.options].some((o) => o.value === selected)) tokenUser.value = selected;
  tbody.innerHTML = "";
  for (const user of users) {
    const row = document.createElement("tr");
    const you = session?.username === user.username ? ' <span class="tag">you</span>' : "";
    if (user.builtin) {
      row.innerHTML = `
        <td>${escapeHtml(user.username)}${you}</td>
        <td>Admin <small class="muted">(set in the service's environment file)</small></td>
        <td></td>`;
    } else {
      row.innerHTML = `
        <td>${escapeHtml(user.username)}${you}</td>
        <td>${roleSelect(user)}</td>
        <td class="row-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-reset-password>Reset password</button>
          <button type="button" class="btn btn-danger btn-sm" data-delete>Delete</button>
        </td>`;
      row.querySelector("[data-role-select]").addEventListener("change", (event) =>
        update(user, { role: event.target.value }),
      );
      row.querySelector("[data-reset-password]").addEventListener("click", () => {
        const password = prompt(`New password for ${user.username} (at least 8 characters):`);
        if (password) update(user, { password });
      });
      row.querySelector("[data-delete]").addEventListener("click", () => remove(user));
    }
    tbody.appendChild(row);
  }
}

async function update(user, changes) {
  try {
    render(await api(`/api/users/${encodeURIComponent(user.username)}`, { method: "PUT", json: changes }));
    toast(changes.password ? `Password reset for ${user.username}` : `${user.username} is now ${ROLE_LABELS[changes.role]}`);
  } catch (error) {
    toastError(error);
    await load();
  }
}

async function remove(user) {
  if (!confirm(`Delete ${user.username}? They'll be signed out immediately.`)) return;
  try {
    render(await api(`/api/users/${encodeURIComponent(user.username)}`, { method: "DELETE" }));
    toast(`Deleted ${user.username}`);
  } catch (error) {
    toastError(error);
  }
}

function renderTokens(tokens) {
  tokensTable.hidden = tokens.length === 0;
  tokensBody.innerHTML = "";
  for (const token of tokens) {
    const row = document.createElement("tr");
    const role = token.role ? ROLE_LABELS[token.role] : "user deleted";
    row.innerHTML = `
      <td>${escapeHtml(token.name)}</td>
      <td>${escapeHtml(token.username)} <small class="muted">${role}</small></td>
      <td>${formatTimestamp(token.created_at)}</td>
      <td>${token.last_used_at ? formatTimestamp(token.last_used_at) : '<span class="muted">never</span>'}</td>
      <td class="row-actions"><button type="button" class="btn btn-danger btn-sm" data-revoke>Revoke</button></td>`;
    row.querySelector("[data-revoke]").addEventListener("click", () => revoke(token));
    tokensBody.appendChild(row);
  }
}

async function revoke(token) {
  if (!confirm(`Revoke "${token.name}"? Anything using it stops working immediately.`)) return;
  try {
    renderTokens(await api(`/api/tokens/${encodeURIComponent(token.id)}`, { method: "DELETE" }));
    toast(`Revoked ${token.name}`);
  } catch (error) {
    toastError(error);
  }
}

const agentTransmit = document.getElementById("agent-transmit");

function renderAgentAccess(access) {
  const url = `${location.origin}/mcp`;
  document.getElementById("agent-mcp-url").textContent = url;
  document.getElementById("agent-mcp-config").textContent = JSON.stringify(
    { mcpServers: { repeater: { url, headers: { Authorization: "Bearer <your API token>" } } } },
    null,
    2,
  );
  document.getElementById("agent-mcp-missing").hidden = access.mcp_available;
  agentTransmit.checked = access.transmit;
}

async function load() {
  authOffNote.hidden = session?.auth_required !== false;
  render(await api("/api/users"));
  const signIn = session?.auth_required !== false;
  document.getElementById("tokens-card").hidden = !signIn;
  document.getElementById("agent-access-card").hidden = !signIn;
  if (signIn) {
    renderTokens(await api("/api/tokens"));
    renderAgentAccess(await api("/api/agent-access"));
  }
}

export function initUsers() {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    withBusy(form.querySelector("button[type=submit]"), async () => {
      try {
        render(await api("/api/users", { method: "POST", json: data }));
        toast(`Added ${data.username}`);
        form.reset();
        // The first account turns sign-in on; this browser isn't signed in yet.
        if (!session.auth_required) location.replace("/login");
      } catch (error) {
        toastError(error);
      }
    });
  });
  tokenForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(tokenForm));
    withBusy(tokenForm.querySelector("button[type=submit]"), async () => {
      try {
        const result = await api("/api/tokens", { method: "POST", json: data });
        renderTokens(result.tokens);
        tokenValue.textContent = result.token;
        tokenCreated.hidden = false;
        tokenForm.reset();
        tokenUser.value = data.username;
      } catch (error) {
        toastError(error);
      }
    });
  });
  agentTransmit.addEventListener("change", async () => {
    const transmit = agentTransmit.checked;
    if (transmit && !confirm("Let API tokens make the repeater transmit? You're responsible for what goes out on the air.")) {
      agentTransmit.checked = false;
      return;
    }
    try {
      renderAgentAccess(await api("/api/agent-access", { method: "PUT", json: { transmit } }));
      toast(transmit ? "Agents may now transmit" : "Agents can no longer transmit");
    } catch (error) {
      agentTransmit.checked = !transmit;
      toastError(error);
    }
  });
  document.getElementById("token-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(tokenValue.textContent);
      toast("Token copied");
    } catch {
      getSelection().selectAllChildren(tokenValue);
    }
  });
  router.addEventListener("change", ({ detail }) => {
    if (detail !== "users") {
      tokenCreated.hidden = true;
      tokenValue.textContent = "";
    }
    if (detail === "users") load().catch(toastError);
  });
  if (currentView === "users") load().catch(toastError);
}
