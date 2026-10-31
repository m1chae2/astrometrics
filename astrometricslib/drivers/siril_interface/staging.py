"""Siril staging: the work folder, the staged frames and the master cache.

Builds the folder Siril works in (symlinks to the frames, grouped as lights,
darks, flats and biases), keeps the cache of finished master calibration
frames, checks the staged flats, and removes work folders. `SirilStaging` is
the layer of the driver class `ImageProcessing` that holds this.
"""

import contextlib
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from astrometricslib.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    CALIBRATION_MATCH_BLOCKING_FLAGS_KEY,
    DARK_FRAME_PATHS_KEY,
    FLAT_FRAME_PATHS_KEY,
    CalibrationSelection,
    format_binning,
    header_binning,
)
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, read_data, read_header
from astrometricslib.drivers.frame_prefetch import prefetch_frames
from astrometricslib.drivers.siril_interface.session import SirilSession

logger = logging.getLogger(__name__)


# Master calibration frames (bias, dark, flat) take a long time to build.
# To save time, they are cached. This turns hours of processing into
# minutes by reusing masters that have already been built for the same
# camera settings.
# The cache checks the actual source files, so if a file is added or removed, a
# calibration frame, it will automatically rebuild a new master.
CALIBRATION_MASTER_CACHE_DIRECTORY_NAME = "MasterCache"


# This decides how long to keep leftover work folders from failed tasks
# before deleting them. Seven days gives us enough time to investigate
# errors without filling up the hard drive with junk data over time.
WORK_DIRECTORY_RETENTION_DAYS = 7


def purge_stale_work_directories(
    workdir: str, retention_days: int = WORK_DIRECTORY_RETENTION_DAYS
) -> tuple[int, int]:
    """Remove work directories left behind by failed or cancelled runs.

    This function skips the cache and any folders that were recently modified,
    so it won't delete data that is currently being used. It also ignores
    errors if a folder can't be deleted, since freeing up space isn't worth
    crashing the program.

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


# Written into the key of the stored master flats, so that a change to how
# the master flat is built or blurred does not reuse one built the old way.
# Increase it whenever that changes what the master flat contains, including
# a change to how the stacking pipeline's flat assessment picks the blur
# width. Version 4 subtracts the bias from a lone flat too. Version 5 is
# saved as 32-bit floating point (the script sets ``set32bits``), not as the
# 16-bit integers of Siril's default preference.
FLAT_MASTER_RECIPE = "flat-recipe-5"


def _master_recipe(kind: str) -> str:
    """Name how a master of this kind is built, for its cache key.

    Parameters
    ----------
    kind : `str`
        The master kind: "bias", "dark" or "flat".

    Returns
    -------
    recipe : `str`
        The recipe name. Empty for kinds built the standard way.
    """
    if kind != "flat":
        return ""
    return FLAT_MASTER_RECIPE


def _calibration_source_fingerprint(frames_directory: str, recipe: str = "") -> str | None:
    """Fingerprint the calibration frames staged in a directory.

    Each staged frame is a symlink into the shared calibration library,
    so the *link targets* are fingerprinted, not the links.

    Parameters
    ----------
    frames_directory : `str`
        Staging directory holding one master's source frames.
    recipe : `str`, optional
        A name for how the master is built from the frames. It is part of
        the fingerprint, so a master built a different way from the same
        frames is not mistaken for this one.

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
    digest.update(recipe.encode("utf-8"))
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


def _frames_use_color_filter_array(frames_directory: str) -> bool:
    """Report whether a staged frame directory holds color (CFA) data.

    This checks if the images use a Bayer color filter array. If we
    tell Siril to process a black-and-white (monochrome) image as a
    color image, it will guess a color pattern and ruin the data. This
    function looks at the image headers to prevent that mistake.

    Parameters
    ----------
    frames_directory : `str`
        Directory of staged frames to inspect.

    Returns
    -------
    uses_color_filter_array : `bool`
        `True` only if a frame declares a ``BAYERPAT``.
    """
    from astrometricslib.drivers.fits_access import frame_uses_color_filter_array

    try:
        frame_names = sorted(os.listdir(frames_directory))
    except OSError as directory_error:
        logger.debug("Could not list %s for CFA detection: %s", frames_directory, directory_error)
        return False

    for frame_name in frame_names:
        frame_path = os.path.join(frames_directory, frame_name)
        if not os.path.isfile(frame_path):
            continue
        # None means the header could not be read at all -- try the next
        # frame rather than letting one unreadable file decide the whole
        # stack's calibration mode.
        result = frame_uses_color_filter_array(frame_path)
        if result is not None:
            return result

    return False


