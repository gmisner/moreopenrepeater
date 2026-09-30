# moreopenrepeater design system

The dashboard is a control panel for a radio repeater. People glance at it to see
whether the repeater is transmitting, check it from a phone in the car or at the
site, and change settings at a desk. The design follows from that: status has to
be readable at a glance and at a distance, controls must be hard to hit by
accident, and nothing decorative competes with live state.

Everything lives in `web/style.css`. The tokens are at the top; components below
them use only token names.

## Principles

1. **State first.** Live things (TX/RX, controller state, links, alerts, GPIO) come
   before configuration, on every screen size.
2. **Color means something.** Green, red, amber and blue are reserved for status
   (see below). Never use them decoratively.
3. **Never color alone.** Every colored state also has a word or an icon: "TX",
   "talking", "Severe", a filled dot.
4. **System fonts, no web fonts.** The controller often runs on a Raspberry Pi with
   no internet connection. Nothing may load from a CDN.
5. **One set of components, two themes.** Components never contain raw colors, so
   the light theme is only a second set of token values.

## Tokens

### Color (semantic)

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| `--bg` | `#111317` | `#f3f4f6` | Page background |
| `--sidebar` | `#16191e` | `#ffffff` | Sidebar and phone header |
| `--panel` | `#1b1f25` | `#ffffff` | Cards |
| `--panel-2` | `#22272e` | `#eef0f3` | Rows and tiles inside cards, secondary buttons |
| `--field` | `#111317` | `#ffffff` | Text inputs and selects |
| `--inset` | `#0b0c0f` | `#f8f9fb` | Log viewer |
| `--border` | `#2c323b` | `#d5d9df` | All borders and dividers |
| `--text` | `#e6e8eb` | `#1a1d22` | Body text |
| `--muted` | `#8a919c` | `#5a6270` | Labels, help text, idle states |
| `--accent` | `#5aa5ff` | `#1a5cc8` | Primary actions, focus ring, links, the logo |
| `--green` | `#35d07f` | `#0a6b3b` | Receiving, COS, on, connected, success |
| `--red` | `#ff7070` | `#b8292a` | Transmitting, PTT, errors, destructive actions |
| `--amber` | `#ffb020` | `#8a5200` | Warnings, announcements, weather alerts, lost connection |
| `--on-accent` | `#08111f` | `#ffffff` | Text on a filled accent button |
| `--on-status` | `#111317` | `#ffffff` | Text on a filled green or amber pill |
| `--on-danger` | `#1a0505` | `#ffffff` | Text on the filled red PTT button |
| `--control-off` | `#3a404a` | `#b9bfc8` | Unlit indicator dots, switch track when off |
| `--knob` | `#ffffff` | `#ffffff` | Switch knob, map home marker ring |
| `--map-bg`, `--overlay`, `--halo` | | | Map background, label backing, text halo |

Each status color has a `-soft` tint (`--accent-soft`, `--green-soft`, `--red-soft`,
`--amber-soft`) built with `color-mix()`, used for tinted backgrounds behind the
same color's text. `--danger-border` and `--grid-line` are built the same way.

Contrast (WCAG AA, 4.5:1 for text) was checked for every status color on
`--panel`, `--panel-2` and `--bg`, and on its own soft tint over each of those, in
both themes. All pairs pass; the tightest are around 4.5:1 (status color on its
tint over `--panel-2`). Re-run the check when changing any color value.

### Status semantics

| Meaning | Color | Examples |
| --- | --- | --- |
| Transmitting / danger | red | PTT indicator, TX header chip, timeout, "Disconnect all" |
| Receiving / on / healthy | green | COS indicator, RX chip, "talking" link tag, "live" pill |
| Needs attention | amber | Weather alerts card, lost connection banner, unsaved changes |
| Neutral activity / primary | accent | Courtesy tone, hang time, links in the activity log, primary buttons |
| Idle / off | muted + `--control-off` | Unlit dots, "off" pills |

### Map palette

The APRS map draws markers and alert areas over OpenStreetMap tiles, which look
the same in both themes, so `web/js/map.js` keeps a fixed palette: repeater
`#4f9dff`, digipeater `#b58cff`, mobile `#35d07f`, fixed `#c9ced6`, weather
`#ffb020`, and alert severity Extreme `#d946ef`, Severe `#ff5c5c`, Moderate
`#ffb020`, Minor `#facc15`. Markers sit on a white disc, so they don't need to
change with the theme.

### Type

System font stack (`--font`) and `--mono` for callsigns, node numbers, times and
logs. The body is 15px.

| Token | Size | Use |
| --- | --- | --- |
| `--text-2xs` | 0.72rem | Uppercase section labels, table headings |
| `--text-xs` | 0.78rem | Tags, pills, small buttons, notes |
| `--text-sm` | 0.86rem | Form labels, tables, help text |
| `--text-md` | 0.92rem | Nav links, toasts, secondary body |
| `--text-base` | 1rem | Callsign chip, PTT button, keypad |
| `--text-lg` | 1.2rem | Large brand, compact state badge |
| `--text-xl` | 1.5rem | Page titles, big numbers |
| `--text-2xl` | 1.9rem | Controller state on the dashboard |

Uppercase labels use `letter-spacing: 0.06em`. Numbers that update live use
`font-variant-numeric: tabular-nums` so they don't jitter.

### Spacing

