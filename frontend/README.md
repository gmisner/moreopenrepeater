# moreopenrepeater dashboard (Mantine)

React + Mantine UI. Source of truth for the dashboard going forward; see
[`docs/adr/0001-mantine-dashboard.md`](../docs/adr/0001-mantine-dashboard.md).

## Develop

Needs the API on `:8000` (proxy is configured in `vite.config.ts`):

```bash
npm install
npm run dev
```

## Build (required before commit / release)

Writes into `../web/dist` for the Pi to serve with no Node:

```bash
npm run build
```

The API keeps serving the legacy vanilla UI until `web/dist/.ready` exists
(touch that file at cutover and commit it with the built assets). CI runs
the same build. Do not edit files under `web/dist` by hand.
