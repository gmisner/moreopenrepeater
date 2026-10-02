import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { toast, toastError, withBusy } from "./ui.js";

const tokenNote = document.getElementById("homeassistant-token");
const testTarget = document.getElementById("homeassistant-test-target");
const testButton = document.getElementById("homeassistant-test");

async function load() {
  const { token_set: tokenSet } = await api("/api/homeassistant");
  tokenNote.innerHTML = tokenSet
    ? "An access token is set, so macros can fire events too."
    : "No access token is set, so only webhooks work. To fire events, put a long-lived access token in " +
      "<code>MOREOPENREPEATER_HOMEASSISTANT_TOKEN</code> in <code>/etc/moreopenrepeater/env</code> and restart.";
}

async function sendTest() {
  const target = testTarget.value.trim();
  if (!target) {
    testTarget.focus();
    return;
  }
  try {
    await api("/api/homeassistant/test", { method: "POST", json: { target } });
    toast(`Home Assistant took the test for ${target}`);
  } catch (error) {
    toastError(error);
  }
}

export function initHomeAssistant() {
  testButton.addEventListener("click", () => withBusy(testButton, sendTest));
  router.addEventListener("change", ({ detail }) => {
    if (detail === "macros") load().catch(toastError);
  });
  if (currentView === "macros") load().catch(toastError);
}