def work_directory_name(target_id: str, output_file: str | None) -> str:
    """Name the Siril work folder (and default log) of one stacking run.

    The folder used to be named after the target alone, so two stacks of the
    same target running at once (its luminance and spectral stacks, or the
    exposure groups of one stack) shared one folder, and each run deleted the
    other's frames when it set up. Adding the name of the output file gives
    each run its own.

    Parameters
    ----------
    target_id : `str`
        The target's name.
    output_file : `str` or `None`
        The file the run will write, or `None` to name the folder after the
        target alone.

    Returns
    -------
    name : `str`
        The target's name with spaces as underscores, followed by ``__`` and
        the output file's name without its extension when there is one.
    """
    name = target_id.replace(" ", "_")
    if not output_file:
        return name
    stem = os.path.splitext(os.path.basename(output_file))[0].replace(" ", "_")
    return f"{name}__{stem}"


def dominant_exposure(frames: list[Any]) -> str:
    """Find the exposure length that most of a batch of frames share.

    The dark master is chosen by exposure length, so a batch is calibrated with
    the dark of its most common length. Before this, the dark of the *first*
    frame's length was applied to every frame, which put a 600 s dark on 300 s
    frames in the NGC 2244 and NGC 2903 sessions (2026-01-17/18) and no dark at
    all on the NGC 1893 session, whose first frame had no dark.

    Parameters
    ----------
    frames : `list`
        Frame records or dictionaries of them, with an ``exposure``.

    Returns
    -------
    exposure : `str`
        The most common exposure, as it was written on the frames (ties go to
        the one that appears first); ``"0"`` when no frame has one.
    """
    counts: dict[str, int] = {}
    for frame in frames:
        value = frame.get("exposure") if isinstance(frame, dict) else getattr(frame, "exposure", None)
        if value is None:
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "0"
    return max(counts, key=counts.__getitem__)


def representative_light_temperature_c(frames: list[Any]) -> float | None:
    """Find a light-frame batch's mean sensor temperature, for dark matching.

    The calibration library files darks by temperature slot and picks the
    slot nearest this value (see `CalibrationLibrary.select_dark_frames`), so
    this is where a batch's actual sensor temperature enters the match.

    A dict frame here is always a plain ``model_dump()`` (see
    `_stack_one_batch`), which uses the Python field name
    (``sensor_temperature_c``), not the camelCase JSON alias
    (``sensorTemperatureC``) -- confirmed against a real incident where
    checking the alias silently found no temperature on every dict
    frame, so Arcturus's dark-temperature mismatch (a dark master at
    ~0C applied to light frames that drifted from -2.9C to -10.7C,
    which Siril reported as 57-64% negative pixels after dark
    subtraction) went undetected even after this check was added.

    Parameters
    ----------
    frames : `list`
        Frame records or dictionaries of them, with a
        ``sensor_temperature_c``.

    Returns
    -------
    mean_temperature_c : `float` or `None`
        The mean of every frame's recorded temperature, or `None` if
        none of them have one.
    """
    temperatures = [
        (
            frame.get("sensor_temperature_c")
            if isinstance(frame, dict)
            else getattr(frame, "sensor_temperature_c", None)
        )
        for frame in frames
    ]
    temperatures = [t for t in temperatures if t is not None]
    return sum(temperatures) / len(temperatures) if temperatures else None


