import { loadAnnouncements } from "./announcements.js";
import { api } from "./api.js";
import { loadConfig } from "./config.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { store } from "./store.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const folderText = document.getElementById("backup-folder");
const errorLine = document.getElementById("backup-error");
const tbody = document.getElementById("backup-tbody");
const empty = document.getElementById("backup-empty");

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function describeContents(c) {
  const parts = [plural(c.macros ?? 0, "macro"), plural(c.announcements ?? 0, "announcement"), plural(c.audio_clips ?? 0, "clip"), plural(c.users ?? 0, "user")];
  if (c.recordings) parts.push(plural(c.recordings, "recording"));
  return parts.join(", ");
}

function describeRestore(r) {
  const parts = [plural(r.macros, "macro"), plural(r.announcements, "announcement"), plural(r.audio_clips, "audio clip")];
  if (r.users !== null) parts.push(plural(r.users, "user"));
  if (r.history) parts.push("activity history");
  if (r.recordings) parts.push(`${plural(r.recordings, "new recording")}`);
  const kept = r.users === null ? " The backup had no dashboard users, so the current ones were kept." : "";
  return `Backup restored: ${parts.join(", ")}.${kept}`;
}

async function refreshAfterRestore() {
  for (const form of document.querySelectorAll("[data-config-form]")) form.classList.remove("dirty");
  const [macros, assets] = await Promise.all([api("/api/macros"), api("/api/assets")]);
  await loadConfig();
  store.set("macros", macros);
  store.set("assets", assets);
  await loadAnnouncements();
}

const RESTORE_WARNING =
  "This replaces the settings, macros, announcements, audio clips, dashboard users, activity history and audit log " +
  "with the backup's. If your account isn't in the backup, you'll be signed out.";

async function restoreFull(request) {
  const result = await request();
  toast(describeRestore(result));
  try {
    await refreshAfterRestore();
  } catch {
    location.reload();
  }
  await loadFolder().catch(() => {});
}

async function restoreFile(file) {
  if (file.name.toLowerCase().endsWith(".zip")) {
    if (!confirm(`Restore "${file.name}"? ${RESTORE_WARNING}`)) return;
    const body = new FormData();
    body.append("file", file);
    await restoreFull(() => api("/api/backup/restore", { method: "POST", body }));
    return;
  }
  let snapshot;
  try {
    snapshot = JSON.parse(await file.text());
  } catch {
    toast("That isn't a backup file: choose a .zip full backup or a .json settings file", "error");
    return;
  }
  const counts = `${plural(snapshot.macros?.length ?? 0, "macro")} and ${plural(snapshot.announcements?.length ?? 0, "announcement")} in the backup`;
  if (!confirm(`Restore "${file.name}"? This replaces the current configuration, macros and announcements (${counts}).`)) return;
  const applied = await api("/api/snapshot", { method: "POST", json: snapshot });
  for (const form of document.querySelectorAll("[data-config-form]")) form.classList.remove("dirty");
  store.set("config", applied.config);
  store.set("macros", applied.macros);
  await loadAnnouncements();
  toast("Settings restored");
}

function renderFolder(folder) {
  folderText.textContent = folder.enabled
    ? `Saved in ${folder.directory} on the repeater. Point MOREOPENREPEATER_BACKUP_DIR at a USB drive or network share to keep them off the SD card.`
    : "No backup folder is set up.";
  errorLine.hidden = !folder.last_error;
  errorLine.textContent = folder.last_error ? `The last scheduled backup failed: ${folder.last_error}` : "";
  document.getElementById("backup-now").disabled = !folder.enabled;
  tbody.innerHTML = "";
  empty.hidden = folder.backups.length > 0;
  for (const backup of folder.backups) {
    const row = document.createElement("tr");
    const href = `/api/backups/${encodeURIComponent(backup.name)}`;
    row.innerHTML = `
      <td>${escapeHtml(formatTimestamp(backup.created_at))}</td>
      <td>${formatSize(backup.size)}</td>
      <td class="muted small">${escapeHtml(describeContents(backup.contents))}</td>
      <td class="row-actions">
        <a class="btn btn-ghost btn-sm" href="${href}" download>Download</a>
        <button type="button" class="btn btn-ghost btn-sm" data-restore>Restore</button>
        <button type="button" class="btn btn-danger btn-sm" data-delete>Delete</button>
      </td>
    `;
    row.querySelector("[data-restore]").addEventListener("click", (event) =>
      withBusy(event.currentTarget, async () => {
        if (!confirm(`Restore the backup from ${formatTimestamp(backup.created_at)}? ${RESTORE_WARNING}`)) return;
        try {
          await restoreFull(() => api(`${href}/restore`, { method: "POST" }));
        } catch (error) {
          toastError(error);
        }
      }),
    );
    row.querySelector("[data-delete]").addEventListener("click", async () => {
      if (!confirm(`Delete the backup from ${formatTimestamp(backup.created_at)}?`)) return;
      try {
        renderFolder(await api(href, { method: "DELETE" }));
      } catch (error) {
        toastError(error);
      }
    });
    tbody.appendChild(row);
  }
}

async function loadFolder() {
  if (session?.role !== "admin") return;
  renderFolder(await api("/api/backups"));
}

export function initBackup() {
  const downloadLink = document.getElementById("backup-download");
  const withRecordings = document.getElementById("backup-download-recordings");
  withRecordings.addEventListener("change", () => {
    downloadLink.href = withRecordings.checked ? "/api/backup?recordings=true" : "/api/backup";
  });

  const snapshotButton = document.getElementById("snapshot-download");
  snapshotButton.addEventListener("click", () =>
    withBusy(snapshotButton, async () => {
      try {
        const snapshot = await api("/api/snapshot");
        const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        const callsign = (snapshot.config.callsign || "repeater").toLowerCase();
        link.href = url;
        link.download = `moreopenrepeater-${callsign}-settings-${new Date().toISOString().slice(0, 10)}.json`;
        link.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        toastError(error);
      }
    }),
  );

  const uploadInput = document.getElementById("backup-upload-input");
  uploadInput.addEventListener("change", async () => {
    const file = uploadInput.files[0];
    uploadInput.value = "";
    if (!file) return;
    try {
      await restoreFile(file);
    } catch (error) {
      toastError(error);
    }
  });

  const nowButton = document.getElementById("backup-now");
  nowButton.addEventListener("click", () =>
    withBusy(nowButton, async () => {
      try {
        renderFolder(await api("/api/backups", { method: "POST" }));
        toast("Backup saved");
      } catch (error) {
        toastError(error);
      }
    }),
  );

  router.addEventListener("change", ({ detail }) => {
    if (detail === "backup") loadFolder().catch(toastError);
  });
  if (currentView === "backup") loadFolder().catch(toastError);
}
