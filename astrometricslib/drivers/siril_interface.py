"""Siril driver for Astrometrics.

Wraps the Siril headless CLI (via named pipes) to build working
directories, run calibration/registration/stacking scripts, and
retrieve the resulting stacked image.
"""

import atexit
import contextlib
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ImageProcessing",
]

logger = logging.getLogger(__name__)

_active_image_processing_instances: weakref.WeakSet = weakref.WeakSet()

# Serializes Siril across every process on this machine. Siril is
# internally multithreaded and takes the whole box when it runs (400-650%
# CPU observed on a 12-core machine), so the batch runner's 3 concurrent
# target workers each launching their own Siril oversubscribes the CPU
# badly. That is not merely slower: on the 2026-08-23 DSLR run it pushed
# stacks past `pipeline_tasks.STACKING_TIMEOUT_SECONDS`, producing real
# failures ("[Sun] Stacking timed out after 600 seconds. Abandoning this
# stack.") alongside two 300s `solve-field` timeouts.
#
# A file lock rather than a `multiprocessing.Lock`: the batch workers are
# separate processes created by a pool this module knows nothing about,
# and `process_target` is also reachable from the backend and from
# scripts, which share no lock object with each other. A lock file in the
# system temp directory is the one rendezvous point all of them can find.
#
# Only the Siril run itself is serialized -- directory building, frame
# symlinking, and every non-Siril pipeline stage stay parallel, so the
# concurrency that is actually helping is preserved.
SIRIL_PROCESS_LOCK_PATH = os.path.join(tempfile.gettempdir(), "astrometricslib-siril.lock")


# Master bias/dark/flat frames are rebuilt from scratch for every target,
# even though the calibration frames themselves come from a shared library
# keyed by camera/ISO/exposure/filter -- so the *same* master is re-stacked
# once per target. Measured on the 2026-08-23 DSLR run: master-building
# cost 168-474s per target (~295s average) across 35 targets, but those 35
# targets resolve to only 9 distinct calibration combinations. Caching
# turns ~172 minutes of master-building into ~44.
#
# The cache key is derived from the source frames themselves (resolved
# path, size, mtime) rather than the camera/ISO/exposure metadata, so a
# changed, added, or removed calibration frame invalidates the master
# automatically instead of silently serving a stale one.
CALIBRATION_MASTER_CACHE_DIRECTORY_NAME = "MasterCache"

# How long an abandoned work directory is kept before a later run sweeps
# it away. A directory is only ever left behind by a failed stack or by a
# run that was interrupted before its cleanup ran; a successful stack
# removes its own. Seven days keeps a failure available for as long as
# anyone realistically investigates it while stopping cancelled runs from
# accumulating -- the 2026-08-23 run left 142GB across seven targets that
# was still sitting there a day later.
WORK_DIRECTORY_RETENTION_DAYS = 7


def purge_stale_work_directories(
    workdir: str, retention_days: int = WORK_DIRECTORY_RETENTION_DAYS
) -> tuple[int, int]:
    """Remove work directories left behind by failed or cancelled runs.

    Skips the master cache and anything modified within the retention
    window, so a directory belonging to a run in progress is never
    touched. Errors are swallowed per directory: reclaiming disk is
    never worth failing a run over.

    Parameters
    ----------
    workdir : `str`
        The Siril work root holding one directory per target.
    retention_days : `int`, optional
        Age past which an untouched directory is removed.

    Returns
    -------
    removed_count : `int`
        How many directories were removed.
    reclaimed_bytes : `int`
        Approximate total bytes freed.
    """
    if not os.path.isdir(workdir):
        return 0, 0

    cutoff_timestamp = time.time() - retention_days * 86400
    removed_count = 0
    reclaimed_bytes = 0

    for entry_name in os.listdir(workdir):
        if entry_name in {CALIBRATION_MASTER_CACHE_DIRECTORY_NAME, "Config"}:
            continue
        entry_path = os.path.join(workdir, entry_name)
        if not os.path.isdir(entry_path):
            continue
        try:
            if os.path.getmtime(entry_path) >= cutoff_timestamp:
                continue
            directory_bytes = 0
            for directory_path, _, file_names in os.walk(entry_path):
                for file_name in file_names:
                    with contextlib.suppress(OSError):
                        directory_bytes += os.path.getsize(os.path.join(directory_path, file_name))
            shutil.rmtree(entry_path)
        except OSError as purge_error:
            logger.debug("Could not purge stale work directory %s: %s", entry_path, purge_error)
            continue
        removed_count += 1
        reclaimed_bytes += directory_bytes

    if removed_count:
        logger.info(
            "Purged %d work director%s older than %d days, reclaiming %.1fGB.",
            removed_count,
            "y" if removed_count == 1 else "ies",
            retention_days,
            reclaimed_bytes / 1_000_000_000,
        )
    return removed_count, reclaimed_bytes


# The three master kinds, mapped to the staging subdirectory holding
# their source frames and the master filename Siril's script produces.
_CALIBRATION_MASTER_KINDS = (
    ("bias", "biases", "bias_stacked.fits"),
    ("dark", "darks", "dark_stacked.fits"),
    ("flat", "flats", "flat_stacked.fits"),
)


def _calibration_source_fingerprint(frames_directory: str) -> str | None:
    """Fingerprint the calibration frames staged in a directory.

    Each staged frame is a symlink into the shared calibration library,
    so the *link targets* are fingerprinted, not the links.

    Parameters
    ----------
    frames_directory : `str`
        Staging directory holding one master's source frames.

    Returns
    -------
    fingerprint : `str` or `None`
        A stable hex digest of the frame set, or `None` if the
        directory is empty or unreadable (nothing to cache).
    """
    import hashlib

    try:
        frame_names = sorted(os.listdir(frames_directory))
    except OSError:
        return None
    if not frame_names:
        return None

    digest = hashlib.sha256()
    for frame_name in frame_names:
        frame_path = os.path.join(frames_directory, frame_name)
        try:
            # stat() follows the symlink, so this describes the real
            # library frame rather than the link this run just made.
            stat_result = os.stat(frame_path)
            digest.update(os.path.realpath(frame_path).encode("utf-8"))
            digest.update(str(stat_result.st_size).encode("utf-8"))
            digest.update(str(int(stat_result.st_mtime)).encode("utf-8"))
        except OSError:
            # An unreadable frame is already skipped when the script is
            # built; refusing to cache keeps the key honest rather than
            # fingerprinting a set that will not match what Siril used.
            return None
    return digest.hexdigest()


# Seconds this process has spent blocked waiting for the Siril lock,
# accumulated across every acquisition since the last reset. A worker
# process runs one target at a time, so a module-level total is scoped
# to exactly one target's stacking attempt.
#
# The caller enforcing a stacking timeout reads this to extend its
# deadline: serialising Siril means a target can sit in the queue for
# minutes, and charging that wait to the target's own stacking budget
# turns a queue into a cascade of timeouts. On the 2026-08-24 run
# NGC 1499 was given 600s at 06:27:21 but did not reach Siril until
# 06:31:51, losing 4.5 of its 10 minutes before any work began.
_siril_lock_wait_state_lock = threading.Lock()
_siril_lock_wait_seconds = 0.0


def reset_siril_lock_wait_seconds() -> None:
    """Zero this process's accumulated Siril lock wait.

    Called before a stacking attempt begins so the total describes only
    that attempt.
    """
    global _siril_lock_wait_seconds
    with _siril_lock_wait_state_lock:
        _siril_lock_wait_seconds = 0.0


def get_siril_lock_wait_seconds() -> float:
    """Return seconds spent waiting for the Siril lock since the last reset.

    Returns
    -------
    wait_seconds : `float`
        Accumulated blocked time, ``0.0`` when the lock was always free.
    """
    with _siril_lock_wait_state_lock:
        return _siril_lock_wait_seconds


def _record_siril_lock_wait(wait_seconds: float) -> None:
    """Add one blocked interval to this process's accumulated wait.

    Parameters
    ----------
    wait_seconds : `float`
        Seconds spent blocked on this acquisition.
    """
    global _siril_lock_wait_seconds
    with _siril_lock_wait_state_lock:
        _siril_lock_wait_seconds += wait_seconds


