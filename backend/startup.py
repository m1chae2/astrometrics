"""Purpose: The work the backend does when it starts and while it runs.

Description: The app's lifespan in `backend.main_backend` calls these
functions. They:

* poll the telescope every two seconds so the app's telemetry stays fresh
  (`periodic_telemetry_loop`);
* load the star catalog and other slow caches before the first screen
  needs them (`warm_start_up_caches`). The ``/api/ready`` route reports
  `sky_catalog_warmup_finished`, and the desktop shell keeps its splash
  screen up until it is set;
* build the three most recent night reviews once the screens are ready, so
  the Observatory Manager opens at once (`warm_night_reviews`).
"""

import asyncio
import logging
import threading
import time

from astrometricslib import warm_earth_orientation_data
from backend.container import container
from backend.services.infrastructure.background_logging import background_log_context

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
                with background_log_context("telemetry"):
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
            container.wayfinder.planning.query_sources(0.0, 0.0, 0.01, limit=None)
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


NIGHT_REVIEW_WARMUP_COUNT = 3
"""How many of the most recent nights `warm_night_reviews` builds.

Design estimate WAY-D103 (see ``validation/wayfinding.md``):
the Observatory Manager opens on the latest night, and a user looking back
usually looks at the night or two before. One build takes about 4 s on the
real library (the first one more), so three nights keep the background work
to about ten seconds.
"""


def warm_night_reviews(night_count: int = NIGHT_REVIEW_WARMUP_COUNT) -> None:
    """Build the most recent night reviews so the first open finds them kept.

    This runs after `warm_start_up_caches` has finished, so it never slows
    the splash screen. It only reads, starts no job and writes no record. It
    shares the wayfinder's review cache, so a later `history:night_quality`
    request for one of these nights is answered from memory. A night that
    fails is logged and skipped, and the others are still built.

    Parameters
    ----------
    night_count : `int`, optional
        How many of the newest nights to build. Defaults to
        `NIGHT_REVIEW_WARMUP_COUNT`.
    """
    with background_log_context("night-review-warmup"):
        started_at = time.monotonic()
        try:
            review = container.wayfinder.review
            nights = review.query(kind="nights", limit=night_count, register_job=False).night_list or []
            for night in reversed(list(nights)[-night_count:]):
                try:
                    review.query(kind="night_quality", session_id=night.night_id, register_job=False)
                except Exception:
                    # A failed night only means a slower first open.
                    logger.exception("Night review warm-up failed for %s", night.night_id)
            logger.info("Night reviews warmed in %.1fs", time.monotonic() - started_at)
        except Exception:
            logger.exception("Night review warm-up failed; the first Observatory Manager open will be slow")