def representative_light_binning(frames: list[Any], light_paths: list[str]) -> str:
    """Find the binning a batch of light frames was taken at, for matching.

    The calibration library files frames by binning, and a frame binned
    differently can never calibrate the lights. The binning is read from the
    header of the first readable light (its ``XBINNING`` and ``YBINNING``
    cards). One light speaks for the batch, because lights binned differently
    have different image sizes and the stacker keeps only the most common
    size. If no header can be read, the binning recorded on the frame
    records is used (the most common value; the width factor stands for
    both axes), and without one, no binning (``"1x1"``).

    Parameters
    ----------
    frames : `list`
        Frame records or dictionaries of them, with a ``binning``.
    light_paths : `list` [`str`]
        The light frame paths that can be read, in the order to try them.

    Returns
    -------
    binning : `str`
        The binning as text, such as ``"2x2"``.
    """
    for path in light_paths:
        try:
            return header_binning(read_header(path))
        except FITS_READ_ERRORS:
            continue
    counts: dict[int, int] = {}
    for frame in frames:
        value = frame.get("binning") if isinstance(frame, dict) else getattr(frame, "binning", None)
        if value is not None:
            counts[int(value)] = counts.get(int(value), 0) + 1
    return format_binning(max(counts, key=counts.__getitem__)) if counts else format_binning()


