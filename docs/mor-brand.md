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

Typography (guidelines): **Montserrat** for brand/headings, **Inter** for UI. Controllers are often offline, so the app uses those faces when present locally and falls back to system sans — nothing loads from a CDN.

Operational semantics stay unchanged: blue primary actions, green RX, red TX, amber warnings.

## Current stage

Shell + dashboard pass toward the approved mockup:

- Mountain SVG backdrop with glass nav/header/cards
- Desktop header with page title; existing Operate / Repeater / Integrations / System nav groups kept
- Orange filled active nav item
- Dashboard status strip (online + TX/RX/offset from APRS config when set)
- KPI cards, RF activity chart, system health, quick links — all from live controller data (no invented user counts)

## Next stages

- Bundle Montserrat/Inter locally if operators want exact faces offline
- Refine logo geometry against the guidelines lockup
- Carry glass surfaces through remaining pages
- Accessibility/contrast pass on glass + mountain backdrop
