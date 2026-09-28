import { loadAnnouncements } from "./announcements.js";
import { api } from "./api.js";
import { store } from "./store.js";
import { toast, toastError, withBusy } from "./ui.js";

export function initBackup() {
  const downloadButton = document.getElementById("snapshot-download");
  downloadButton.addEventListener("click", () =>
    withBusy(downloadButton, async () => {
      try {
        const snapshot = await api("/api/snapshot");
        const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        const callsign = (snapshot.config.callsign || "repeater").toLowerCase();
        link.href = url;
        link.download = `moreopenrepeater-${callsign}-${new Date().toISOString().slice(0, 10)}.json`;
        link.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        toastError(error);
      }
    }),
  );

  const uploadInput = document.getElementById("snapshot-upload-input");
  uploadInput.addEventListener("change", async () => {
    const file = uploadInput.files[0];
    uploadInput.value = "";
    if (!file) return;
    let snapshot;
    try {
      snapshot = JSON.parse(await file.text());
    } catch {
      toast("That file isn't valid JSON", "error");
      return;
    }
    const macroCount = snapshot.macros?.length ?? 0;
    const announcementCount = snapshot.announcements?.length ?? 0;
    const counts = `${macroCount} macros and ${announcementCount} announcements in backup`;
    if (!confirm(`Restore "${file.name}"? This replaces the current configuration, macros and announcements (${counts}).`)) return;
    try {
      const applied = await api("/api/snapshot", { method: "POST", json: snapshot });
      for (const form of document.querySelectorAll("[data-config-form]")) form.classList.remove("dirty");
      store.set("config", applied.config);
      store.set("macros", applied.macros);
      await loadAnnouncements();
      toast("Configuration restored");
    } catch (error) {
      toastError(error);
    }
  });
}
