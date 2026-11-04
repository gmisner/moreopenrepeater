"""HTTP route modules registered from `create_app`.

Each module exposes `register(app, **deps)` so `app.py` can stay a thin
FastAPI shell while `AppRuntime` owns wiring and background loops.
"""
