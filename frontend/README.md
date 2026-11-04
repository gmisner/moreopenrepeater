# moreopenrepeater dashboard (Mantine)

React + Mantine UI. Source of truth for the dashboard; see
[`docs/adr/0001-mantine-dashboard.md`](../docs/adr/0001-mantine-dashboard.md).

## Develop

Needs the API on `:8000` (proxy is configured in `vite.config.ts`):

```bash
npm install
npm run dev
```

## Build (required before commit / release)

Writes into `../web/dist`. FastAPI serves that directory only (no Node on
the Pi; no vanilla fallback):

```bash
npm install
npm run build
```

Commit the resulting `web/dist/` with your UI change. CI runs the same
build. Do not edit files under `web/dist` by hand.
