# ADR 0002: Config facets (one source of truth)

## Status

Accepted

## Context

`RepeaterConfig` (~195 fields), `ConfigResponse`, and `ConfigUpdateRequest`
were field-for-field mirrors maintained by hand. Adding a weather or radio
setting meant editing three places that could drift. The Mantine dashboard
already groups keys with `useConfigSlice`, but the Python domain and API
models stayed a single flat blob.

## Decision

Split domain config into **frozen dataclass facets** under
`src/controller/config/facets/`, composed into `RepeaterConfig`:

- `identity_timing`, `radio_audio`, `link_radio`, `remote_base`, `aprs`,
  `weather`, `monitor`, `autopatch`, `mailbox`, `net`, `gpio_fan`,
  `homeassistant`, `linking`, `backup`, `transcription`, `public_page`

`RepeaterConfig` keeps **flat public attributes** (so existing
`config.callsign` call sites and `dataclasses.replace`-style updates keep
working) and exposes nested facet views (`config.weather.wx_lat`) that
project the same fields. Wire helpers `to_flat_dict` / `from_flat_dict` /
`replace_flat` are the persistence and API seam.

**Flat wire format stays.** On-disk state and `/api/config` remain one flat
JSON object of the same field names. Nesting is an internal module seam.

`ConfigResponse` is **generated** from `RepeaterConfig` (`api.config_schema`)
so the response field set cannot drift from the domain. `ConfigUpdateRequest`
keeps per-field validation; `clear_*` targets are declared as `CLEAR_FIELDS`
on the owning facet module. Section comments in the update model name the
facet for locality.

## Consequences

- Changing a weather (or other facet) setting is edited in one facet module
  for domain defaults; response shape follows automatically.
- Call sites may use flat or nested access; new code should prefer the facet
  view when touching a whole group.
- Remaining flat access is intentional and legacy-compatible: facets project
  the same fields, so `config.wx_lat` and `config.weather.wx_lat` stay
  equivalent. Prefer facet views when a change already touches a whole group.
- Hottest weather/APRS call sites (`api/service/weather.py`, `api/aprs_map.py`,
  APRS beacon + weather poll in `api/runtime.py`, APRS map fields in
  `api/routes/site.py`) were deepened to nested access. `identity_timing`,
  `link_radio`, and `state_machine` remain mostly flat for a later pass.
- A nested JSON API would need an intentional version bump and a Mantine
  client change in the same PR — out of scope here.
- `ConfigUpdateRequest` Field constraints remain hand-tuned next to the
  update model; further generating those from facet metadata is a follow-up.