@contextlib.contextmanager
def siril_process_lock(
    job_logger: logging.Logger | None = None, max_concurrent_runs: int | None = None
) -> Iterator[None]:
    """Hold one of a limited number of machine-wide Siril slots.

    Siril is internally multithreaded and takes most of the machine when
    it runs (400-688% CPU observed on 12 cores), so unbounded concurrent
    launches oversubscribe the box badly enough to push stacks past
    their timeout. A limit is therefore necessary -- but the limit is a
    tuning knob, not a constant.

    How many slots is read from ``[Processing.Parallelism]
    siril_concurrency``, the setting that already existed for exactly
    this purpose. An earlier version of this function took a single
    exclusive lock regardless, which silently overrode that setting and
    pinned the machine to one Siril at a time: on the 2026-08-24 run
    stacking was 44% of a 199-minute wall clock at roughly 57% CPU
    utilisation, so the serialisation itself became the bottleneck.

    Slots are POSIX advisory file locks via
    `datastore.disk_interface.acquire_resource_slot`, so they bind every
    process on the machine -- the batch script and the backend service
    both -- and the kernel releases them even if a holder is killed
    outright, so a crashed stack cannot wedge every later one.

    Parameters
    ----------
    job_logger : `logging.Logger`, optional
        Logger used to record that a run is waiting on another Siril
        run, so a stalled-looking job is explainable from its log.
    max_concurrent_runs : `int`, optional
        Slot count override, for benchmarking. Defaults to the
        configured `siril_concurrency`.

    Yields
    ------
    `None`
        Control returns to the caller holding a slot.
    """
    from datastore.disk_interface import acquire_resource_slot

    slot_count = max_concurrent_runs
    configuration = None
    if slot_count is None:
        try:
            from astrometricslib.utilities.config_loader import get_configuration

            configuration = get_configuration()
            slot_count = configuration.get_siril_concurrency()
        except Exception as configuration_error:
            # A missing configuration must not make Siril unrunnable;
            # one slot is the safe reading, matching the old behaviour.
            logger.debug("Could not read siril_concurrency, using 1 slot: %s", configuration_error)
            slot_count = 1
    slot_count = max(1, int(slot_count))

    waiting_message = (
        f"Waiting for a free Siril slot ({slot_count} allowed concurrently) before starting this run..."
    )
    logger.info(waiting_message)
    if job_logger:
        job_logger.info(waiting_message)

    wait_started_at = time.monotonic()
    with acquire_resource_slot(configuration, "siril", slot_count):
        # Recorded whether or not the wait was long: the stacking
        # timeout adds this back to its budget, and a queue that exists
        # to protect the CPU must not convert into a cascade of
        # timeouts for the targets waiting their turn.
        waited_seconds = time.monotonic() - wait_started_at
        _record_siril_lock_wait(waited_seconds)
        if waited_seconds >= 1.0:
            acquired_message = f"Waited {waited_seconds:.1f}s for a Siril slot."
            logger.info(acquired_message)
            if job_logger:
                job_logger.info(acquired_message)
        yield


def select_dominant_frame_dimensions(frame_paths: list[str]) -> tuple[set[str], tuple[int, int] | None]:
    """Keep only the frames sharing the most common image dimensions.

    Siril builds a sequence from frames of identical geometry and
    refuses anything else outright -- "Cannot add an image with
    different properties to an existing sequence" -- which fails the
    *whole* conversion, not just the offending frame. A handful of
    odd-sized files therefore costs a target its entire stack.

    Observed on the 2026-08-24 run: Sun had 4 stray frames (2402x1753,
    2286x2122, 1905x1689, 2233x1761) among 446 at 6000x4000 and lost all
    450; M 27 had 45 frames at 6016x4016 against 81 at 6000x4000 and
    lost all 126. Keeping the majority geometry costs those 4 frames of
    Sun's 450 and recovers everything else.

    Ties are resolved toward the larger frame count first and then the
    larger pixel area, so a genuinely split set prefers the more
    detailed geometry rather than whichever happened to be read first.

    Parameters
    ----------
    frame_paths : `list` [`str`]
        Candidate light-frame paths, already known to be readable.

    Returns
    -------
    kept_paths : `set` [`str`]
        Paths whose dimensions match the dominant geometry. Frames
        whose header cannot be read are kept, so this filter never
        removes a frame on the strength of a failed read.
    dominant_dimensions : `tuple` [`int`, `int`] or `None`
        The winning ``(NAXIS1, NAXIS2)``, or `None` when no header
        could be read at all.
    """
    from astropy.io import fits

    paths_by_dimensions: dict[tuple[int, int], list[str]] = {}
    unreadable_paths: list[str] = []
    for frame_path in frame_paths:
        try:
            header = fits.getheader(frame_path)
            dimensions = (int(header["NAXIS1"]), int(header["NAXIS2"]))
        except Exception:
            unreadable_paths.append(frame_path)
            continue
        paths_by_dimensions.setdefault(dimensions, []).append(frame_path)

    if not paths_by_dimensions:
        return set(frame_paths), None

    dominant_dimensions = max(
        paths_by_dimensions,
        key=lambda dimensions: (
            len(paths_by_dimensions[dimensions]),
            dimensions[0] * dimensions[1],
        ),
    )
    return set(paths_by_dimensions[dominant_dimensions]) | set(unreadable_paths), dominant_dimensions


def _frames_use_color_filter_array(frames_directory: str) -> bool:
    """Report whether a staged frame directory holds color (CFA) data.

    Siril's ``-cfa``/``-equalize_cfa``/``-debayer`` calibration flags are
    only meaningful for a sensor with a Bayer color filter array. Applied
    to a monochrome camera they are actively wrong: Siril logs "No Bayer
    pattern found in the header file", falls back to a *guessed* RGGB
    pattern, and demosaics anyway -- turning a 2D mono frame into a
    3-channel RGB one whose pixels are interpolations across neighbours
    that were never a color mosaic. Observed on a real ZWO ASI 533MM Pro
    (monochrome) run: every stacked product came out (3, 3008, 3008)
    instead of (3008, 3008), and intermediates grew ~6x (416MB -> 2.5GB).

    ``BAYERPAT`` is the authoritative marker and the same one Siril
    itself looks for: a CFA sensor writes it (e.g. "RGGB"), a mono sensor
    does not. Frame dimensionality deliberately is *not* used as a
    signal, because an undebayered CFA frame and a mono frame are both
    2D and indistinguishable by shape alone.

    Absence of the keyword is treated as monochrome -- the conservative
    reading, since guessing a pattern is exactly the failure being
    corrected here.

    Parameters
    ----------
    frames_directory : `str`
        Directory of staged frames to inspect.

    Returns
    -------
    uses_color_filter_array : `bool`
        `True` only if a frame declares a ``BAYERPAT``.
    """
    from astropy.io import fits

    try:
        frame_names = sorted(os.listdir(frames_directory))
    except OSError as directory_error:
        logger.debug("Could not list %s for CFA detection: %s", frames_directory, directory_error)
        return False

    for frame_name in frame_names:
        frame_path = os.path.join(frames_directory, frame_name)
        if not os.path.isfile(frame_path):
            continue
        try:
            bayer_pattern = fits.getheader(frame_path).get("BAYERPAT")
        except Exception as header_error:
            # A single unreadable frame must not decide the whole stack's
            # calibration mode; try the next one instead.
            logger.debug("Could not read header of %s: %s", frame_path, header_error)
            continue
        if bayer_pattern is not None and str(bayer_pattern).strip():
            return True
        # A readable header with no BAYERPAT is a definitive mono answer;
        # no need to open the rest of the sequence.
        return False

    return False


def _cleanup_all_active_image_processing_instances() -> None:
    """Clean up any ImageProcessing instances still alive at exit.

    Safety net registered with `atexit` so subprocesses started by
    an ImageProcessing instance are not leaked if the interpreter
    exits without an explicit `cleanup_subprocesses()` call.
    """
    for instance in list(_active_image_processing_instances):
        try:
            instance.cleanup_subprocesses()
        except Exception:
            logger.debug("Failed to clean up ImageProcessing subprocesses at exit", exc_info=True)


atexit.register(_cleanup_all_active_image_processing_instances)


