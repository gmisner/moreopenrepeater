# ADR 0001: Mantine dashboard (React)

## Status

Accepted

## Context

The dashboard is a vanilla HTML/CSS/JS SPA in `web/` (~3k lines of
markup, ~7k lines of JS, ~1k lines of CSS). Spacing and component
consistency are hand-maintained against
`design-system/moreopenrepeater/MASTER.md`. Contributors keep rediscovering
the same density and form-layout bugs.

The controller often runs offline on a Raspberry Pi. MASTER forbids CDN
loads and web fonts. The install path is a git checkout; the Pi must not
need Node to update.

## Decision

Replace the vanilla dashboard with a **React + Mantine** app in a big-bang
cutover (login, listen, and all authenticated views land together).

- Source lives in `frontend/` (Vite + TypeScript + Mantine).
- `npm run build` writes into `web/dist/` (and related static entrypoints).
- **CI and developers build the UI; the Pi only receives prebuilt files**
  from git. Install/update never runs `npm`.
- Theme tokens map 1:1 to MASTER (system fonts, semantic status colors,
  light/dark/auto). No Google Fonts / CDN.
- High-frequency WebSocket status updates go through a narrow store so
  TX/RX indicators do not re-render the whole tree.

## Consequences

- First-load JS is larger than today's static files; LAN use stays fine;
  weak uplinks will notice.
- Runtime cost depends on careful subscription boundaries around live
  status; done poorly, TX/RX can feel worse than imperative DOM updates.
- Contributors need Node locally (or CI) to change the UI.
- Vanilla `web/index.html`, `web/js/*`, and most of `web/style.css` go away
  at cutover; Leaflet stays vendored for the map.
- Cutover is gated on committing `web/dist/.ready` (with matching built
  assets). Until then FastAPI keeps serving the vanilla dashboard even if
  `web/dist/` exists from a local build.
