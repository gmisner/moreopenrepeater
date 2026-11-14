## Agent skills

### Issue tracker

Issues live in GitHub Issues for `gmisner/moreopenrepeater` via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/`. See `docs/agents/domain.md`.

### Dashboard UI

UI work on pages, shell, nav, or shared frontend components: follow
`docs/agents/dashboard-ui.md`. Tokens and a11y floors stay in
`design-system/moreopenrepeater/MASTER.md`; MOR blue chrome in `docs/mor-brand.md`.

## Modular architecture migration (#221)

Follow [GitHub issue #221](https://github.com/gmisner/moreopenrepeater/issues/221) as the parent epic. Work in small, independently reviewable PRs in the issue's staged order; do not rewrite the system at once. Start with a repository/process/device ownership inventory and performance baseline, then propose ADRs and versioned contracts before extracting processes.

**Radio safety invariants:** Local repeating, IDs, timeout handling and safe PTT must not depend on FastAPI, the dashboard, optional modules, a logging collector or external services. The core owns radio/PTT resources. Optional modules may request access only through scoped, expiring contracts. Never perform blocking network, disk or logging work in real-time controller/audio paths. Do not claim GPIO writes prove RF transmission without independent feedback.

**Approval gates:** Discovery, documentation and draft contracts can proceed without further input. Obtain maintainer approval for ADRs before irreversible process/transport choices and before any core extraction, transmitter/hardware behavior change or live deployment. Do not autonomously merge architecture-changing PRs. Preserve existing behavior, credentials, configuration, Raspberry Pi compatibility and rollback options.

**Observability:** Emit structured versioned events with correlation IDs; use bounded nonblocking telemetry, record dropped events, redact secrets and omit audio/message content by default. A collector failure or full disk must not stall the repeater.

**PR checklist:** Link the stage in #221; state scope, affected components, ADRs, tests/results, physical hardware versus simulated validation, resource/audio/PTT impact, migration, rollback, remaining risks and follow-ups. If acceptance gates fail, keep the PR in draft. Existing agent documentation and dashboard/issue-triage instructions above remain applicable.
