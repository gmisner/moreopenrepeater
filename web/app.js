import { api } from "./js/api.js";
import { initAnnouncements, loadAnnouncements } from "./js/announcements.js";
import { initAssets, loadAssets } from "./js/assets.js";
import { initAudit } from "./js/audit.js";
import { initAutopatch } from "./js/autopatch.js";
import { initAudio } from "./js/audio.js";
import { initBackup } from "./js/backup.js";
import { initConfig, loadConfig } from "./js/config.js";
import { initListen } from "./js/listen.js";
import { initLogs } from "./js/logs.js";
import { initMap } from "./js/map.js";
import { initMacros, loadMacros } from "./js/macros.js";
import { initRouter } from "./js/router.js";
import { initSession } from "./js/session.js";
import { initSimulator } from "./js/simulator.js";
import { applyStatus, connectStatusSocket, initStatus } from "./js/status.js";
import { toastError } from "./js/ui.js";
import { initUsage } from "./js/usage.js";
import { initUsers } from "./js/users.js";
import { initWeather, loadWeather } from "./js/weather.js";

async function main() {
  if (!(await initSession())) return;

  initRouter();
  initStatus();
  initConfig();
  initAssets();
  initAudio();
  initListen();
  initMacros();
  initAnnouncements();
  initWeather();
  initAutopatch();
  initMap();
  initUsage();
  initSimulator();
  initLogs();
  initBackup();
  initUsers();
  initAudit();

  try {
    // Assets first so the clip dropdowns have options before config
    // values are applied to them.
    await loadAssets();
    await Promise.all([loadConfig(), loadMacros(), loadAnnouncements(), loadWeather(), api("/api/status").then(applyStatus)]);
  } catch (error) {
    toastError(error);
  }
  connectStatusSocket();
}

main();
