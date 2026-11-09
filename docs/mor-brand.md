# MOR brand integration

MOR (MoreOpenRepeater) — **Radio. Reimagined.**

Reference artwork:

- [Brand guidelines](brand/brand-guidelines.jpg)
- [Dashboard mockup](brand/dashboard-mockup.jpg)

## Tokens

| Token | Hex | Use |
| --- | --- | --- |
| MOR Navy | `#0B1F2E` | Shell, panels, brand mark |
| MOR Orange | `#FF7A00` | Active nav, lockup accent, non-operational decoration |
| Slate | `#475569` | Secondary borders / muted chrome |
| Cloud | `#E5E7EB` | Light surfaces / glass edges |

Typography: one system UI stack for brand, headings, and body (controllers are often offline — no CDN fonts). Monospace stays only for tabular telemetry numbers.

Operational semantics stay unchanged: blue primary actions, green RX, red TX, amber warnings.

## Current stage

Shell + dashboard aligned with the rest of the app surfaces:

- Page background uses the same gray `--mor-bg` as every other route (no mountain photo)
- Dashboard cards use `--mor-panel` / `--mor-border` like the rest of the UI
- Navy chrome stays in the nav; orange marks the active item
- Status strip / KPI / quick-link spacing kept from the mockup rhythm
- Metrics stay live controller/config data only (no invented user counts)

## Next stages

- Optionally bundle Montserrat/Inter later if operators want exact brand faces offline
- Refine logo geometry against the guidelines lockup
