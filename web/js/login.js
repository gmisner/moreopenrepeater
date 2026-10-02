const form = document.getElementById("login-form");
const errorEl = document.getElementById("login-error");
const submitButton = form.querySelector("button[type=submit]");
// Only the listening page can be asked for, so the link can't send people off-site.
const next = new URLSearchParams(location.search).get("next") === "/listen" ? "/listen" : "/";

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.hidden = true;
  submitButton.disabled = true;
  submitButton.textContent = "Signing in\u2026";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    if (response.ok) {
      location.replace(next);
      return;
    }
    const error = await response.json().catch(() => ({}));
    errorEl.textContent = typeof error.detail === "string" ? error.detail : "Sign in failed";
    errorEl.hidden = false;
    form.elements.password.select();
  } catch {
    errorEl.textContent = "Can't reach the repeater. Check your connection.";
    errorEl.hidden = false;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Sign in";
  }
});
