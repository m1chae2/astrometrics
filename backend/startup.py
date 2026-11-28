"""Purpose: The work the backend does when it starts and while it runs.

Description: The app's lifespan in `backend.main_backend` calls these
functions. They:

* poll the telescope every two seconds so the app's telemetry stays fresh,
  and send the app the running jobs of the shared job list when they change
  (`periodic_telemetry_loop`, `publish_running_jobs`), so a job another
  program started (an AI tool, a console script) shows up at once;
* load the star catalog and other slow caches before the first screen
  needs them (`warm_start_up_caches`). The ``/api/ready`` route reports
  `sky_catalog_warmup_finished`, and the desktop shell keeps its splash
  screen up until it is set;
* build the depth goal status of the targets most likely to be opened first,
  so the first Image Processing visit finds it kept (`warm_depth_goals`);
* read the star-shape review, the frame states and the grouped frame list of
  those same targets, so the Image Processing panels of the first target
  open from warm caches (`warm_target_reviews`);
* build the three most recent night reviews once the screens are ready, so
  the Observatory Manager opens at once (`warm_night_reviews`);
* read those Image Processing reviews again in the background after a
  target's data changes during the night (`keep_target_reviews_warm`).
"""

import asyncio
import contextlib
import ctypes
import functools
import logging
import sys
import threading
import time

from backend.container import container
from backend.services.infrastructure.background_logging import background_log_context

logger = logging.getLogger(__name__)

#: Set once the start-up warm-up has finished, whether or not it succeeded.
#: The desktop shell polls ``/api/ready`` and holds its splash screen until
#: this is set, so the app never opens onto an empty Planetarium while the
#: catalog loads. It is set on failure too: a broken warm-up only means a
#: slow first load, and must never keep the app from opening.
sky_catalog_warmup_finished = threading.Event()


def publish_running_jobs() -> None:
    """Send the app the jobs that are running now, read from the job list.

    The job list is one table that every program shares: this backend, the
    MCP servers and console scripts all record their jobs in it. The app
    learns that a job started or ended from the ``processing`` part of the
    ``system_state_update`` event. A backend service updates that part when
    it starts or ends one of its own jobs, but a job recorded by another
    path (a library call that records its own job, another program) would
    not reach it. Reading the table here covers every job. The event goes
    out only when the running jobs changed.
    """
    if not container.initialized or container.job_service is None or container.astrometrics_service is None:
        return
    running = container.job_service.get_active_jobs()
    container.astrometrics_service.update_processing_jobs([
        {"target_id": job.target_id, "job_id": job.id, "status": job.status} for job in running
    ])


async def periodic_telemetry_loop() -> None:
    """Poll the telescope status and the running jobs every two seconds.

    Each poll updates the shared state, which sends the new telemetry and
    any change in the running jobs to every connected app window. The loop
    runs until it is cancelled.
    """
    while True:
        try:
            with background_log_context("telemetry"):
                await asyncio.to_thread(publish_running_jobs)
        except Exception:
            logger.exception("Error while reading the running jobs")
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
    * the Astronomy Manager's stellar summary cache;
    * the list of quality metrics. Building it imports every pipeline's
      metric list and SciPy's statistics module, which otherwise costs about
      half a second on the first star-shape or quality request.

    Each step logs its own failure and never stops the others.
    `sky_catalog_warmup_finished` is set when all of them are done.
    """

    def warm_sky() -> None:
        """Build the sky engine and load its catalog with a tiny query."""
        started_at = time.monotonic()
        try:
            container.wayfinder.planning.catalogs.query_sources(0.0, 0.0, 0.01)
            logger.info("Sky catalog warmed in %.1fs", time.monotonic() - started_at)
        except Exception:
            # Warm-up runs in the background; a failure only makes the first
            # request slower, so it is logged and the server keeps going.
            logger.warning("Sky catalog warm-up failed; first Planetarium load will be slow", exc_info=True)

    def warm_stellar_summaries() -> None:
        """Fill the stellar catalog summary cache."""
        try:
            stellar_started_at = time.monotonic()
            container.stellar_service.warm_catalog_summary_cache()
            logger.info("Stellar catalog summary warmed in %.1fs", time.monotonic() - stellar_started_at)
        except Exception:
            logger.warning(
                "Stellar summary warm-up failed; first Astronomy Manager load will be slow", exc_info=True
            )

    def warm_quality_metrics() -> None:
        """Build the list of quality metrics once, through the public API."""
        try:
            metrics_started_at = time.monotonic()
            container.astrometrics.quality.records.get_metrics()
            logger.info("Quality metric list loaded in %.1fs", time.monotonic() - metrics_started_at)
        except Exception:
            # Only the first quality request gets slower, so this is not a
            # warning the user needs.
            logger.debug("Quality metric list not loaded at start-up", exc_info=True)

    try:
        steps = [
            threading.Thread(target=step, name=step.__name__, daemon=True)
            for step in (warm_sky, warm_stellar_summaries, warm_quality_metrics)
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
    fails is logged as a warning and skipped, and the others are still built.

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
            nights = list(review.nights.query())
            for night in reversed(nights[-night_count:]):
                try:
                    review.nights.get(night.night_id, required=True)
                except Exception:
                    # A failed night only means a slower first open.
                    logger.warning("Night review warm-up failed for %s", night.night_id, exc_info=True)
            logger.info("Night reviews warmed in %.1fs", time.monotonic() - started_at)
        except Exception:
            logger.warning(
                "Night review warm-up failed; the first Observatory Manager open will be slow", exc_info=True
            )


DEPTH_GOAL_WARMUP_COUNT = 3
"""How many targets `warm_depth_goals` prepares.

