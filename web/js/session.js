import { api } from "./api.js";
import { toastError } from "./ui.js";

// Returns false if the page is navigating away to the login screen.
export async function initSession() {
  const session = await api("/api/session");
  if (session.auth_required && !session.authenticated) {
    location.replace("/login");
    return false;
  }
  if (session.auth_required) {
    document.getElementById("user-name").textContent = session.username;
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
