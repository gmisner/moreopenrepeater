"""logd, the log collector (#221, #229). See `store` (the SQLite file), `server` (the sockets) and `main`.

This package must stay light to import: the container health check is `python -m logd.healthcheck`, which
imports nothing from here but the standard library.
"""
