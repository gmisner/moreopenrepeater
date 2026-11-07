# Monitoring

## Prometheus metrics

The API exposes a Prometheus-compatible scrape endpoint at:

```
GET /metrics
```

No sign-in is required (Prometheus convention). On a host reachable from the
public internet, bind the dashboard behind a reverse proxy and allowlist
`/metrics` to your scraper, or keep the service on a private LAN / Tailscale
only.

### Example scrape config

```yaml
scrape_configs:
  - job_name: moreopenrepeater
    static_configs:
      - targets: ["repeater.local:8000"]
```

### What is exported

| Metric | Meaning |
|---|---|
| `moreopenrepeater_up` | Process is serving metrics |
| `moreopenrepeater_process_uptime_seconds` | Seconds since this API process started |
| `moreopenrepeater_controller_state{state=…}` | Active RepeaterController state |
| `moreopenrepeater_ptt_active` / `…_cos_active` | PTT asserted / COS open |
| `moreopenrepeater_transmitter_enabled` | Transmitter enabled |
| `moreopenrepeater_linked_nodes` | Linked AllStar / network node count |
| `moreopenrepeater_audio_engine_running` | Live audio engine running |
| `moreopenrepeater_rx_level_dbfs` | Receive level (dBFS) |
| `moreopenrepeater_audio_*_total` | Audio glitch counters |
| `moreopenrepeater_kerchunks_filtered_total` | Kerchunks dropped since process start |
| `moreopenrepeater_activity_*` | Last-24h activity totals (RX, timeouts, IDs, …) |

Uptime Kuma and similar tools can also hit `/metrics` or `/api/status` (the
latter needs a session or API token when sign-in is on).

## Grafana

A starter dashboard lives at
[`packaging/grafana/moreopenrepeater.json`](../packaging/grafana/moreopenrepeater.json).

1. Scrape the repeater with Prometheus (snippet above).
2. In Grafana: **Dashboards → Import** → upload the JSON (or paste it).
3. Pick your Prometheus datasource when prompted (`datasource` template variable).

Panels cover PTT/COS, RX level, controller state, linked nodes, 24h activity
counters, and audio glitch totals.

## Uptime Kuma

| Monitor type | URL / settings | Notes |
|---|---|---|
| HTTP(s) | `http://repeater:8000/metrics` | Expect status 200 and body containing `moreopenrepeater_up 1` (keyword monitor if available) |
| HTTP(s) – JSON | `http://repeater:8000/api/status` | Needs a session cookie or API token when sign-in is on; prefer `/metrics` for anonymous scrapes |
| Prometheus | Point at the same scrape job Grafana uses | Alert when `moreopenrepeater_up == 0` or `moreopenrepeater_audio_engine_running == 0` while audio is supposed to be on |

Keep `/metrics` on Tailscale / LAN only; it is intentionally unauthenticated.
