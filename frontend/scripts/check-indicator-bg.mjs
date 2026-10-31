#!/usr/bin/env node
/**
 * Regression: Dashboard PTT/COS cards used bg={`${color}.light`}, which Mantine
 * cannot parse — React throws and the SPA goes blank when a transmission lights
 * COS/PTT. Fail if that pattern returns, or if the CSS-var form stops working.
 */
import React from "react";
import { renderToString } from "react-dom/server";
import { Card, MantineProvider, createTheme } from "@mantine/core";

const theme = createTheme({});

function render(bgStyle) {
  return renderToString(
    React.createElement(
      MantineProvider,
      { theme, forceColorScheme: "light" },
      React.createElement(Card, { withBorder: true, style: bgStyle }, "indicator"),
    ),
  );
}

function mustThrow(label, fn) {
  try {
    fn();
    console.error(`FAIL: expected throw for ${label}`);
    process.exit(1);
  } catch (e) {
    if (!String(e.message).includes("startsWith")) {
      console.error(`FAIL: unexpected error for ${label}:`, e.message);
      process.exit(1);
    }
  }
}

function mustWork(label, fn) {
  try {
    const html = fn();
    if (!html.includes("indicator")) {
      console.error(`FAIL: empty render for ${label}`);
      process.exit(1);
    }
  } catch (e) {
    console.error(`FAIL: threw for ${label}:`, e.message);
    process.exit(1);
  }
}

// The broken API that blanked the dashboard on RX/TX.
mustThrow("bg green.light", () =>
  renderToString(
    React.createElement(
      MantineProvider,
      { theme, forceColorScheme: "light" },
      React.createElement(Card, { bg: "green.light" }, "x"),
    ),
  ),
);
mustThrow("bg red.light", () =>
  renderToString(
    React.createElement(
      MantineProvider,
      { theme, forceColorScheme: "light" },
      React.createElement(Card, { bg: "red.light" }, "x"),
    ),
  ),
);

// Fixed form used by Indicator (CSS custom properties).
for (const color of ["green", "red"]) {
  mustWork(`style var ${color}-light`, () =>
    render({ background: `var(--mantine-color-${color}-light)` }),
  );
}

console.log("OK indicator bg regression");
