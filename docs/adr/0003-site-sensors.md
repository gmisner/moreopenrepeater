# ADR 0003: Site sensors as a config facet + service seam

## Status

Accepted

## Context

Issue #37 needs named analog site sensors (DS18B20 1-wire temperature and
ADS1115 I2C ADC channels) with dashboard readings, threshold alerts
(hysteresis), an optional on-air speak, and a DTMF macro. GPIO pins are
already named entities on `RepeaterConfig`; phone-alert credentials live in
`alerts.json`; append-heavy history (activity, audit) uses SQLite.

## Decision

1. **Definitions** live on a new `sensors` config facet (`sensors_enabled`,
   `sensors_poll_interval`, `site_sensors` list) so they ride
   `/api/config`, backups, and the flat wire format (ADR 0002).
2. **`SiteSensors`** (`api.site_sensors`) owns polling, hysteresis latch
   state, `notifier.post` alerts, optional `speak` on threshold, and the
   `sensor` macro hook — same shape as `AirportWeather` / `HealthMonitor`.
3. **Hardware adapters** sit in `sensors/` (sysfs 1-wire and raw
   `/dev/i2c-*`, no new pip deps). Missing devices return `None`; tests
   inject a fake reader.
4. **History** is SQLite (`data/sensors.db`), not `RepeaterConfig` — same
   reason as activity: append-heavy, queried by time range.

## Consequences

- Adding a sensor type means a reader branch plus UI type option; the facet
  list stays the operator-facing inventory.
- Live readings are not part of the WebSocket status snapshot; clients poll
  `GET /api/sensors` (GPIO-style).
- Charting and long-term Health-page history can grow on the SQLite store
  without further config-shape changes.
