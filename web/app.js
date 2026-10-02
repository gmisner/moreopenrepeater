import { api } from "./js/api.js";
import { initAlerts } from "./js/alerts.js";
import { initAnnouncements, loadAnnouncements } from "./js/announcements.js";
import { initAssets, loadAssets } from "./js/assets.js";
import { initAudit } from "./js/audit.js";
import { initAllStar } from "./js/allstar.js";
import { initFavorites } from "./js/favorites.js";
import { initFolds } from "./js/folds.js";
import { initLinkSchedules } from "./js/link_schedules.js";
import { initGpioSchedules } from "./js/gpio_schedules.js";
import { initGpio } from "./js/gpio.js";
import { initLinks } from "./js/links.js";
import { initAutopatch } from "./js/autopatch.js";
import { initAudio } from "./js/audio.js";
import { initBackup } from "./js/backup.js";
import { initConfig, loadConfig } from "./js/config.js";
import { initListen } from "./js/listen.js";
import { initLogs } from "./js/logs.js";
import { initMap } from "./js/map.js";
import { initMacros, loadMacros } from "./js/macros.js";
import { initControlCodes } from "./js/control_codes.js";
import { initHomeAssistant } from "./js/homeassistant.js";
import { initNet } from "./js/net.js";
import { initRouter } from "./js/router.js";
import { initSession } from "./js/session.js";
import { initSimulator } from "./js/simulator.js";
import { applyStatus, connectStatusSocket, initStatus } from "./js/status.js";
import { initThemePicker } from "./js/theme_picker.js";
import { toastError } from "./js/ui.js";
import { initUpdates } from "./js/updates.js";
import { initUsage } from "./js/usage.js";
import { initUsers } from "./js/users.js";
import { initWeather, loadWeather } from "./js/weather.js";

async function main() {
  initThemePicker();
  if (!(await initSession())) return;

  initRouter();
  initStatus();
  initConfig();
  initAssets();
  initAudio();
  initListen();
  initMacros();
  initControlCodes();
  initHomeAssistant();
  initAnnouncements();
  initNet();
  initWeather();
  initAutopatch();
  initAllStar();
  initGpio();
  initLinks();
  initFavorites();
  initLinkSchedules();
  initGpioSchedules();
  initMap();
  initUsage();
  initSimulator();
  initLogs();
  initBackup();
  initUsers();
  initAudit();
  initUpdates();
  initAlerts();
  // Last, so fold summaries describe values the other modules just filled in.
  initFolds();

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
