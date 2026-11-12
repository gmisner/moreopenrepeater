# ADR 0001: Mantine dashboard (React)

## Status

Accepted (vanilla deletion done)

## Context

The dashboard was a vanilla HTML/CSS/JS SPA in `web/` (~3k lines of
markup, ~7k lines of JS, ~1k lines of CSS). Spacing and component
consistency were hand-maintained against
`design-system/moreopenrepeater/MASTER.md`. Contributors kept rediscovering
the same density and form-layout bugs.

The controller often runs offline on a Raspberry Pi. MASTER forbids CDN
loads and web fonts. The install path is a git checkout; the Pi must not
need Node to update.

## Decision

Replace the vanilla dashboard with a **React + Mantine** app in a big-bang
cutover (login, listen, and all authenticated views land together).

- Source lives in `frontend/` (Vite + TypeScript + Mantine).
- `npm run build` writes into `web/dist/`.
- **CI and developers build the UI; the Pi only receives prebuilt files**
  from git. Install/update never runs `npm`.
- Theme tokens map 1:1 to MASTER (system fonts, semantic status colors,
  light/dark/auto). No Google Fonts / CDN.
- High-frequency WebSocket status updates go through a narrow store so
  TX/RX indicators do not re-render the whole tree. The store is split into
  React contexts (`connected`, callsign, live on-air fields, gpio/link
  extras, full snapshot) with matching hooks in `frontend/src/status.tsx`.
- FastAPI serves **`web/dist` only**. Presence of `web/dist/index.html` is
  the gate; if it is missing, app startup fails with a clear error. There
  is no fallback to vanilla `web/index.html` / `listen.html` / `login.html`.
- The former cutover marker `web/dist/.ready` is obsolete and removed.
  Dist presence alone is enough.

## Consequences

- First-load JS is larger than the old static files; LAN use stays fine;
  weak uplinks will notice.
- Runtime cost depends on careful subscription boundaries around live
  status; done poorly, TX/RX can feel worse than imperative DOM updates.
- Contributors need Node locally (or CI) to change the UI.
- Vanilla sources (`web/js/*`, root HTML/CSS/JS entrypoints) are deleted.
  Leaflet stays vendored under `frontend/public/vendor` and is copied into
  dist. Icons, manifest, and other static assets required by the SPA live
  under `web/dist/` (and mirrored at `web/` / `frontend/public` as needed).