class ImageProcessing:
    """Drive Siril to calibrate, register, and stack target frames.

    Builds the working directory Siril expects, launches Siril in
    headless mode over named pipes, and copies the resulting stacked
    image (and optional diagnostics) back out to the target library.
    """

    def __init__(self, config=None, calibration_library=None, job_repository=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the ImageProcessing class with optional dependencies."""
        from astrometricslib.utilities.calibration_library import CalibrationLibrary
        from astrometricslib.utilities.config_loader import get_configuration

        self.config = config or get_configuration()
        if calibration_library is None:
            self.calibration_library = CalibrationLibrary(app_config=self.config)
            self.calibration_library.load_library()
        else:
            self.calibration_library = calibration_library
        self.siril_executable = self.config.get_siril_executable()
        # Work directory should ideally be in config or a safe temp
        # location in workspace
        self.workdir = os.path.join(os.path.expanduser("~"), "Siril", "Work")
        self.subprocesses = []
        self.gui_launched = False
        self.job_repository = job_repository
        # Reset at the start of every process_target call; initialized
        # here too so build_directories can also be called standalone
        # (as existing tests do) without requiring a prior
        # process_target call.
        self.last_run_diagnostics: dict[str, Any] = {
            "corrupt_frames_skipped": [],
            "calibration_mismatch_flags": [],
        }
        _active_image_processing_instances.add(self)

    def reset_gui_flag(self) -> None:
        """Clear the flag tracking whether the Siril GUI was launched."""
        self.gui_launched = False

    def cleanup_subprocesses(self) -> None:
        """Terminate every Siril subprocess this instance has started."""
        for process in self.subprocesses:
            if process.poll() is None:
                self._kill_process_tree(process)

    def _kill_process_tree(
        self, process: subprocess.Popen, job_logger: logging.Logger | None = None, workdir: str | None = None
    ) -> None:
        """Terminate a Siril subprocess and its entire process group.

        Siril is launched via flatpak/bwrap, which sandboxes the
        actual `siril` binary as a descendant process. Signaling
        process.pid alone can miss that descendant entirely, leaving it
        running indefinitely as an orphan. We send SIGTERM and SIGKILL to
        the process group and clean up lingering child processes associated
        with the work directory pipes.
        """
        log = job_logger.info if job_logger else logger.info
        try:
            pgid = os.getpgid(process.pid)
            try:
                os.killpg(pgid, signal.SIGTERM)
            except Exception as exc:
                logger.debug("SIGTERM to process group %s failed (likely already exited): %s", pgid, exc)
            time.sleep(0.2)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception as exc:
                logger.debug("SIGKILL to process group %s failed (likely already exited): %s", pgid, exc)
            process.wait(timeout=2)
        except Exception:
            log("Process group termination completed.")

        if workdir and os.path.exists(workdir):
            try:
                import glob

                pipes = glob.glob(os.path.join(workdir, "siril_command.*"))
                for pipe in pipes:
                    try:
                        subprocess.run(
                            ["fuser", "-k", "-9", pipe],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception as exc:
                        logger.debug("fuser cleanup failed for pipe '%s': %s", pipe, exc)
                # Safety net for sandboxed processes linked to workdir pipes
                subprocess.run(
                    ["pkill", "-9", "-f", workdir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                logger.debug("Failed to force-kill lingering Siril processes for '%s': %s", workdir, exc)

    def _is_fits_file_readable(self, path: str) -> bool:
        """Read a FITS file's primary data to confirm it isn't corrupted.

        A single corrupted calibration or light frame handed to
        Siril can stall its ordered write queue indefinitely (the
        writer can never advance past the slot for a frame whose
        data failed to read), hanging the entire stacking job. This
        check lets callers filter such files out before they ever
        reach Siril.

        Returns
        -------
        is_readable : `bool`
            `True` if `path`'s primary data could be read; `False`
            if the file is missing data or fails to open.
        """
        try:
            from astropy.io import fits

            with fits.open(path, memmap=False) as hdul:
                hdu = hdul[1] if hdul[0].data is None and len(hdul) > 1 else hdul[0]
                if hdu.data is None:
                    return False
            return True
        except Exception:
            return False

    def restore_cached_calibration_masters(
        self, target_folder: str, job_logger: logging.Logger | None = None
    ) -> set[str]:
        """Copy any already-built masters for this frame set into place.

        See `_calibration_source_fingerprint` for why the same masters
        were previously rebuilt once per target and what that cost.

        Parameters
        ----------
        target_folder : `str`
            This run's staging directory.
        job_logger : `logging.Logger`, optional
            Logger for recording each cache hit.

        Returns
        -------
        restored_kinds : `set` [`str`]
            The master kinds ("bias"/"dark"/"flat") now present in the
            run's ``process`` directory, whose build steps the
            generated Siril script can therefore skip.
        """
        restored_kinds: set[str] = set()
        process_directory = os.path.join(target_folder, "process")
        os.makedirs(process_directory, exist_ok=True)

        for kind, frames_subdirectory, master_filename in _CALIBRATION_MASTER_KINDS:
            fingerprint = _calibration_source_fingerprint(os.path.join(target_folder, frames_subdirectory))
            if fingerprint is None:
                continue
            cached_master_path = os.path.join(
                self.workdir,
                CALIBRATION_MASTER_CACHE_DIRECTORY_NAME,
                f"{kind}_{fingerprint}.fits",
            )
            if not os.path.exists(cached_master_path):
                continue
            try:
                shutil.copy2(cached_master_path, os.path.join(process_directory, master_filename))
            except OSError as copy_error:
                # A failed restore is not fatal: leaving the kind out of
                # restored_kinds makes the script rebuild it as before.
                logger.debug("Could not restore cached %s master: %s", kind, copy_error)
                continue
            restored_kinds.add(kind)
            if job_logger:
                job_logger.info(f"Reusing cached master {kind} frame (fingerprint {fingerprint[:12]}).")
            # Also emitted on this module's own logger, which propagates
            # to the "astrometricslib" package logger that carries the
            # database handler. `process_target`'s job_logger sets
            # propagate=False and only gains a DbLogHandler when a job
            # repository was supplied, which the batch path does not do,
            # so hits were recorded solely in per-target file logs and a
            # run appeared to have zero cache reuse while actually
            # serving 59 masters from cache.
            logger.info("Reusing cached master %s frame (fingerprint %s).", kind, fingerprint[:12])

        return restored_kinds

    def store_calibration_masters_in_cache(
        self, target_folder: str, job_logger: logging.Logger | None = None
    ) -> None:
        """Save this run's freshly built masters for later runs to reuse.

        Writes via a temporary file plus `os.replace`, so a reader can
        never observe a half-written master even though several runs
        may finish around the same time.

        Parameters
        ----------
        target_folder : `str`
            This run's staging directory.
        job_logger : `logging.Logger`, optional
            Logger for recording each master cached.
        """
        cache_directory = os.path.join(self.workdir, CALIBRATION_MASTER_CACHE_DIRECTORY_NAME)
        try:
            os.makedirs(cache_directory, exist_ok=True)
        except OSError as cache_directory_error:
            logger.debug("Could not create calibration master cache: %s", cache_directory_error)
            return

        for kind, frames_subdirectory, master_filename in _CALIBRATION_MASTER_KINDS:
            built_master_path = os.path.join(target_folder, "process", master_filename)
            if not os.path.exists(built_master_path):
                continue
            fingerprint = _calibration_source_fingerprint(os.path.join(target_folder, frames_subdirectory))
            if fingerprint is None:
                continue
            cached_master_path = os.path.join(cache_directory, f"{kind}_{fingerprint}.fits")
            if os.path.exists(cached_master_path):
                continue
            partial_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=cache_directory, suffix=".partial", delete=False
                ) as partial_file:
                    partial_path = partial_file.name
                shutil.copy2(built_master_path, partial_path)
                os.replace(partial_path, cached_master_path)
            except OSError as store_error:
                logger.debug("Could not cache %s master: %s", kind, store_error)
                if partial_path is not None:
                    with contextlib.suppress(OSError):
                        os.unlink(partial_path)
                continue
            if job_logger:
                job_logger.info(f"Cached master {kind} frame (fingerprint {fingerprint[:12]}).")

    def build_directories(
        self,
        id: str,
        image_files: Any,
        camera_filter: str | None = None,
        job_logger: logging.Logger | None = None,
    ) -> str:
        """Create working directories for processing and symlink frames in.

        Parameters
        ----------
        id : `str`
            Target identifier used to name the working folder.
        image_files : `Any`
            Either a list of frame records (dict or FrameRecord-like
            objects with ``path``/``camera``/``telescope``/``iso``/
            ``exposure``/``filter`` attributes) or a legacy nested
            dict keyed by telescope/camera/iso/exposure/filter.
        camera_filter : `str`, optional
            Camera name to restrict light frames to. If `None`
            (default), it is inferred from the first frame, falling
            back to ``"ZWO ASI 533MM Pro"``.
        job_logger : `logging.Logger`, optional
            Logger to record progress to. If `None` (default), the
            module logger is used.

        Returns
        -------
        target_folder : `str`
            Path to the populated working directory for this target.
        """
        log = job_logger.info if job_logger else logger.info

        if not camera_filter:
            if isinstance(image_files, list) and len(image_files) > 0:
                f = image_files[0]
                camera_filter = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", None)

            if not camera_filter:
                camera_filter = "ZWO ASI 533MM Pro"

        target_folder = os.path.join(self.workdir, id)
        for folder in ["biases", "darks", "flats", "lights", "process"]:
            folder_path = os.path.join(target_folder, folder)
            if os.path.exists(folder_path):
                shutil.rmtree(folder_path)
            os.makedirs(folder_path, exist_ok=True)

        library = self.calibration_library

        light_idx = 0
        dark_idx = 0
        bias_idx = 0
        flat_idx = 0

        def find_readable_paths(candidate_paths: list[str]) -> set:
            """Check FITS readability for many candidate paths concurrently.

            Each check opens a file with astropy and reads its
            header, which releases the GIL during the actual disk
            read, so a small thread pool overlaps I/O across
            candidates instead of checking them one at a time.

            Returns
            -------
            readable_paths : `set`
                Subset of `candidate_paths` whose FITS data read
                successfully.
            """
            if not candidate_paths:
                return set()
            with ThreadPoolExecutor(max_workers=8) as pool:
                is_readable_by_path = dict(
                    zip(candidate_paths, pool.map(self._is_fits_file_readable, candidate_paths), strict=False)
                )
            return {path for path, is_readable in is_readable_by_path.items() if is_readable}

        # Handle FrameRecord list (Modern)
        if isinstance(image_files, list) and len(image_files) > 0:
            log(f"Processing frame list for {id}")
            # Group by categories to find calibrations
            candidate_light_paths = [
                frame.get("path") if isinstance(frame, dict) else getattr(frame, "path", "")
                for frame in image_files
                if (frame.get("camera") if isinstance(frame, dict) else getattr(frame, "camera", "Unknown"))
                == camera_filter
            ]
            readable_light_paths = find_readable_paths(candidate_light_paths)

            # Applied after the readability filter so a corrupt frame
            # cannot skew which geometry looks dominant.
            readable_light_paths, dominant_dimensions = select_dominant_frame_dimensions(
                sorted(readable_light_paths)
            )
            excluded_for_dimensions = [
                path for path in candidate_light_paths if path and path not in readable_light_paths
            ]
            mismatched_paths = [
                path
                for path in excluded_for_dimensions
                if path not in self.last_run_diagnostics.get("corrupt_frames_skipped", [])
            ]
            if mismatched_paths and dominant_dimensions:
                log(
                    f"Excluding {len(mismatched_paths)} frame(s) whose dimensions differ from the "
                    f"dominant {dominant_dimensions[0]}x{dominant_dimensions[1]}; Siril cannot build "
                    f"a sequence from mixed geometry."
                )
                self.last_run_diagnostics.setdefault("dimension_mismatched_frames", []).extend(
                    mismatched_paths
                )

            for frame in image_files:
                # Expecting dict if serialized, or FrameRecord if internal
                path = frame.get("path") if isinstance(frame, dict) else getattr(frame, "path", "")
                cam = frame.get("camera") if isinstance(frame, dict) else getattr(frame, "camera", "Unknown")

                if cam != camera_filter:
                    continue

                if path not in readable_light_paths:
                    log(f"Skipping corrupted/unreadable light frame: {path}")
                    self.last_run_diagnostics.setdefault("corrupt_frames_skipped", []).append(path)
                    continue

                dst_name = f"light_source_{light_idx:05d}.fits"
                try:
                    os.symlink(path, os.path.join(target_folder, "lights", dst_name))
                    # Tracked so the per-frame .lst star lists
                    # (0-indexed in the same symlink order) can be
                    # matched back to their original submitted paths
                    # for the spectral registration-quality check.
                    self.last_run_diagnostics.setdefault("symlinked_light_paths", []).append(path)
                    light_idx += 1
                except Exception as e:
                    log(f"Error symlinking light {path}: {e}")

            # Populate calibrations from the first matching frame
            matching_frames = [
                f
                for f in image_files
                if (f.get("camera") if isinstance(f, dict) else getattr(f, "camera", "")) == camera_filter
            ]
            if matching_frames:
                f = matching_frames[0]
                tel = f.get("telescope") if isinstance(f, dict) else getattr(f, "telescope", "Apertura 75Q")
                cam = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", camera_filter)
                iso = f.get("iso") if isinstance(f, dict) else getattr(f, "iso", "800")
                exp = f.get("exposure") if isinstance(f, dict) else getattr(f, "exposure", "0")
                filt = f.get("filter") if isinstance(f, dict) else getattr(f, "filter", "None")

                def soft_flag_calibration_mismatch(
                    master_paths: list[str], master_kind: str, check_exposure: bool
                ) -> None:
                    """Log, without blocking, a relaxed-match gain mismatch.

                    Checks whether a relaxed-matched calibration
                    master's own gain (and, for darks only,
                    exposure) looks incompatible with the light
                    frames it'll be applied to.

                    calibration_library.py's get_dark_frames/
                    get_bias_frames/get_flat_frames are documented
                    to deliberately accept a
                    mismatched-gain master over having none at all.
                    This check doesn't override that: it only
                    surfaces the mismatch as a soft flag, checked
                    against the first matched master file as a
                    low-cost approximation rather than reading every
                    matched file's header. check_exposure must be
                    False for bias/flat masters -- their exposure
                    times are unrelated to the light frames' by
                    design (bias is near-zero, flats are set by the
                    flat panel's brightness), so comparing them
                    against light exposure would flag normal,
                    correct calibration setups as mismatched.
                    """
                    if not master_paths:
                        return
                    from astropy.io import fits

                    from astrometricslib.tasks.target_tasks.frame_homogeneity import (
                        is_calibration_gain_compatible,
                        is_dark_calibration_metadata_compatible,
                    )

                    try:
                        with fits.open(master_paths[0], memmap=False) as hdul:
                            header = hdul[0].header
                        master_iso = str(header.get("ISOSPEED", header.get("GAIN", iso)))
                        master_exp = float(header.get("EXPTIME", exp))
                    except Exception:
                        return

                    if check_exposure:
                        compatible = is_dark_calibration_metadata_compatible(
                            light_exposure=float(exp),
                            light_gain=str(iso),
                            master_exposure=master_exp,
                            master_gain=master_iso,
                        )
                    else:
                        compatible = is_calibration_gain_compatible(
                            light_gain=str(iso), master_gain=master_iso
                        )

                    if not compatible:
                        exposure_note = (
                            f" exposure={master_exp}s vs light frames'... exposure={exp}s"
                            if check_exposure
                            else ""
                        )
                        message = (
                            f"{master_kind} master '{master_paths[0]}' has gain={master_iso} vs "
                            f"light frames' gain={iso}.{exposure_note}"
                        )
                        log(f"Calibration metadata mismatch (soft flag, master still applied): {message}")
                        self.last_run_diagnostics.setdefault("calibration_mismatch_flags", []).append(message)

                dark_frame_paths = library.get_dark_frames(camera=cam, iso=iso, exposure=exp)
                soft_flag_calibration_mismatch(dark_frame_paths, "dark", check_exposure=True)
                readable_dark_paths = find_readable_paths(dark_frame_paths)
                for item in dark_frame_paths:
                    if item not in readable_dark_paths:
                        log(f"Skipping corrupted/unreadable dark frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "darks", f"dark_{dark_idx:05d}.fits"))
                        dark_idx += 1
                    except Exception as e:
                        log(f"Error symlinking dark {item}: {e}")

                bias_frame_paths = library.get_bias_frames(camera=cam, iso=iso)
                soft_flag_calibration_mismatch(bias_frame_paths, "bias", check_exposure=False)
                readable_bias_paths = find_readable_paths(bias_frame_paths)
                for item in bias_frame_paths:
                    if item not in readable_bias_paths:
                        log(f"Skipping corrupted/unreadable bias frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "biases", f"bias_{bias_idx:05d}.fits"))
                        bias_idx += 1
                    except Exception as e:
                        log(f"Error symlinking bias {item}: {e}")

                flat_frame_paths = library.get_flat_frames(
                    telescope=tel, camera=cam, filter_type=filt, iso=iso
                )
                soft_flag_calibration_mismatch(flat_frame_paths, "flat", check_exposure=False)
                readable_flat_paths = find_readable_paths(flat_frame_paths)
                for item in flat_frame_paths:
                    if item not in readable_flat_paths:
                        log(f"Skipping corrupted/unreadable flat frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "flats", f"flat_{flat_idx:05d}.fits"))
                        flat_idx += 1
                    except Exception as e:
                        log(f"Error symlinking flat {item}: {e}")

            return target_folder

        # Handle Legacy 5-Level Structure
        if isinstance(image_files, dict):
            for telescope, tel_val in image_files.items():
                if not isinstance(tel_val, dict):
                    continue
                for camera, cam_val in tel_val.items():
                    if camera != camera_filter:
                        continue
                    for iso, iso_val in cam_val.items():
                        if not isinstance(iso_val, dict):
                            continue
                        for exptime, exp_val in iso_val.items():
                            if not isinstance(exp_val, dict):
                                continue
                            for filt, filter_val in exp_val.items():
                                file_list = []
                                if isinstance(filter_val, list):
                                    file_list = filter_val
                                elif isinstance(filter_val, dict):
                                    for date_files in filter_val.values():
                                        if isinstance(date_files, list):
                                            file_list.extend(date_files)

                                for item in file_list:
                                    path = item.get("path") if isinstance(item, dict) else item
                                    try:
                                        os.symlink(
                                            path,
                                            os.path.join(
                                                target_folder, "lights", f"light_source_{light_idx:05d}.fits"
                                            ),
                                        )
                                        light_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink light frame '%s': %s", path, exc)

                                # Calibrations per group
                                for item in library.get_dark_frames(camera=camera, iso=iso, exposure=exptime):
                                    try:
                                        os.symlink(
                                            item,
                                            os.path.join(target_folder, "darks", f"dark_{dark_idx:05d}.fits"),
                                        )
                                        dark_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink dark frame '%s': %s", item, exc)
                                for item in library.get_bias_frames(camera=camera, iso=iso):
                                    try:
                                        os.symlink(
                                            item,
                                            os.path.join(
                                                target_folder, "biases", f"bias_{bias_idx:05d}.fits"
                                            ),
                                        )
                                        bias_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink bias frame '%s': %s", item, exc)
                                for item in library.get_flat_frames(
                                    telescope=telescope, camera=camera, filter_type=filt, iso=iso
                                ):
                                    try:
                                        os.symlink(
                                            item,
                                            os.path.join(target_folder, "flats", f"flat_{flat_idx:05d}.fits"),
                                        )
                                        flat_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink flat frame '%s': %s", item, exc)

        return target_folder

    def create_named_pipes(self, base_path: str) -> tuple[str, str]:
        """Create the FIFOs used to send commands to/read output from Siril.

        Parameters
        ----------
        base_path : `str`
            Directory in which to create the named pipes.

        Returns
        -------
        command_pipe : `str`
            Path to the pipe Siril reads commands from.
        output_pipe : `str`
            Path to the pipe Siril writes status output to.
        """
        command_pipe = os.path.join(base_path, "siril_command.in")
        output_pipe = os.path.join(base_path, "siril_command.out")
        for pipe in [command_pipe, output_pipe]:
            if os.path.exists(pipe):
                os.remove(pipe)
            os.mkfifo(pipe)
            # Owner-only: both ends (this process and the spawned Siril
            # subprocess) run as the current user, so wider access isn't
            # needed.
            os.chmod(pipe, 0o600)
        return command_pipe, output_pipe

    def run_siril_headless(
        self,
        command_pipe: str,
        output_pipe: str,
        stdout=subprocess.DEVNULL,  # ruff: ignore[missing-type-function-argument]
        stderr=subprocess.DEVNULL,  # ruff: ignore[missing-type-function-argument]
        job_logger: logging.Logger | None = None,
    ) -> subprocess.Popen:
        """Launch Siril in headless mode, reading/writing over named pipes.

        Parameters
        ----------
        command_pipe : `str`
            Path to the FIFO Siril should read commands from.
        output_pipe : `str`
            Path to the FIFO Siril should write status output to.
        stdout : optional
            Destination for the subprocess's stdout stream.
        stderr : optional
            Destination for the subprocess's stderr stream.
        job_logger : `logging.Logger`, optional
            Logger to record the launch command to. If `None`
            (default), nothing is logged.

        Returns
        -------
        process : `subprocess.Popen`
            The running Siril subprocess, also appended to
            ``self.subprocesses`` for later cleanup.
        """
        import shlex

        parts = shlex.split(self.siril_executable)
        if "flatpak" in parts[0]:
            if "run" in parts:
                idx = parts.index("run")
                parts.insert(idx + 1, "--filesystem=host")
            parts.insert(-1, "--command=sh")
            args = [*parts, "-c", f"siril -p -r {command_pipe} -w {output_pipe}"]
        else:
            args = [*parts, "-p", "-r", command_pipe, "-w", output_pipe]

        if job_logger:
            job_logger.info(f"Executing: {' '.join(args)}")
        # start_new_session=True makes this process the leader of its
        # own process group, so the whole sandboxed tree
        # (flatpak/bwrap/siril) can be killed together via os.killpg
        # later -- signaling process.pid alone can miss the actual
        # siril binary running as a descendant inside the sandbox.
        process = subprocess.Popen(args, stdout=stdout, stderr=stderr, start_new_session=True)
        self.subprocesses.append(process)
        time.sleep(2)
        return process

    def send_commands(
        self, command_pipe: str, commands: list[str], job_logger: logging.Logger | None = None
    ) -> None:
        """Write a sequence of commands to the Siril command pipe.

        Parameters
        ----------
        command_pipe : `str`
            Path to the FIFO to write commands to.
        commands : `list` [`str`]
            Siril script commands to send, in order. An ``"exit"``
            command is always appended after them.
        job_logger : `logging.Logger`, optional
            Logger to record write errors to. If `None` (default),
            errors are silently swallowed.
        """
        try:
            with open(command_pipe, "w") as pipe:
                for cmd in commands:
                    pipe.write(cmd + "\n")
                    pipe.flush()
                    time.sleep(0.05)
                pipe.write("exit\n")
                pipe.flush()
        except Exception as e:
            if job_logger:
                job_logger.error(f"Pipe write error: {e}")

    def read_output(
        self,
        output_pipe: str,
        target_folder: str,
        target_name: str,
        output_file: str | None = None,
        library_dest: str | None = None,
        job_logger: logging.Logger | None = None,
        generate_rejmap: bool = False,
        registered_seq_name: str | None = None,
    ) -> str | None:
        """Read Siril's status output and copy out the finished stack.

        Blocks reading ``output_pipe`` line by line until Siril
        reports success or error, then copies the resulting stacked
        file (and, if requested, the rejection map and registration
        sequence) to its final destination.

        Parameters
        ----------
        output_pipe : `str`
            Path to the FIFO Siril writes status output to.
        target_folder : `str`
            Working directory containing the ``process`` scratch
            subdirectory Siril wrote its output into.
        target_name : `str`
            Target name used to build the output filename.
        output_file : `str`, optional
            Explicit output filename to use when ``library_dest`` is
            not given. If `None` (default), a name derived from
            ``target_name`` is used.
        library_dest : `str`, optional
            Directory to copy the final stacked file into. If `None`
            (default), the file is written inside ``target_folder``.
        job_logger : `logging.Logger`, optional
            Logger to record progress and errors to. If `None`
            (default), the module logger is used.
        generate_rejmap : `bool`, optional
            Whether to also copy out the ``-rejmap`` output.
            Default is `False`.
        registered_seq_name : `str`, optional
            Base name of the registered ``.seq`` file to copy out.
            If `None` (default), no registration sequence is copied.

        Returns
        -------
        final_file_path : `str` or `None`
            Path to the copied stacked file, or `None` if Siril
            reported an error or no result was found.
        """
        log = job_logger.info if job_logger else logger.info
        try:
            with open(output_pipe) as pipe:
                for line in pipe:
                    log(line.strip())
                    if "status: success stack" in line:
                        stacked_file = os.path.join(target_folder, "process", "result_stacked.fits")
                        if not os.path.exists(stacked_file):
                            stacked_file = os.path.join(target_folder, "process", "result_stacked.fit")

                        if not os.path.exists(stacked_file):
                            continue

                        safe_name = target_name.replace(" ", "_")
                        final_filename = output_file if output_file else f"{safe_name}_Stacked.fits"

                        if library_dest:
                            os.makedirs(library_dest, exist_ok=True)
                            final_file_path = os.path.join(library_dest, final_filename)
                        else:
                            final_file_path = os.path.join(target_folder, final_filename)

                        shutil.copy(stacked_file, final_file_path)

                        # The -rejmap option writes its output next to
                        # result_stacked in the same scratch "process"
                        # directory, which is removed once
                        # process_target returns. Copy it out
                        # alongside the stacked result (derivable
                        # from the returned path by appending
                        # "_RejMap" to its stem) so callers can still
                        # inspect it after the scratch directory is
                        # gone.
                        if generate_rejmap:
                            stacked_stem = os.path.splitext(os.path.basename(stacked_file))[0]
                            rejmap_src = None
                            for ext in (".fits", ".fit"):
                                candidate = os.path.join(
                                    target_folder, "process", f"{stacked_stem}_low+high_rejmap{ext}"
                                )
                                if os.path.exists(candidate):
                                    rejmap_src = candidate
                                    break
                            if rejmap_src:
                                rejmap_dest = os.path.splitext(final_file_path)[0] + "_RejMap.fits"
                                shutil.copy(rejmap_src, rejmap_dest)
                            else:
                                log("Rejection map was requested but no rejmap output was found.")

                        # The .seq file's R<n> lines hold each input
                        # frame's own FWHM as measured by Siril's
                        # findstar pass during registration -- exactly
                        # the "median input FWHM" a post-stack
                        # FWHM-degradation check needs, computed at no
                        # extra cost. Same preservation pattern as the
                        # rejmap above: copy it out before the scratch
                        # directory is removed.
                        #
                        # The *input* sequence is preserved, not the
                        # registered "r_" output. Both carry identical
                        # findstar columns, but registration writes its
                        # computed transforms back into the input
                        # sequence, while the r_ frames are already
                        # physically aligned and so record an identity
                        # matrix for every frame. Preserving r_ therefore
                        # silently discarded every frame's dx/dy shift:
                        # all 238 frames measured before this fix stored
                        # dx=0, dy=0, which is precisely the per-frame
                        # drift series needed to see tracking error,
                        # polar misalignment, or a mount's periodic
                        # error. Confirmed on a real M 106 run, same
                        # frames in both files:
                        #   pp_light_source.seq  -> H ... 6.31985 ... 5.55176
                        #   r_pp_light_source.seq -> H 1 0 0 0 1 0 0 0 1
                        if registered_seq_name:
                            seq_src = os.path.join(target_folder, "process", f"{registered_seq_name}.seq")
                            if os.path.exists(seq_src):
                                seq_dest = os.path.splitext(final_file_path)[0] + "_Registration.seq"
                                shutil.copy(seq_src, seq_dest)
                            else:
                                log("Registration sequence file was expected but not found.")

                        should_open_gui = (
                            not self.gui_launched
                            and os.environ.get("HEADLESS") != "1"
                            and self.config.get_auto_open_siril_gui()
                        )
                        if should_open_gui:
                            self.launch_siril_gui(final_file_path)
                            self.gui_launched = True
                        return final_file_path
                    elif "status: error" in line:
                        return None
                return None
        except Exception as e:
            if job_logger:
                job_logger.error(f"Pipe read error: {e}")
            return None

    def process_target(
        self,
        id: str,
        image_files: Any,
        output_file: str | None = None,
        camera_filter: str | None = None,
        log_file: str | None = None,
        is_spectral: bool = False,
        job_id: str | None = None,
        rejection_sigma: tuple[float, float] | None = None,
        filter_wfwhm: str | None = None,
        filter_round: str | None = None,
        stack_weight: str | None = None,
        generate_rejmap: bool | None = None,
    ) -> str | None:
        """Calibrate, register, and stack a target's frames via Siril.

        Builds the working directory, drives Siril through a
        generated headless script (master calibration generation,
        registration, and rejection-stacking), and copies the
        resulting stacked file out to the target library.

        Parameters
        ----------
        id : `str`
            Target identifier used to name the working folder and
            log file.
        image_files : `Any`
            Frame list or legacy nested dict, as accepted by
            `build_directories`.
        output_file : `str`, optional
            Explicit output filename. If `None` (default), a name
            derived from ``id`` is used.
        camera_filter : `str`, optional
            Camera name to restrict light frames to. If `None`
            (default), it is inferred from the first frame.
        log_file : `str`, optional
            Path to write the job's Siril log to. If `None`
            (default), a path under the configured logs directory is
            used.
        is_spectral : `bool`, optional
            Whether these are spectroscopy frames, which use a
            shift-only registration transform. Default is `False`.
        job_id : `str`, optional
            Job identifier used to report progress to the job
            repository. If `None` (default), progress isn't
            reported.
        rejection_sigma : `tuple` [`float`, `float`], optional
            Explicit ``(low, high)`` rejection sigma override. If
            `None` (default), the sigma is derived from the
            configured rejection mode.
        filter_wfwhm : `str`, optional
            Siril ``-filter-wfwhm`` value. If `None` (default), the
            configured default is used.
        filter_round : `str`, optional
            Siril ``-filter-round`` value. If `None` (default), the
            configured default is used.
        stack_weight : `str`, optional
            Siril ``-weight`` value. If `None` (default), the
            configured default is used.
        generate_rejmap : `bool`, optional
            Whether to generate a rejection map. If `None` (default),
            the configured default is used.

        Returns
        -------
        result : `str` or `None`
            Path to the final stacked file, or `None` if the job
            failed.
        """
        self.reset_gui_flag()
        # Populated as process_target runs, read by
        # stacking_operations.process_target immediately afterward
        # to assemble a StackQualitySummary -- corrupt-frame skips
        # and calibration-mismatch soft-flags aren't otherwise
        # derivable from the returned stacked path, unlike the
        # rejmap/registration data which are recoverable from files
        # copied out alongside it.
        self.last_run_diagnostics: dict[str, Any] = {
            "corrupt_frames_skipped": [],
            "calibration_mismatch_flags": [],
        }
        if filter_wfwhm is None:
            filter_wfwhm = self.config.get_stack_filter_wfwhm_percentile()
        if filter_round is None:
            filter_round = self.config.get_stack_filter_round_percentile()
        if stack_weight is None:
            stack_weight = self.config.get_stack_weight()
        if generate_rejmap is None:
            generate_rejmap = self.config.get_stack_generate_rejmap()
        job_logger = logging.getLogger(f"siril_{id}")
        job_logger.setLevel(logging.INFO)
        # Isolate this job's log to its own dedicated file (below)
        # instead of bubbling up to the root logger, which may be
        # shared with unrelated activity in the calling process
        # (e.g. the backend/planetarium server).
        job_logger.propagate = False
        if job_logger.handlers:
            job_logger.handlers.clear()
        if not log_file:
            try:
                logs_path = self.config.get_logs_path()
                safe_id = id.replace(" ", "_")
                log_file = os.path.join(str(logs_path), f"stack_{safe_id}.log")
            except Exception:
                log_file = "siril.log"

        handler = logging.FileHandler(log_file, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        job_logger.addHandler(handler)

        db_handler = None
        if self.job_repository is not None:
            from astrometricslib.drivers.logger_interface import DbLogHandler

            db_handler = DbLogHandler(self.job_repository, job_id=job_id)
            job_logger.addHandler(db_handler)

        job_logger.info(f"JOB START: {id}")

        try:
            frames_path = self.config.get_frames_path()
            library_dest = os.path.join(frames_path, "lights", id)
        except Exception:
            library_dest = None

        if not camera_filter:
            if isinstance(image_files, list) and len(image_files) > 0:
                f = image_files[0]
                camera_filter = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", None)

            if not camera_filter:
                camera_filter = "ZWO ASI 533MM Pro"

        target_folder = None
        res = None
        siril_lock = contextlib.ExitStack()
        try:
            safe_id = id.replace(" ", "_")
            target_folder = self.build_directories(
                safe_id, image_files, camera_filter=camera_filter, job_logger=job_logger
            )
            command_pipe, output_pipe = self.create_named_pipes(target_folder)

            # Get actual counts to handle 1-frame sequence issue in
            # Siril. Siril's 'convert' does not create a .seq file
            # for a single input frame, which causes subsequent
            # 'calibrate' or 'stack' commands to fail.
            num_biases = len(os.listdir(os.path.join(target_folder, "biases")))
            num_darks = len(os.listdir(os.path.join(target_folder, "darks")))
            num_flats = len(os.listdir(os.path.join(target_folder, "flats")))
            num_lights = len(os.listdir(os.path.join(target_folder, "lights")))

            # Decided from the lights, then applied to the flats too: both
            # sets come from the same camera for a given stack (frames are
            # filtered by camera_filter above), and mixing modes between
            # them would calibrate a debayered light against a non-debayered
            # flat. See `_frames_use_color_filter_array` for why guessing a
            # Bayer pattern (Siril's own fallback) is the bug being fixed.
            uses_color_filter_array = _frames_use_color_filter_array(os.path.join(target_folder, "lights"))
            color_filter_array_flags = " -cfa -equalize_cfa" if uses_color_filter_array else ""
            # Surfaced so a quality summary can state whether this stack
            # demosaiced. A monochrome sensor being debayered was a real
            # defect found only by reading Siril's own logs.
            self.last_run_diagnostics["debayer_applied"] = uses_color_filter_array
            job_logger.info(
                f"Sensor type detected as {'color (CFA)' if uses_color_filter_array else 'monochrome'}; "
                f"{'applying' if uses_color_filter_array else 'skipping'} CFA/debayer calibration flags."
            )

            # rejection_sigma passed by the caller is an explicit
            # override and always wins. Otherwise, "adaptive" mode
            # (the default) derives sigma from num_lights via
            # Chauvenet's criterion rather than using one fixed
            # constant for every stack -- see
            # tasks/target_tasks/rejection_thresholds.py for why. "fixed"
            # mode uses the configured constant unconditionally.
            if rejection_sigma is not None:
                rejection_sigma_low, rejection_sigma_high = rejection_sigma
                rejection_sigma_mode_used = "override"
            elif self.config.get_stack_rejection_sigma_mode() == "fixed":
                rejection_sigma_low, rejection_sigma_high = self.config.get_stack_rejection_sigma()
                rejection_sigma_mode_used = "fixed"
            else:
                from astrometricslib.tasks.target_tasks.rejection_thresholds import chauvenet_sigma

                adaptive_sigma = chauvenet_sigma(max(num_lights, 1))
                rejection_sigma_low = rejection_sigma_high = adaptive_sigma
                rejection_sigma_mode_used = "adaptive"

            # Applies the minimum-surviving-frames floor to a
            # percentage-based filter_wfwhm before it's ever sent to
            # Siril: -filter-wfwhm=X% keeps a deterministic X% of
            # num_lights, so the survivor count (and whether it dips
            # below the floor) is knowable in advance without
            # needing Siril to run first. See
            # tasks/target_tasks/stack_quality_tasks.py for the
            # loosening-ladder logic.
            from astrometricslib.tasks.target_tasks.stack_quality_tasks import (
                resolve_filter_wfwhm_with_floor,
            )

            requested_filter_wfwhm = filter_wfwhm
            filter_wfwhm, filter_wfwhm_loosened = resolve_filter_wfwhm_with_floor(num_lights, filter_wfwhm)
            if filter_wfwhm_loosened:
                job_logger.info(
                    f"filter_wfwhm loosened from {requested_filter_wfwhm!r} to {filter_wfwhm!r} "
                    f"to keep at least the minimum-surviving-frames floor with {num_lights} input frames."
                )

            self.last_run_diagnostics.update({
                "num_lights": num_lights,
                "rejection_sigma_low": rejection_sigma_low,
                "rejection_sigma_high": rejection_sigma_high,
                "rejection_sigma_mode": rejection_sigma_mode_used,
                "filter_wfwhm_requested": requested_filter_wfwhm,
                "filter_wfwhm_effective": filter_wfwhm,
                "filter_wfwhm_loosened": filter_wfwhm_loosened,
            })

            siril_debug_log = os.path.join(target_folder, "siril_debug.log")

            import re
            import subprocess

            # Held from launch until read_output has drained Siril's
            # results below, and released by the finally block's
            # siril_lock.close() on every exit path. Entered via an
            # ExitStack rather than a `with` block purely to avoid
            # re-indenting the ~180 lines this span covers; the release
            # guarantee is the same.
            siril_lock.enter_context(siril_process_lock(job_logger=job_logger))

            # Measured from launch rather than from the start of
            # process_target, so waiting on the Siril lock is not counted
            # as time this stack spent working.
            siril_started_at = time.monotonic()

            process = self.run_siril_headless(
                command_pipe,
                output_pipe,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                job_logger=job_logger,
            )

            def read_siril_stdout():  # ruff: ignore[missing-return-type-private-function]
                progress_regex = re.compile(r"progress:.*?([0-9.]+)\s*%")
                try:
                    with open(siril_debug_log, "w") as debug_out:
                        for line in iter(process.stdout.readline, b""):
                            decoded_line = line.decode("utf-8", errors="ignore")
                            debug_out.write(decoded_line)
                            debug_out.flush()

                            # Parse and update progress
                            match = progress_regex.search(decoded_line)
                            if match and self.job_repository is not None and job_id:
                                try:
                                    val = float(match.group(1))
                                    job = self.job_repository.get_job(job_id)
                                    if job:
                                        job.progress_current = int(val)
                                        self.job_repository.upsert_job(job)
                                except Exception as exc:
                                    logger.debug("Failed to parse/persist Siril progress line: %s", exc)
                except Exception as e:
                    if job_logger:
                        job_logger.error(f"Error in Siril stdout reader thread: {e}")

            log_reader_thread = threading.Thread(target=read_siril_stdout, daemon=True)
            log_reader_thread.start()

            script = ["setext fits"]

            # Masters already built for this exact set of calibration
            # frames are copied straight in, and their build steps below
            # are skipped -- the frames still had to be staged above,
            # since their fingerprint is what identifies the master.
            #
            # Only the *build* steps are skipped. num_biases/num_darks/
            # num_flats deliberately keep their real values, because the
            # lights' `calibrate` command reads them to decide whether to
            # pass -bias=/-dark=/-flat=; zeroing them would silently drop
            # the very masters just restored.
            restored_master_kinds = self.restore_cached_calibration_masters(
                target_folder, job_logger=job_logger
            )

            # Automated Master Calibration Generation
            if num_biases > 0 and "bias" not in restored_master_kinds:
                script += [f"cd {os.path.join(target_folder, 'biases')}"]
                if num_biases == 1:
                    # Single bias: convert and use directly as master
                    script += [
                        "convert bias -out=../process",
                        "cd ../process",
                        "load bias_00001.fits",
                        "save bias_stacked",
                    ]
                else:
                    script += [
                        "convert bias -out=../process -fitseq",
                        "cd ../process",
                        "stack bias rej 3 3 -nonorm -out=bias_stacked",
                    ]

            if num_darks > 0 and "dark" not in restored_master_kinds:
                script += [f"cd {os.path.join(target_folder, 'darks')}"]
                if num_darks == 1:
                    # Single dark: convert and use directly as master
                    script += [
                        "convert dark -out=../process",
                        "cd ../process",
                        "load dark_00001.fits",
                        "save dark_stacked",
                    ]
                else:
                    script += [
                        "convert dark -out=../process -fitseq",
                        "cd ../process",
                        "stack dark rej 3 3 -nonorm -out=dark_stacked",
                    ]

            if num_flats > 0 and "flat" not in restored_master_kinds:
                script += [f"cd {os.path.join(target_folder, 'flats')}"]
                if num_flats == 1:
                    script += [
                        "convert flat -out=../process",
                        "cd ../process",
                        "load flat_00001.fits",
                        "save flat_stacked",
                    ]
                else:
                    script += ["convert flat -out=../process -fitseq", "cd ../process"]
                    # Calibrate flat with bias if available
                    script += [
                        f"calibrate flat {'-bias=bias_stacked' if num_biases > 0 else ''}"
                        f"{color_filter_array_flags}",
                        "stack pp_flat rej 3 3 -norm=mul -out=flat_stacked",
                    ]

            # Process Lights
            script += [f"cd {os.path.join(target_folder, 'lights')}"]
            # only set in the multi-frame branch below; single-frame
            # stacks skip registration entirely
            seq = None
            if num_lights == 1:
                # Siril's 'convert' does not create a .seq file for
                # a single input frame, so sequence-based
                # calibrate/register/stack commands can't be used
                # here. Load and save the single converted frame
                # directly; calibration is skipped in this case.
                script += [
                    "convert light_source -out=../process",
                    "cd ../process",
                    "load light_source_00001.fits",
                    "save result_stacked",
                ]
            else:
                script += ["convert light_source -out=../process -fitseq", "cd ../process"]

                has_cal = num_darks > 0 or num_biases > 0 or num_flats > 0
                if has_cal:
                    dark_flag = "-dark=dark_stacked" if num_darks > 0 else ""
                    flat_flag = "-flat=flat_stacked" if num_flats > 0 else ""
                    bias_flag = "-bias=bias_stacked" if num_biases > 0 else ""
                    # -debayer belongs only to the lights: it is what turns
                    # a CFA mosaic into an RGB image, and is meaningless
                    # (and harmful) on monochrome data.
                    debayer_flag = " -debayer" if uses_color_filter_array else ""
                    script.append(
                        f"calibrate light_source {dark_flag} {flat_flag} {bias_flag}"
                        f"{color_filter_array_flags}{debayer_flag}"
                    )
                    seq = "pp_light_source"
                else:
                    seq = "light_source"

                # Multi-frame Stacking
                # Spectroscopy frames need a shift-only transform: a
                # diffraction grating disperses every star's light,
                # not just the target's, so field stars are usually
                # still plentiful (confirmed empirically -- 99-125
                # stars/frame in a real SA200 session is typical,
                # not "too few" for a homography fit). The actual
                # constraint is that any rotation or scale
                # introduced by registration would rotate the
                # dispersion axis and smear the spectral trace
                # between frames, breaking wavelength calibration
                # consistency across the stack.
                # Pin star detection instead of inheriting whatever the
                # Siril GUI last persisted to its own config: detection
                # settings are global user state, so without this the
                # pipeline's registration behaviour differs between
                # machines and silently changes if someone adjusts the
                # GUI. -relax=on is what M 42 needs -- its nebulosity
                # dominates the frame's background statistics, and with
                # the default relax=off Siril's findstar returned 9
                # candidates on a calibrated frame carrying 464
                # detectable stars (photutils DAOStarFinder, 5 sigma),
                # which aborted registration for the whole sequence.
                # With relax=on the same frame yields 472.
                register_commands = ["setfindstar -relax=on"]

                if is_spectral:
                    # Multi-frame Stacking
                    # Spectroscopy frames need a shift-only transform: a
                    # diffraction grating disperses every star's light,
                    # not just the target's, so field stars are usually
                    # still plentiful (confirmed empirically -- 99-125
                    # stars/frame in a real SA200 session is typical,
                    # not "too few" for a homography fit). The actual
                    # constraint is that any rotation or scale
                    # introduced by registration would rotate the
                    # dispersion axis and smear the spectral trace
                    # between frames, breaking wavelength calibration
                    # consistency across the stack.
                    #
                    # Left on single-pass registration deliberately: the
                    # two-pass reference selection below is validated
                    # only against standard frames, and the spectral
                    # path's shift-only constraint is the more delicate
                    # of the two to disturb.
                    register_commands.append(f"register {seq} -transf=shift")
                    registered_seq = f"r_{seq}"
                else:
                    # Two-pass registration scores every frame before
                    # picking a reference, where single-pass just takes
                    # the first. M 42's first frame is also its worst
                    # for this purpose -- pointing sits ~9.6 arcmin off
                    # the other 21 -- and single-pass registration
                    # matched only 6-8 star pairs against it and
                    # registered nothing, even with detection fixed.
                    # Two-pass chose a different reference and stacked
                    # 21 of 22 frames.
                    register_commands.append(f"register {seq} -2pass")
                    # -2pass computes the transforms without applying
                    # them, so the registered sequence only exists once
                    # seqapplyreg has run.
                    register_commands.append(f"seqapplyreg {seq}")
                    registered_seq = f"r_{seq}"

                # Field-star population is typically rich enough in
                # spectral frames too (see above), so
                # FWHM/roundness-based selection and weighting apply
                # to both frame types -- they're a proxy for overall
                # seeing/focus quality, not something specific to
                # standard imaging.
                stack_options = [f"rej {rejection_sigma_low:.4f} {rejection_sigma_high:.4f}"]
                if filter_wfwhm:
                    stack_options.append(f"-filter-wfwhm={filter_wfwhm}")
                if filter_round:
                    stack_options.append(f"-filter-round={filter_round}")
                if stack_weight:
                    stack_options.append(f"-weight={stack_weight}")
                if generate_rejmap:
                    stack_options.append("-rejmap")
                stack_options += ["-norm=addscale", "-out=result_stacked"]

                script += [
                    *register_commands,
                    f"stack {registered_seq} " + " ".join(stack_options),
                ]

            def write_commands():  # ruff: ignore[missing-return-type-private-function]
                self.send_commands(command_pipe, script, job_logger=job_logger)

            writer = threading.Thread(target=write_commands)
            writer.daemon = True
            writer.start()

            res = self.read_output(
                output_pipe,
                target_folder,
                id,
                output_file=output_file,
                library_dest=library_dest,
                job_logger=job_logger,
                generate_rejmap=generate_rejmap,
                registered_seq_name=seq,
            )

            self.last_run_diagnostics["stacking_duration_seconds"] = round(
                time.monotonic() - siril_started_at, 1
            )

            # Cached here rather than in the finally block: the masters
            # live inside target_folder, which the finally block deletes
            # on success, and caching a master from a run that failed
            # partway risks storing a half-built one.
            if res:
                self.store_calibration_masters_in_cache(target_folder, job_logger=job_logger)

            # Per-frame zero-order star tracking for spectral
            # stacks: parsed here (while the scratch directory still
            # exists, before the finally block's cleanup) rather
            # than preserving the raw .lst files out, since
            # stacking_operations.py only needs the parsed values,
            # not the files themselves.
            if is_spectral and seq and res:
                import glob as _glob

                from astrometricslib.data_access.image_quality_metrics import parse_zero_order_star

                lst_paths = sorted(
                    _glob.glob(os.path.join(target_folder, "process", "cache", f"{seq}*.lst")),
                    key=lambda p: int(os.path.splitext(os.path.basename(p))[0].replace(seq, "")),
                )
                self.last_run_diagnostics["zero_order_stars"] = [parse_zero_order_star(p) for p in lst_paths]

            # Update stacked FITS header with total
            # exposure time
            if res and os.path.exists(res):
                try:
                    from astropy.io import fits

                    total_exp = 0.0
                    if isinstance(image_files, list):
                        for f in image_files:
                            exp = f.get("exposure") if isinstance(f, dict) else getattr(f, "exposure", "0")
                            try:
                                total_exp += float(exp)
                            except Exception as exc:
                                logger.debug("Skipping unparsable exposure value '%s': %s", exp, exc)

                    if total_exp > 0:
                        with fits.open(res, mode="update") as hdul:
                            hdul[0].header["EXPTIME"] = total_exp
                            hdul[0].header["EXPOSURE"] = total_exp
                            hdul[0].header.add_comment(
                                f"Total exposure time summed from {len(image_files)} frames."
                            )
                        job_logger.info(f"Updated stacked header: EXPTIME={total_exp}s")
                except Exception as e:
                    job_logger.error(f"Failed to update stacked header EXPTIME: {e}")

            writer.join(timeout=5)
            self._kill_process_tree(process, job_logger=job_logger, workdir=target_folder)

            # Wait for log reader thread to finish reading all output
            log_reader_thread.join(timeout=10)
            return res
        except Exception as fatal:
            job_logger.error(f"FATAL: {fatal}", exc_info=True)
            return None
        finally:
            self.cleanup_subprocesses()
            # Released after cleanup_subprocesses so the next waiting
            # Siril run never starts while this one's process tree is
            # still being torn down.
            siril_lock.close()
            job_logger.removeHandler(handler)
            handler.close()
            if db_handler is not None:
                job_logger.removeHandler(db_handler)
            # Only remove the scratch work directory once the final stack has
            # been copied out to library_dest. If library_dest wasn't
            # resolvable, the returned file lives inside target_folder itself,
            # so it must be left in place. On failure, target_folder is left
            # in place too, since its logs/intermediate files are often needed
            # to diagnose what went wrong.
            if res and library_dest and target_folder and os.path.exists(target_folder):
                try:
                    shutil.rmtree(target_folder)
                    job_logger.info(f"Removed temporary work directory: {target_folder}")
                except Exception as cleanup_error:
                    job_logger.warning(
                        f"Failed to remove temporary work directory {target_folder}: {cleanup_error}"
                    )
            elif target_folder and os.path.exists(target_folder):
                # Keeping the whole directory to preserve diagnostics was
                # the right intent at the wrong granularity: measured on
                # 2026-08-24, a failed M 106 run held 65GB, of which
                # process/ was 65GB and everything actually read when
                # diagnosing -- siril_debug.log plus the staged symlink
                # trees recording exactly which frames were used -- came
                # to 1.1MB. Dropping only the intermediates keeps every
                # artifact that has ever been useful.
                self._discard_stacking_intermediates(target_folder, job_logger)

    def _discard_stacking_intermediates(
        self, target_folder: str, job_logger: logging.Logger | None = None
    ) -> int:
        """Delete a failed run's bulk intermediates, keeping its evidence.

        Removes only ``process/``, which holds the FITSEQ conversions and
        registered sequences. The debug log and the staged symlink trees
        stay, since those are what actually explain a failure and cost
        about a megabyte between them.

        Parameters
        ----------
        target_folder : `str`
            The target's work directory.
        job_logger : `logging.Logger`, optional
            Logger for recording what was reclaimed.

        Returns
        -------
        reclaimed_bytes : `int`
            Approximate bytes freed, ``0`` if there was nothing to
            remove or it could not be removed.
        """
        process_directory = os.path.join(target_folder, "process")
        if not os.path.isdir(process_directory):
            return 0

        reclaimed_bytes = 0
        for directory_path, _, file_names in os.walk(process_directory):
            for file_name in file_names:
                with contextlib.suppress(OSError):
                    reclaimed_bytes += os.path.getsize(os.path.join(directory_path, file_name))

        try:
            shutil.rmtree(process_directory)
        except OSError as cleanup_error:
            logger.debug("Could not discard intermediates in %s: %s", process_directory, cleanup_error)
            return 0

        message = (
            f"Stack did not succeed; discarded {reclaimed_bytes / 1_000_000_000:.1f}GB of "
            f"intermediates from {process_directory} (logs and staged frame lists kept)."
        )
        logger.info(message)
        if job_logger:
            job_logger.info(message)
        return reclaimed_bytes

    def launch_siril_gui(self, file_path: str) -> None:
        """Open the stacked result in the Siril GUI, if available.

        Parameters
        ----------
        file_path : `str`
            Path to the stacked FITS file to open in Siril.
        """
        import shlex

        args = [*shlex.split(self.siril_executable), file_path]
        try:
            # Explicitly redirected, not inherited: an unredirected
            # stdout/stderr would hand this GUI process a copy of the
            # caller's pipe write-end, so any caller reading this
            # process's own output via subprocess.run(capture_output=
            # True) (e.g. the script test harness) would block until
            # this GUI window is closed, since the pipe never reports
            # EOF while any process still holds it open.
            subprocess.Popen(
                args,
                cwd=os.path.dirname(file_path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            logger.debug("Failed to launch Siril GUI for '%s': %s", file_path, exc)
