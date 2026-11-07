import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "@mantine/dates/styles.css";
import "./field-contrast.css";

import { ColorSchemeScript, MantineProvider, localStorageColorSchemeManager } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyDensity, readDensity } from "./prefs";
import { theme } from "./theme";

const colorSchemeManager = localStorageColorSchemeManager({ key: "theme" });
applyDensity(readDensity());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ColorSchemeScript defaultColorScheme="auto" localStorageKey="theme" />
    <MantineProvider theme={theme} defaultColorScheme="auto" colorSchemeManager={colorSchemeManager}>
      <Notifications position="bottom-right" />
      <App />
    </MantineProvider>
  </StrictMode>,
);