Design estimate WAY-D108 (see ``validation/wayfinding.md``): the target the
telescope points at, plus the most recently imaged targets. The Image
Processing screen opens on the most recently imaged target that has a
picture, and the user usually looks at that one or the one before. One cold
build takes 1 to 2 s on the real library, so three targets keep the
background work to a few seconds.
"""


def _depth_goal_warmup_targets(count: int) -> list[str]:
    """Choose the targets whose depth goal is most likely to be read first.

    The target the telescope points at comes first (when the telemetry loop
    already knows it). The others are the targets with a picture, newest
    imaged first, which is how the Image Processing screen chooses the target
    it opens on. A target appears once.

    Parameters
    ----------
    count : `int`
        The most targets to return.

    Returns
    -------
    targets : `list` [`str`]
        Target ids, at most ``count``, in the order to build them.
    """
    chosen: list[str] = []
    telescope_state = container.astrometrics_service.get_state().get("telescope", {})
    pointed_at = telescope_state.get("targetName")
    if pointed_at:
        # The telescope reports a name; the library may spell the id otherwise.
        pointed_target = container.target_service.get_targets(str(pointed_at), detail="summary")
        if pointed_target is not None:
            chosen.append(str(pointed_target.id))
        else:
            logger.debug("Depth goal warm-up: %r is not a library target; skipped", pointed_at)

    imaged_at = container.target_service.get_camera_index()["targets"]
    with_picture = {
        str(target.id)
        for target in container.target_service.get_targets(detail="summary")
        if target.stacking.processed_image or target.stacking.stacked_image
    }
    newest_first = sorted(
        (target_id for target_id in imaged_at if target_id in with_picture),
        key=lambda target_id: imaged_at[target_id]["lastFrameTime"] or float("-inf"),
        reverse=True,
    )
    for target_id in newest_first:
        if target_id not in chosen:
            chosen.append(target_id)
    return chosen[:count]


def warm_depth_goals(target_count: int = DEPTH_GOAL_WARMUP_COUNT) -> list[str]:
    """Build the depth goal status of the likely first targets, one at a time.

    This runs after the other warm-ups, so it never competes with them. It
    calls the same function as the ``advisory:depth_goal`` request, so the
    first request for one of these targets finds the status kept. It only
    reads. A target that fails, or a library with no targets, is logged at
    debug level and skipped, since the only cost is a slower first visit.

    Parameters
    ----------
    target_count : `int`, optional
        The most targets to build. Defaults to `DEPTH_GOAL_WARMUP_COUNT`.

    Returns
    -------
    targets : `list` [`str`]
        The targets it chose, in order, so `warm_target_reviews` can warm
        the same ones. Empty when no target could be chosen.
    """
    with background_log_context("depth-goal-warmup"):
        started_at = time.monotonic()
        try:
            targets = _depth_goal_warmup_targets(target_count)
        except Exception:
            logger.debug("Depth goal warm-up could not choose targets", exc_info=True)
            return []
        built = 0
        for target_id in targets:
            try:
                container.advisory_service.get_depth_goal(target_id)
                built += 1
            except Exception:
                logger.debug("Depth goal warm-up skipped %s", target_id, exc_info=True)
        logger.info(
            "Depth goals warmed for %d of %d targets in %.1fs",
            built,
            len(targets),
            time.monotonic() - started_at,
        )
        return targets


def warm_target_reviews(target_id: str, log_level: int = logging.INFO) -> None:
    """Read one target's Image Processing reviews so their caches are warm.

    This runs after `warm_depth_goals`, once for each target it chose, and
    again after a target's data changes (`keep_target_reviews_warm`). Each
    target is its own step in the heavy lane, so a user's request waits for
    at most one target. It makes the same three calls the Image Processing
    screen makes when it opens a target:

    * the star-shape review (``advisory:star_shapes``), which loads the
      roundness limit and the per-frame star-shape rows;
    * the frame states (``target:get_frame_statuses``), which lists the
      frames that were set aside;
    * the frames grouped by filter (``target:get_frames_grouped``), which
      fills the dark-frame temperature cache.

    It only reads. A call that fails is logged at debug level and the others
    still run, since the only cost is a slower first visit. The reads are
    marked as background reads, so they do not count as the app asking about
    the target (`TargetReviewRewarmer.note_requested`).

    Parameters
    ----------
    target_id : `str`
        The target's id.
    log_level : `int`, optional
        The level of the "warmed" line. The night-time re-reads log it at
        debug level, so they do not fill the log.
    """
    rewarmer = container.target_review_rewarmer
    background = rewarmer.background_reads() if rewarmer is not None else contextlib.nullcontext()
    with background_log_context("target-review-warmup"), background:
        started_at = time.monotonic()
        calls = (
            ("star shapes", lambda: container.advisory_service.get_star_shape_review(target_id)),
            ("frame states", lambda: container.target_service.get_frame_statuses(target_id)),
            ("grouped frames", lambda: container.target_service.get_frame_stats_grouped(target_id)),
        )
        for name, call in calls:
            try:
                call()
            except Exception:
                logger.debug("Target review warm-up skipped the %s of %s", name, target_id, exc_info=True)
        elapsed = time.monotonic() - started_at
        logger.log(log_level, "Target reviews warmed for %s in %.1fs", target_id, elapsed)


async def keep_target_reviews_warm() -> None:
    """Run the night-time re-reads of the target reviews until cancelled.

    The services tell the container's `TargetReviewRewarmer` when a target's
    data changes (new frames, new quality rows, a finished stack, frames put
    back). This runs its loop, which waits for `sky_catalog_warmup_finished`
    and then calls `warm_target_reviews` for each changed target, one target
    per heavy-lane step and only when the lane is free. The app's lifespan
    starts it and cancels it at shutdown. Without a rewarmer (a container
    that was not built) it returns at once.
    """
    rewarmer = container.target_review_rewarmer
    if rewarmer is None:
        return
    await rewarmer.run(
        functools.partial(warm_target_reviews, log_level=logging.DEBUG), sky_catalog_warmup_finished
    )


# Windows job object limit flag: end every process in the job when the last
# handle to the job closes (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``).
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
# ``JobObjectExtendedLimitInformation`` in ``SetInformationJobObject``.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

# Keeps the job handle open as long as the backend runs. The system closes it
# when the backend ends for any reason, and that is what ends the helpers.
_backend_job_handle: int | None = None


def bind_helper_programs_to_backend_lifetime() -> bool:
    """On Windows, end every helper program when the backend ends.

    The backend joins a job object whose limit is "kill on job close". Every
    program the backend starts afterwards (Siril, GraXpert)
    joins the same job, because children inherit it unless started with
    ``CREATE_BREAKAWAY_FROM_JOB``, which the library never uses. The backend's
    children use no creation flags beyond the defaults, so they work in the
    job as before. When the backend ends, even by a crash or a kill, Windows
    closes the job handle and stops the helpers. On Linux the library asks
    the kernel to do this for each helper (``child_process_options`` in
    ``astrometricslib.foundation.system``); macOS has no such step.

    This is not a user error when it fails (the backend may already be in a
    job that forbids this, as under some launchers), so a failure is logged
    at a low level and start-up goes on.

    Returns
    -------
    bound : `bool`
        `True` when the backend joined the job, `False` on other systems or
        when Windows refused.
    """
    global _backend_job_handle
    if sys.platform != "win32":
        return False
    if _backend_job_handle is not None:
        return True
    try:
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            """The ``JOBOBJECT_BASIC_LIMIT_INFORMATION`` structure."""

            _fields_ = (
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            )

        class IoCounters(ctypes.Structure):
            """The ``IO_COUNTERS`` structure."""

            _fields_ = tuple(
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            )

        class ExtendedLimits(ctypes.Structure):
            """The ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` structure."""

            _fields_ = (
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            )

        def raise_job_error() -> None:
            """Turn the last Windows error code into an `OSError`.

            Raises
            ------
            OSError
                Always.
            """
            raise OSError(ctypes.get_last_error(), "Windows refused the job object call")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise_job_error()
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise_job_error()
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            raise_job_error()
    except (OSError, AttributeError, ValueError) as job_error:
        logger.warning("Helper programs are not tied to the backend's lifetime: %s", job_error)
        return False
    _backend_job_handle = job
    logger.info("Helper programs will end when the backend ends")
    return True
