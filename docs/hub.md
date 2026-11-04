# Multi-repeater hub (Sites)

A lightweight hub so one moreopenrepeater instance can list other repeaters:
callsign/state, version/channel, last activity, alerts, and a link to each
site's own dashboard.

## How it works

- The **hub** keeps a site list in `data/hub-sites.json` (base URL + API token
  per remote). Tokens are never returned by the API after create/update.
- On **Sites** (admin), the hub polls each remote with that site's Bearer token:
  - `GET /api/status` — reachable?, callsign, state
  - `GET /api/updates` — version / channel (needs an **admin** token)
  - `GET /api/activity/transmissions?limit=1` — last activity
  - `GET /api/alerts` — alerts summary (needs an **admin** token)
- Remotes **never call home**. If the hub is down, each site keeps running.

## Setup

1. On each remote, open **Users** and create an API token (admin for full
   columns; viewer still shows status and activity).
2. On the hub, open **Sites**, add a name, the remote's origin URL
   (`https://repeater.example.com`, no `/api` path), and the token.
3. Use **Refresh status** to re-poll. **Open** goes to that site's dashboard.

The same machine can be both a local repeater and a hub; the Sites list is
optional and independent of on-air behavior.

## API

| Method | Path | Notes |
|--------|------|--------|
| `GET` | `/api/hub/sites` | Configured sites (no secrets) |
| `POST` | `/api/hub/sites` | `{name, base_url, api_token}` |
| `PUT` | `/api/hub/sites/{id}` | Optional name / base_url / api_token |
| `DELETE` | `/api/hub/sites/{id}` | Remove from hub only |
| `GET` | `/api/hub/sites/status` | Poll every site |
| `GET` | `/api/hub/sites/{id}/status` | Poll one site |

All hub routes require an admin session (or admin Basic/Bearer on the hub).

## Out of scope (follow-ups)

- Bulk update-all across sites
- Pushing the same macros / tones / announcements to a group
