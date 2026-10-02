import { api } from "./api.js";
import { toastError } from "./ui.js";

const ROLE_LABELS = { admin: "admin", operator: "operator", viewer: "read-only", listener: "listener" };

export let session = null;

// Returns false if the page is navigating away to the login screen.
export async function initSession() {
  session = await api("/api/session");
  if (session.auth_required && !session.authenticated) {
    location.replace("/login");
    return false;
  }
  if (session.role === "listener") {
    location.replace("/listen");
    return false;
  }
  document.body.dataset.role = session.role;
  for (const el of document.querySelectorAll("[data-admin-only]")) el.hidden = session.role !== "admin";
  if (session.auth_required) {
    document.getElementById("user-name").textContent = session.username;
    document.getElementById("user-role").textContent = ROLE_LABELS[session.role] ?? session.role;
    document.getElementById("user-box").hidden = false;
    document.getElementById("logout-button").addEventListener("click", async () => {
      try {
        await api("/api/logout", { method: "POST" });
        location.replace("/login");
      } catch (error) {
        toastError(error);
      }
    });
  }
  return true;
}
