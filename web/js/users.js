import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, toast, toastError, withBusy } from "./ui.js";

const ROLES = ["admin", "operator", "viewer"];
const ROLE_LABELS = { admin: "Admin", operator: "Operator", viewer: "Viewer" };

const tbody = document.getElementById("users-tbody");
const form = document.getElementById("user-form");
const authOffNote = document.getElementById("users-auth-off");

function roleSelect(user) {
  const options = ROLES.map(
    (r) => `<option value="${r}" ${r === user.role ? "selected" : ""}>${ROLE_LABELS[r]}</option>`,
  ).join("");
  return `<select data-role-select>${options}</select>`;
}

function render(users) {
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

async function load() {
  authOffNote.hidden = session?.auth_required !== false;
  render(await api("/api/users"));
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
  router.addEventListener("change", ({ detail }) => {
    if (detail === "users") load().catch(toastError);
  });
  if (currentView === "users") load().catch(toastError);
}
