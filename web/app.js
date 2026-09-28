import { api } from "./js/api.js";
import { initAnnouncements, loadAnnouncements } from "./js/announcements.js";
import { initAssets, loadAssets } from "./js/assets.js";
import { initAudio } from "./js/audio.js";
import { initBackup } from "./js/backup.js";
import { initConfig, loadConfig } from "./js/config.js";
import { initLogs } from "./js/logs.js";
import { initMacros, loadMacros } from "./js/macros.js";
import { initRouter } from "./js/router.js";
import { initSession } from "./js/session.js";
import { initSimulator } from "./js/simulator.js";
import { applyStatus, connectStatusSocket, initStatus } from "./js/status.js";
import { toastError } from "./js/ui.js";
import { initUsage } from "./js/usage.js";
import { initWeather, loadWeather } from "./js/weather.js";

async function main() {
  if (!(await initSession())) return;

  initRouter();
  initStatus();
  initConfig();
  initAssets();
  initAudio();
  initMacros();
  initAnnouncements();
  initWeather();
  initUsage();
  initSimulator();
  initLogs();
  initBackup();

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
