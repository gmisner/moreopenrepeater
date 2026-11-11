# MOR brand integration

MOR (MoreOpenRepeater) — **Radio. Reimagined.**

## Logo assets

Exact opaque sheet crops live in [`docs/brand/exact-sheet/`](brand/exact-sheet/).
Transparent (alpha) variants live in [`docs/brand/transparent/`](brand/transparent/)
(see `GUIDE.md` there). The dashboard uses the **transparent** set.

Served copies (resized for the UI):

| File | Use |
| --- | --- |
| `frontend/public/brand/app_icon.png` | Shell / Listen brand mark, favicon / PWA icons |
| `frontend/public/brand/primary_stacked.png` | Login lockup (on navy plate) |
| `frontend/public/brand/horizontal.png` | Wide lockups when needed |
| `frontend/public/brand/wordmark_minimal.png` | Compact wordmark when needed |
| `frontend/public/brand/hero_logo_with_tagline.png` | Docs / marketing (exact-sheet crop) |

Reference artwork:

- [Transparent preview](brand/transparent/PREVIEW.jpg)
- [Exact source sheet](brand/exact-sheet/source_sheet.png)
- [Brand guidelines](brand/brand-guidelines.png)
- [Dashboard mockup](brand/dashboard-mockup.jpg)

## Tokens

| Token | Hex | Use |
| --- | --- | --- |
| Background / MOR Navy | `#0B1220` | Page bg (dark), shell nav, login lockup plate |
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
- Brand mark is the transparent `app_icon.png` (tile + mountain + tower + MOR)
- Login uses transparent `primary_stacked.png` on a navy plate
- Sidebar callsign / station identity is centered in its pill
- Metrics stay live controller/config data only

## Next stages

- Optionally bundle Montserrat/Inter later if operators want exact brand faces offline
- True SVG masters if a designer exports them separately from the sheet
