# Dashboard UI

How to change the React dashboard (`frontend/`) so it stays consistent with the
current design. Tokens, status colors, spacing, and accessibility floors live
in `design-system/moreopenrepeater/MASTER.md`. Brand chrome (MOR navy/orange)
is in `docs/mor-brand.md`. This doc is the composition and shared-pattern
layer those files do not cover.

## When you open this

Read this before editing pages under `frontend/src/pages/`, the shell in
`frontend/src/layout/`, nav in `frontend/src/nav.ts`, or shared UI under
`frontend/src/components/`. After UI changes that operators would notice, rebuild
`web/dist` (`npm run build` in `frontend/`) so the Pi serves the new files.

## Source of truth in code

| Concern | Look here |
| --- | --- |
| Nav groups and phone tab bar | `frontend/src/nav.ts` |
| On-air / signal path | `frontend/src/pages/dashboard/OnAir.tsx` |
| Recent transmissions | `frontend/src/pages/dashboard/RecentActivity.tsx` |
| Sticky save bar | `frontend/src/components/ConfigActions.tsx` |
| Empty list / missing setup | `frontend/src/components/EmptyState.tsx` |
| Field (i) tips and "How this works" | `frontend/src/components/FieldHint.tsx` (`labeled`, `InfoNote`) |
| Offline banner | `frontend/src/components/ConnectionBanner.tsx` |
| Settings gate + crash catcher | `frontend/src/layout/RouteBoundary.tsx` |
| Loading / failed-load cards | `frontend/src/components/StateCards.tsx` |
| Table action column header | `frontend/src/components/ActionsHeader.tsx` |
| Theme tokens wired into Mantine | `frontend/src/theme.ts` |

Prefer these components over inventing a parallel pattern.

## Principles

1. **State first.** Live TX/RX and controller state beat configuration on every
   screen size.
2. **Operator job, not software shape.** Nav and page grouping follow what the
   operator is doing (Operate / Repeater / Integrations / System), not package
   layout.
3. **Plain English on the air.** Labels and empty copy say what the repeater
   does; avoid developer jargon in user-visible strings.
4. **Explanations in hovers, not subtitles.** Prefer `labeled(...)` / `InfoNote`
   over long page subtitles or paragraphs under every field.
5. **One shared empty.** Use `EmptyState` (optional `action`) when a list or
   setup panel has nothing yet.
6. **Save stays reachable.** Settings forms that can dirty use `ConfigActions`
   so the save bar sticks once there are unsaved changes.
7. **Resilience is shared.** Do not reimplement offline banners, config loading
   gates, or crash catchers on individual pages — they live in the shell /
   `RouteBoundary`.

## Dashboard composition

The dashboard hero is **On the air** (`OnAir`): station line, controller state
in operator words, live timers (time-out / hang / next ID), then a four-stage
**signal path** (Receiver → Tone → Controller → Transmitter) that lights with
COS/CTCSS/PTT.

Around it: control overview / health, then **Recent transmissions** as a
full-width horizontal panel (`RecentActivity`), then incident cards (lockout,
net, patch) when relevant, then the rest. Do not resurrect a separate PTT/COS
tile row as the primary status story — that role belongs to the signal path.

### Recent transmissions rows

Each row is a three-cell horizontal panel: **time | duration bar | kind +
duration**. Keep the kind label (`Kerchunk` / `Timed out`) and the duration
**outside** the bar track (`.mor-act-meta`). When every row is a short equal
kerchunk, bars render at 100% of the track; a label inside the track overflows
onto the duration and reads as `Kerchun1s` (#194). Do not put Recent
transmissions back in a narrow side column.

## Navigation

- Edit groups only in `NAV` / `TABBAR` in `nav.ts`.
- New pages get an icon *and* a label; place them in the group that matches the
  operator job.
- Phone tab bar stays the four most-used destinations plus More; do not grow
  it casually.

## Settings pages

- Forms over saved config: rely on `RouteBoundary`'s config gate (loading
  skeleton, then `ErrorCard` + retry). Add the route id to `NEEDS_CONFIG` when
  inventing a new settings page that must not flash defaults.
- End dirty forms with `<ConfigActions dirty={...} saving={...} onReset={...} />`.
- Put field help in `labeled("Label", "hint")` or a nearby `InfoNote`, not a
  permanent subtitle block.
- Card sections may use a short `subtitle` for scope ("SIP credentials for…");
  keep it one line.

## Lists and tables

- Empty collections: `<EmptyState>…</EmptyState>`, with an `action` when there
  is an obvious next step (add, connect, open setup).
- Tables with row buttons: include `<ActionsHeader />` so the actions column
  has a screen-reader name.

## AllStar and similar ops surfaces

Match the existing AllStar page shape when building similar ops UIs: setup
checklist when incomplete, live link diagram when linked, connect bar for the
primary action, schedule/timeline for timed behavior, and `EmptyState` when a
sub-panel is not available yet.

## Copy and a11y

- Status never by color alone (word or icon with every lit state).
- Interactive targets meet MASTER's size floors; tips work on hover, focus, and
  touch (`FieldHint` / `InfoNote` already do).
- Viewers never see controls they cannot use.
- After a page change, focus lands on the page `h1` (shell behavior — keep it).

## Done when

- The change reuses the components above instead of one-off layout.
- Nav, if touched, still reads as operator jobs.
- Settings pages that need config are gated; dirty forms use the sticky save bar.
- Empty and error paths use `EmptyState` / `ErrorCard` with plain copy.
- `web/dist` is rebuilt when the UI ships to the Pi.
