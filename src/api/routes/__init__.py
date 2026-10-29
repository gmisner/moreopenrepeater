"""HTTP route modules registered from `create_app`.

Each module exposes `register(app, **deps)` so `app.py` can stay the wiring
layer while endpoint groups live next to their domain.
"""
