# MOR brand integration

MOR (MoreOpenRepeater) — **Radio. Reimagined.**

Reference artwork:

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
- Brand mark is mountain + lattice tower under blue signal arcs
- Login and the dashboard shell keep a subtle topographic contour wash (fades under the cards); no mountain photo wallpaper
- Sidebar callsign / station identity is centered in its pill
- Metrics stay live controller/config data only

## Next stages

- Optionally bundle Montserrat/Inter later if operators want exact brand faces offline
- Swap in designer-exported lockup / favicon rasters when a full branding kit is available
