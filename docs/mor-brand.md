# MOR brand integration

MOR (MoreOpenRepeater) — **Radio. Reimagined.**

## Exact sheet assets

Pixel crops from the brand sheet live in [`docs/brand/exact-sheet/`](brand/exact-sheet/) (see `GUIDE.md` there). They are **opaque sheet crops**, not transparent SVGs — do not treat them as editable masters.

Served copies for the dashboard:

| File | Use |
| --- | --- |
| `frontend/public/brand/app_icon.png` | Shell / Listen brand mark, favicon / PWA icons |
| `frontend/public/brand/primary_stacked.png` | Login lockup |
| `frontend/public/brand/horizontal.png` | Wide lockups when needed |
| `frontend/public/brand/hero_logo_with_tagline.png` | Docs / marketing |

Reference artwork:

- [Exact source sheet](brand/exact-sheet/source_sheet.png)
- [Brand guidelines](brand/brand-guidelines.png)
- [Dashboard mockup](brand/dashboard-mockup.jpg)

## Tokens

| Token | Hex | Use |
| --- | --- | --- |
| Background / MOR Navy | `#0B1220` | Page bg (dark), shell nav, brand mark tile |
| Primary / MOR Blue | `#0D6EFD` | Active nav, OPEN highlight, primary actions, signal arcs |
| Accent / Hover | `#3DA5FF` | Hover fills, lighter brand accents |
| Text / UI | `#E5E7EB` | Body text on dark brand surfaces |

Typography: brand faces are Montserrat / Inter. Controllers are often offline, so the app keeps a system UI stack (no CDN fonts). Monospace stays only for tabular telemetry numbers.

Operational semantics stay unchanged: green RX, red TX, amber warnings. Brand blue is chrome and primary actions only — never a substitute for TX/RX state.

## Current stage

Shell + dashboard follow the blue identity sheet:

- Dark page background uses `#0B1220` (`--mor-bg`)
- Dashboard cards use `--mor-panel` / `--mor-border` above that bg
- Navy chrome stays in the nav; MOR blue marks the active item and OPEN
- Brand mark is the exact-sheet `app_icon.png` (mountain + tower + MOR)
- Login uses `primary_stacked.png`; authenticated pages use a flat bg (no topo wash)
- Sidebar callsign / station identity is centered in its pill
- Metrics stay live controller/config data only

## Next stages

- Optionally bundle Montserrat/Inter later if operators want exact brand faces offline
- Transparent / SVG masters if a designer exports them separately from the sheet