class SirilStaging(SirilSession):
    """Stage the frames Siril reads and cache the masters it builds."""

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
            if read_data(path) is None:
                return False
            return True
        except FITS_READ_ERRORS:
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
            fingerprint = _calibration_source_fingerprint(
                os.path.join(target_folder, frames_subdirectory), _master_recipe(kind)
            )
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
            (job_logger or logger).info(
                "Reusing cached master %s frame (fingerprint %s).", kind, fingerprint[:12]
            )

        return restored_kinds

    def assess_staged_flats(
        self,
        target_folder: str,
        uses_color_filter_array: bool,
        job_logger: logging.Logger | None = None,
        camera: str | None = None,
        assess_flats: Callable[[list[str], str | None], Any] | None = None,
    ) -> Any:
        """Measure the staged flat frames and decide whether to smooth them.

        The master flat's noise is copied into every calibrated light, and
        stacking does not average it away unless the frames were dithered
        (see `stacking/pre_processing/flat_calibration.py`). When the
        master would be noisier than the limit set there, the assessment
        carries the Gaussian width to blur it with. The Siril script
        applies the blur (its ``gauss`` command) to the master flat it
        builds.

        A colour (Bayer) sensor's flat is measured but never smoothed,
        because blurring the mosaic would mix its colours.

        Parameters
        ----------
        target_folder : `str`
            This run's staging directory.
        uses_color_filter_array : `bool`
            Whether the lights come from a colour sensor.
        job_logger : `logging.Logger`, optional
            Logger for the findings.
        camera : `str`, optional
            The name of the camera that took the flats. Its profile sets
            the full-scale value the flat brightness is judged against.
            Without it, the assessment guesses the scale from the pixels.
        assess_flats : `Callable`, optional
            The stacking pipeline's flat measurement (see
            `PipelineSteps.assess_flats`). Without it, the flats are not
            measured and the master flat is not smoothed.

        Returns
        -------
        assessment : `FlatAssessment` or `None`
            What was measured about the flat set. `smoothing_sigma_pixels` is
            `None` when no smoothing is to be applied. `None` when no
            ``assess_flats`` was given.
        """
        import dataclasses

        if assess_flats is None:
            (job_logger or logger).warning(
                "Flat calibration: no flat assessment step was given; the flats are not checked or smoothed."
            )
            return None

        flat_directory = os.path.join(target_folder, "flats")
        flat_paths = sorted(os.path.join(flat_directory, name) for name in os.listdir(flat_directory))
        assessment = assess_flats(flat_paths, camera)
        for issue in assessment.issues:
            (job_logger or logger).warning("Flat calibration: %s", issue)
        if assessment.needs_smoothing and uses_color_filter_array:
            note = "not smoothed: blurring a colour sensor's flat would mix its colours"
            (job_logger or logger).warning("Flat calibration: %s", note)
            return dataclasses.replace(
                assessment, smoothing_sigma_pixels=None, issues=[*assessment.issues, note]
            )
        return assessment

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
            fingerprint = _calibration_source_fingerprint(
                os.path.join(target_folder, frames_subdirectory), _master_recipe(kind)
            )
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
                job_logger.info("Cached master %s frame (fingerprint %s).", kind, fingerprint[:12])

    def _record_calibration_selection(
        self,
        selection: CalibrationSelection,
        kind: str,
        log: Callable[[str], None],
        warn: Callable[[str], None],
    ) -> list[str]:
        """Copy a calibration selection's flags into the run's diagnostics.

        A soft flag (the library used frames of another gain, offset or
        temperature because nothing closer exists) goes into
        ``calibration_mismatch_flags``, and the frames are still applied. A
        blocking flag (the frames could never calibrate the lights, such as a
        binning mismatch) goes into ``calibration_match_blocking_flags``, and
        the selection has no frames. The stacking pipeline reads both lists
        (see `calibration_gates` in `pipelines/stacking/pre_processing/`
        `assess_input_quality.py`) and fails its ``calibration_metadata`` gate.

        Parameters
        ----------
        selection : `CalibrationSelection`
            What the library chose for this kind of frame.
        kind : `str`
            ``"dark"``, ``"bias"`` or ``"flat"``, for the log lines.
        log : `Callable` [[`str`], `None`]
            Writes an information line to the run's log.
        warn : `Callable` [[`str`], `None`]
            Writes a warning line to the run's log.

        Returns
        -------
        frames : `list` [`str`]
            The chosen file paths.
        """
        for flag in selection.flags:
            log(f"Calibration metadata mismatch (soft flag, {kind} still applied): {flag}")
            self.last_run_diagnostics.setdefault("calibration_mismatch_flags", []).append(flag)
        for flag in selection.blocking_flags:
            warn(f"Calibration cannot be matched (blocking flag, {kind} not applied): {flag}")
            self.last_run_diagnostics.setdefault(CALIBRATION_MATCH_BLOCKING_FLAGS_KEY, []).append(flag)
        return selection.frames

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
            A list of frame records (dict or FrameRecord-like objects
            with ``path``/``camera``/``telescope``/``iso``/
            ``exposure``/``filter`` attributes).
        camera_filter : `str`, optional
            Camera name to restrict light frames to. If `None`
            (default), it is inferred from the first frame; if it
            still can't be determined, no camera filtering is applied
            and every frame is treated as a match.
        job_logger : `logging.Logger`, optional
            Logger to record progress to. If `None` (default), the
            module logger is used.

        Returns
        -------
        target_folder : `str`
            Path to the populated working directory for this target.
        """
        log = job_logger.info if job_logger else logger.info
        warn = job_logger.warning if job_logger else logger.warning

        if not camera_filter and isinstance(image_files, list) and len(image_files) > 0:
            f = image_files[0]
            camera_filter = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", None)

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
                if camera_filter is None
                or (frame.get("camera") if isinstance(frame, dict) else getattr(frame, "camera", "Unknown"))
                == camera_filter
            ]
            readable_light_paths = find_readable_paths(candidate_light_paths)

            # Applied after the readability filter so a corrupt frame
            # cannot skew which geometry looks dominant.
            from astrometricslib.drivers.fits_access import select_dominant_frame_dimensions

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

                if camera_filter is not None and cam != camera_filter:
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
                except OSError as e:
                    log(f"Error symlinking light {path}: {e}")

            # Populate calibrations from the first matching frame
            matching_frames = [
                f
                for f in image_files
                if camera_filter is None
                or (f.get("camera") if isinstance(f, dict) else getattr(f, "camera", "")) == camera_filter
            ]
            if matching_frames:
                f = matching_frames[0]
                tel = f.get("telescope") if isinstance(f, dict) else getattr(f, "telescope", "Unknown")
                cam = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", camera_filter)
                iso = f.get("iso") if isinstance(f, dict) else getattr(f, "iso", "800")
                offset = f.get("offset") if isinstance(f, dict) else getattr(f, "offset", "0")
                # The dark frames are chosen by exposure length. Take the
                # length most of the batch has, not the first frame's: a
                # batch that holds a few frames of another length (small
                # exposure groups are folded into their nearest neighbour)
                # must get the dark of the majority, not of whichever frame
                # happens to be first.
                exp = dominant_exposure(matching_frames)
                filt = f.get("filter") if isinstance(f, dict) else getattr(f, "filter", "None")

                light_temperature_c = representative_light_temperature_c(matching_frames)
                staged_light_paths = sorted(readable_light_paths)
                light_binning = representative_light_binning(matching_frames, staged_light_paths)
                first_light = staged_light_paths[0] if staged_light_paths else ""
                # How the calibration flags name this batch of lights.
                light_label = (
                    f"the {len(staged_light_paths)} light frame(s) starting with "
                    f"{os.path.basename(first_light)}"
                )

                dark_frame_paths = self._record_calibration_selection(
                    library.select_dark_frames(
                        camera=cam,
                        exposure=exp,
                        iso=iso,
                        offset=offset,
                        binning=light_binning,
                        temperature_c=light_temperature_c,
                        light_label=light_label,
                    ),
                    "dark",
                    log,
                    warn,
                )
                readable_dark_paths = find_readable_paths(dark_frame_paths)
                for item in dark_frame_paths:
                    if item not in readable_dark_paths:
                        log(f"Skipping corrupted/unreadable dark frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "darks", f"dark_{dark_idx:05d}.fits"))
                        dark_idx += 1
                        # Kept for the calibration record: the darks applied.
                        self.last_run_diagnostics.setdefault(DARK_FRAME_PATHS_KEY, []).append(item)
                    except OSError as e:
                        log(f"Error symlinking dark {item}: {e}")

                bias_frame_paths = self._record_calibration_selection(
                    library.select_bias_frames(
                        camera=cam, iso=iso, offset=offset, binning=light_binning, light_label=light_label
                    ),
                    "bias",
                    log,
                    warn,
                )
                readable_bias_paths = find_readable_paths(bias_frame_paths)
                for item in bias_frame_paths:
                    if item not in readable_bias_paths:
                        log(f"Skipping corrupted/unreadable bias frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "biases", f"bias_{bias_idx:05d}.fits"))
                        bias_idx += 1
                        # The library file behind the link, for the quality
                        # step (the flat-to-light match reads the bias level
                        # from these).
                        self.last_run_diagnostics.setdefault(BIAS_FRAME_PATHS_KEY, []).append(item)
                    except OSError as e:
                        log(f"Error symlinking bias {item}: {e}")

                flat_frame_paths = self._record_calibration_selection(
                    library.select_flat_frames(
                        telescope=tel,
                        camera=cam,
                        filter_type=filt,
                        iso=iso,
                        offset=offset,
                        binning=light_binning,
                        light_label=light_label,
                    ),
                    "flat",
                    log,
                    warn,
                )
                readable_flat_paths = find_readable_paths(flat_frame_paths)
                for item in flat_frame_paths:
                    if item not in readable_flat_paths:
                        log(f"Skipping corrupted/unreadable flat frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "flats", f"flat_{flat_idx:05d}.fits"))
                        flat_idx += 1
                        # The library file behind the link, so the quality
                        # step can rebuild the flat's dust map from it (the
                        # master flat is not kept).
                        self.last_run_diagnostics.setdefault(FLAT_FRAME_PATHS_KEY, []).append(item)
                    except OSError as e:
                        log(f"Error symlinking flat {item}: {e}")

            return target_folder

        return target_folder

    def _prefetch_staged_frames(self, target_folder: str, job_logger: logging.Logger) -> None:
        """Read the staged frames into the mount cache, if allowed.

        Does nothing unless ``prefetch_frames`` is on. The staged lights,
        darks, biases and flats are symlinks to the real files, so each link
        is resolved first. The summary goes in the job log.

        Parameters
        ----------
        target_folder : `str`
            Work folder that `build_directories` filled with symlinks.
        job_logger : `logging.Logger`
            Logger for the job; receives the summary line.
        """
        # "is True" so a test double config (whose getters return truthy
        # mock objects) never triggers a download.
        if self.config.get_prefetch_frames() is not True:
            return
        real_paths: list[str] = []
        for sub_folder in ("lights", "darks", "biases", "flats"):
            folder = os.path.join(target_folder, sub_folder)
            if os.path.isdir(folder):
                real_paths.extend(os.path.realpath(entry.path) for entry in os.scandir(folder))
        summary = prefetch_frames(real_paths, self.config.get_prefetch_workers())
        job_logger.info(
            "Pre-fetch: read %d files (%.1f MB) in %.1f s; %d failed, %d missing.",
            summary.file_count,
            summary.byte_count / 1e6,
            summary.elapsed_seconds,
            summary.failure_count,
            summary.skipped_missing_count,
        )

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
