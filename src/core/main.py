"""Run the core with no web app. See `core/__init__.py`."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Optional

from api.activity import FLUSH_SECONDS as ACTIVITY_FLUSH_SECONDS
from api.activity import RETENTION_DAYS

from .build import CoreApp, build_core
from .config_boundary import CoreConfigError
from .control import ControlServer, socket_path_from_env

_logger = logging.getLogger("moreopenrepeater.core")

#: systemd should not restart-loop on a configuration the core refuses to run.
EXIT_CONFIG = 2


async def serve(
    app: CoreApp,
    stop: Optional[asyncio.Event] = None,
    control_socket: Optional[Path] = None,
    allow_simulate: bool = False,
) -> None:
    """Start the core, run until `stop` is set, then shut it down.

    The steps and their order are the core's half of `AppRuntime`'s lifespan:
    text-to-speech is waited for before audio and PTT come up, and `READY=1`
    follows the audio engines and links.
    """
    core = app.runtime
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    tasks: list[asyncio.Task] = []
    core.start_fan()
    core.start_tick(tasks)
    core.start_announcements(tasks)
    await core.start_tts()
    core.warm_renderer(loop)
    core.start_long_id(tasks)
    await core.start_audio_and_links(loop, tasks)
    core.notify_ready()
    core.start_io()
    tasks.append(asyncio.create_task(_keep_activity(app.activity_store)))
    control: Optional[ControlServer] = None
    if control_socket is not None:
        # Last, after the radio is up: if it cannot start the core simply runs without it.
        control = ControlServer(
            control_socket, service=app.service, runtime=core, allow_simulate=allow_simulate,
            assets_have=app.assets_store.has, log_hub=app.log_hub, activity_store=app.activity_store,
        )
        await control.start()
    _logger.info("core running alone (no web app); state is %s", app.state_path)
    await stop.wait()
    core.announce_stopping()
    if control is not None:
        await control.stop()
    for task in tasks:
        task.cancel()
    await core.stop_devices()
    app.activity_store.flush()
    await core.stop_tts()


async def _keep_activity(store) -> None:
    """Write recorded activity to disk every so often (the store holds rows back to spare the SD
    card) and drop what is older than the retention. The writes run off the event loop."""
    loop = asyncio.get_running_loop()
    since_prune = None
    while True:
        try:
            await loop.run_in_executor(None, store.flush)
            if since_prune is None or since_prune >= 24 * 3600:
                await loop.run_in_executor(None, store.prune, (datetime.now() - timedelta(days=RETENTION_DAYS)).timestamp())
                since_prune = 0.0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a full disk must not stop the radio
            _logger.exception("could not write the activity history")
        await asyncio.sleep(ACTIVITY_FLUSH_SECONDS)
        since_prune = (since_prune or 0.0) + ACTIVITY_FLUSH_SECONDS


def main(env: Optional[Mapping[str, str]] = None) -> int:
    try:
        app = build_core(env)
    except CoreConfigError as error:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        _logger.error("the core will not start: %s", error)
        print(f"moreopenrepeater-core: {error}", file=sys.stderr)
        return EXIT_CONFIG

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        environment = dict(os.environ if env is None else env)
        await serve(
            app, stop, socket_path_from_env(environment),
            allow_simulate=environment.get("MOREOPENREPEATER_CORE_SIMULATE", "").strip() == "1",
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
