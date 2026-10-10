"""Purpose: The work the backend does when it starts and while it runs.

Description: The app's lifespan in `backend.main_backend` calls these
functions. They:

* poll the telescope every two seconds so the app's telemetry stays fresh
  (`periodic_telemetry_loop`);
* load the star catalog and other slow caches before the first screen
  needs them (`warm_start_up_caches`). The ``/api/ready`` route reports
  `sky_catalog_warmup_finished`, and the desktop shell keeps its splash
  screen up until it is set.
"""

import asyncio
import logging
import threading
import time

from astrometricslib import warm_earth_orientation_data
from backend.container import container

logger = logging.getLogger(__name__)

#: Set once the start-up warm-up has finished, whether or not it succeeded.
#: The desktop shell polls ``/api/ready`` and holds its splash screen until
#: this is set, so the app never opens onto an empty Planetarium while the
#: catalog loads. It is set on failure too: a broken warm-up only means a
#: slow first load, and must never keep the app from opening.
sky_catalog_warmup_finished = threading.Event()


async def periodic_telemetry_loop() -> None:
    """Poll the telescope status every two seconds, until cancelled.

    Each poll updates the shared state, which sends the new telemetry to
    every connected app window.
    """
    while True:
        try:
            if container.initialized and container.telescope_service:
                # get_status() reaches async driver calls through
                # asyncio.run(), which fails on a thread that already runs an
                # event loop, like this one. A worker thread has no loop, and
                # it also keeps a slow hardware query from blocking every
                # other request.
                await asyncio.to_thread(container.telescope_service.get_status)
        except Exception:
            logger.exception("Error in periodic telemetry loop")
        await asyncio.sleep(2.0)


def warm_start_up_caches() -> None:
    """Fill the caches the first screens need, all at once, before first use.

    Three independent pieces of start-up work run side by side instead of one
    after another, so the splash waits for the slowest (about 2.6 s on the
    real library) rather than their sum (about 3.6 s):

    * the Planetarium's star catalog, which the first `planetarium:get_sources`
      request would otherwise load while the user waits on an empty sky;
    * astropy's Earth-orientation table, read by the first altitude/azimuth
      conversion;
    * the Astronomy Manager's stellar summary cache.

    Each step logs its own failure and never stops the others.
    `sky_catalog_warmup_finished` is set when all of them are done.
    """

    def warm_sky() -> None:
        """Build the sky engine and load its catalog with a tiny query."""
        started_at = time.monotonic()
        try:
            container.wayfinder.planning.get_sources(0.0, 0.0, 0.01)
            logger.info("Sky catalog warmed in %.1fs", time.monotonic() - started_at)
        except Exception:
            # Warm-up runs in the background; a failure only makes the first
            # request slower, so it is logged and the server keeps going.
            logger.exception("Sky catalog warm-up failed; first Planetarium load will be slow")

    def warm_earth_orientation() -> None:
        """Load astropy's Earth-orientation table."""
        try:
            elapsed = warm_earth_orientation_data()
            logger.info("Earth-orientation data loaded in %.1fs", elapsed)
        except Exception:
            logger.exception("Earth-orientation warm-up failed; the first star click will be slow")

    def warm_stellar_summaries() -> None:
        """Fill the stellar catalog summary cache."""
        try:
            stellar_started_at = time.monotonic()
            container.stellar_service.warm_catalog_summary_cache()
            logger.info("Stellar catalog summary warmed in %.1fs", time.monotonic() - stellar_started_at)
        except Exception:
            logger.exception("Stellar summary warm-up failed; first Astronomy Manager load will be slow")

    try:
        steps = [
            threading.Thread(target=step, name=step.__name__, daemon=True)
            for step in (warm_sky, warm_earth_orientation, warm_stellar_summaries)
        ]
        for step_thread in steps:
            step_thread.start()
        for step_thread in steps:
            step_thread.join()
    finally:
        sky_catalog_warmup_finished.set()