All padding, margins and gaps come from a 4px scale: `--space-1` (4px),
`--space-2` (8px), `--space-3` (12px), `--space-4` (16px), `--space-5` (20px),
`--space-6` (24px), `--space-8` (32px), `--space-9` (36px), `--space-12` (48px),
`--space-16` (64px). `--space-half` (2px) is only for the vertical padding of
tags. Buttons and other touch targets sit at least `--space-2` apart.

### Shape and motion

Radii: `--radius-xs` 4px (inline code, small labels), `--radius-sm` 6px (tags),
`--radius-md` 8px (buttons, inputs, rows), `--radius` 10px (cards), `--radius-lg`
14px (login card), `--radius-pill` (pills, chips, switches).

Shadows are rare: `--shadow-sm` for the switch knob and map markers,
`--shadow-md` for toasts, `--shadow-lg` for the login card. Cards use borders, not
shadows.

Transitions use `--speed` (0.15s) and animate only color, opacity and transform.
`prefers-reduced-motion` cuts them all to effectively zero.

## Layout

- **Desktop (over 860px):** fixed 240px sidebar, content up to 1150px wide.
- **Tablet and phone (860px and under):** the sidebar becomes a sticky header with
  the logo and the TX/RX chips, which stay visible on every page and link back to
  the dashboard. A bottom tab bar holds the four most-used pages (Dashboard,
  Activity, Links, Map) and **More**, which opens the full sidebar as a
  full-screen menu above the tab bar (Escape or any link closes it). Content
  reserves space for the tab bar and the phone's home indicator, and
  `scroll-padding` keeps focused fields out from under the header and tab bar.
  Heights use `dvh` so the browser's address bar doesn't cut off the layout.
- **Phone (600px and under):** cards get 1rem padding, the brand name is hidden
  (the logo stays), the controller state sits on one line, and toasts span the
  width.
- **Dashboard order**, at every width: controller state and PTT/COS/CTCSS, weather
  alerts (only when there are any), links, GPIO (only when set up), listen live,
  recent activity, then the static station and feature summaries.

## Components

- **Card:** `--panel`, 1px `--border`, `--radius`. An `h2` label in
  `--text-2xs` uppercase `--muted`, optionally in a `.card-header` with one small
  ghost button on the right. A card that needs attention gets an amber border
  (weather alerts).
- **Indicator tile:** dot + name + value. Lit state tints the tile with the status
  color's soft background and border, and the dot glows.
- **Buttons:** default (`--panel-2`), `.btn-primary` (filled accent),
  `.btn-ghost` (transparent), `.btn-danger` (red outline, fills red-soft on
  hover), `.btn-sm`. The PTT button fills red while held.
  - One primary button per form, for the action that form exists for (Save,
    Connect). The dashboard has one: Connect. Filled buttons that show an *on*
    state (a linked favorite, a GPIO output that's on) aren't calls to action and
    don't count.
  - Anything that drops a connection or deletes something is `.btn-danger`:
    Disconnect, Remove, Delete, Hang up. It sits apart from the primary button (a
    `.spacer` between them in `.form-actions`) and asks for confirmation when it
    can't be undone with one click.
  - Form actions go at the bottom right of their card, primary last.
- **Pills and tags:** pills are round and show connection or on/off state; tags
  are small labels (role, severity, "talking", "announced").
- **Segmented control:** for two or three mutually exclusive choices (listen
  source, theme, time range). Active segment uses `aria-pressed="true"` or
  `.active`.
- **Switch:** for settings that take effect when saved. Checkbox for list items.
- **Toasts:** bottom right (full width on phones), green edge for success, red
  for errors.

## Themes

`web/theme.js` runs in `<head>` before the stylesheet paints and sets
`data-theme="light"` or `"dark"` on `<html>`. The choice is saved in
`localStorage` under `theme` (missing means Auto, which follows
`prefers-color-scheme` and updates live when the system setting changes). The
Auto/Light/Dark control is in the sidebar footer.

To add a color: add it to both the dark `:root` block and the
`:root[data-theme="light"]` block, check its contrast in both, then use the token.

## Brand

The mark is a broadcast tower with two pairs of signal arcs, drawn on a 32×32
grid with 2.2–2.4px round-capped strokes. The outer arcs are at 55% opacity.

- `web/icon.svg`: app icon, the mark in `#4f9dff` on a dark rounded square.
  Used as the favicon and the manifest's scalable icon.
- `web/favicon.ico` (16 and 32px), `icon-192.png`, `icon-512.png`: rendered from
  `icon.svg`.
- `web/apple-touch-icon.png` (180px) and `icon-maskable-512.png`: full-bleed
  versions with the mark shrunk toward the center, because iOS and Android apply
  their own rounded mask.
- In the page, the mark is inline SVG using `currentColor`, colored `--accent`.

The name is always written in lowercase: moreopenrepeater.

## Accessibility checklist

- Text contrast at least 4.5:1 in both themes (see Color).
- Every interactive element at least 24×24px, and 44px tall on touch screens
  (`pointer: coarse`), with 8px between neighbors.
- Inputs use 16px text on touch screens; smaller text makes iOS Safari zoom in on
  focus.
- Links in text use `--accent`, never the browser's default blue.
- Visible focus ring: 2px `--accent` outline on `:focus-visible`.
- Status never relies on color alone.
- Live regions only where a change needs announcing (connection banner); the
  header TX/RX chips carry a spoken label instead of announcing every change.
- Respect `prefers-reduced-motion`.
- Viewers (read-only role) never see controls they can't use.
