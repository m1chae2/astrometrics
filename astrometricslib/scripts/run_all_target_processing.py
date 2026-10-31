"""Batch processing script that runs the full pipeline on every target.

Runs stacking, astrometry, photometry, and spectroscopy (where
applicable) in one pass per camera. For a forced restack, ``--stack-only``
runs the stacking step alone, ``--progress-file`` lets an interrupted run
resume, ``--camera`` limits the run to one camera, and ``--review-list``
writes a table (CSV) of what each target's run recorded. A stack-only run also
stacks the earlier nights of every target whose stack holds two or more
nights (the earlier member of the forecast check's pair), when that subset
would hold at least `MINIMUM_EARLIER_NIGHTS_FRAMES` frames;
``--no-earlier-nights`` turns that off. It also runs the odd–even difference
noise measurement for every target (the library refuses it, with a stored
reason, when a half would hold fewer than 8 frames), so each stack has a
measured random part and a fixed-pattern noise (noise that stays at the same
place on the sky in every frame, so more frames do not reduce it);
``--no-odd-even-difference`` turns that off.

The cameras, and their order, come from
the setups listed in the config file: the primary camera first, then the
others. Each target is processed by the first camera that has frames of it --
see `run_full_processing`'s docstring for why later passes skip targets an
earlier pass already covered.

A failure in any single target's pipeline -- including a real
worker-process crash -- is caught and recorded rather than
propagated, so it never halts the rest of the batch; see
`astrometricslib.utilities.parallel_batch.run_parallel_batch` for the
full fault-tolerance behavior (per-target exception handling, plus
automatic worker-pool restart on a process crash).
"""

import argparse
import csv
import json
import logging
import math
import os
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any

from astrometricslib import FITS_READ_ERRORS, AstrometricsError

# Disable opening Siril GUI when stacking completes -- must be set
# before astrometricslib is imported, since a batch run across every
# target should never pop open a GUI window mid-run.
os.environ["HEADLESS"] = "1"

from astrometricslib import EARLIER_NIGHTS_VARIANT, Astrometrics, configure_logging, frame_subject_id
from astrometricslib.drivers.camera_profile_store import camera_identity, resolve_camera_profile
from astrometricslib.foundation.paths import siril_work_directory as siril_work_directory_path
from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.camera_passes import assign_targets_to_cameras, camera_pass_order
from astrometricslib.pipelines.shared.frame_grouping import (
    select_frames_for_camera,
    select_frames_for_processing,
    split_standard_and_spectral_frames,
)
from astrometricslib.pipelines.stacking.metrics import (
    FRAME_QUARANTINE_ROUNDNESS,
    FRAME_QUARANTINE_SKY_MEDIAN,
    STACKING_APERTURE_SET_NUMBER,
    STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_FRAME_WEIGHT,
    STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO,
    STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT,
)
from astrometricslib.pipelines.stacking.odd_even_difference_run import (
    ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF,
)
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import parse_set_aside_action
from astrometricslib.utilities.parallel_batch import BatchRunSummary

# What a Nikon D5300 target needs in its camera profile before a restack:
# a measured noise entry for exactly this setting. Every D5300 frame in the
# library was taken at ISO 800, offset 0, binning 1.
NIKON_REQUIRED_ISO = "800"
NIKON_REQUIRED_OFFSET = 0
NIKON_REQUIRED_BINNING = 1

# The fewest frames the earlier-nights subset may hold for the restack to make
# it. User decision of 2026-10-26: a smaller first stack is too noisy a check
# of the forecast (wayfindinglib refuses such a check too). Not tuned on data;
# register status Design estimate. The general subset minimum
# (`MINIMUM_SUBSET_FRAMES`, 4) is unchanged.
MINIMUM_EARLIER_NIGHTS_FRAMES = 8

# Progress-file statuses. A target in `PROGRESS_DONE_STATUSES` is not run
# again on resume. "failed" is run again only with --retry-failed, and
# "refused" is checked again on every run, since the profile may have changed.
PROGRESS_DONE_STATUSES = ("success", "skipped")
PROGRESS_ERROR_TEXT_LIMIT = 2000

REVIEW_COLUMNS = (
    "target",
    "camera",
    "optic_mm",
    "status",
    "refusal_reason",
    "weights_known",
    "predicted_noise_present",
    "aperture_set_present",
    "earlier_nights_present",
    "odd_even_difference",
    "frames_moved_count",
    "frames_moved",
)


def _print_pass_summary(camera_name: str, summary: BatchRunSummary) -> None:
    """Print one camera pass's outcome in the same format as the others.

    Parameters
    ----------
    camera_name : `str`
        The camera this pass processed, for the header line.
    summary : `BatchRunSummary`
        The pass's aggregated success/skip/failure state.
    """
    stacked_targets = [
        (target_id, result["stack_outputs"])
        for target_id, result in summary.results.items()
        if result.get("stack_outputs")
    ]

    print("\n==========================================")
    print(f"{camera_name} PASS COMPLETE")
    print(f"Processed targets: {len(summary.succeeded)}")
    print(f"Skipped targets (no frames for this camera): {len(summary.skipped)}")
    print(f"Failed targets: {len(summary.failed)}")
    if summary.failed:
        print("\nFailed Targets Summary:")
        for target_id, reason in summary.failed:
            print(f"  - {target_id}: {reason}")
    if summary.skipped:
        print("\nSkipped Targets Summary:")
        for target_id, reason in summary.skipped:
            print(f"  - {target_id}: {reason}")
    print(f"Stacked targets: {len(stacked_targets)}")
    if stacked_targets:
        print("\nStacked Targets Summary:")
        for target_id, outputs in stacked_targets:
            stack_descriptions = [f"{stack_type.capitalize()}={path}" for stack_type, path in outputs.items()]
            print(f"  - {target_id}: {', '.join(stack_descriptions)}")
    print("==========================================")


def _resolve_focal_lengths(astrometrics: Astrometrics, targets: list, arguments: Any) -> list:
    """Decide which optic(s) this run processes.

    An explicit --optic wins. --all-optics runs every focal length
    present in the selection, primary first, so a target imaged through
    two optics ends up with a stack for each. Otherwise the observatory's
    primary optic is used, which is what makes a plain run deterministic
    rather than dependent on whichever frames happen to be present.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Interface providing the configuration.
    targets : `list`
        The targets this run will process.
    arguments : `Any`
        Parsed command-line arguments.

    Returns
    -------
    focal_lengths : `list`
        Focal lengths in millimetres, in the order passes should run. A
        single `None` means "every focal length", used only when no
        primary is configured and none could be discovered.
    """
    if arguments.focal_length_mm:
        return [arguments.focal_length_mm]

    primary = astrometrics.config.get_primary_focal_length_mm()
    if not arguments.all_optics:
        if primary:
            print(f"Using the primary optic: {primary:g}mm (from [Observatory.Telescope]).")
            return [primary]
        print(
            "WARNING: no primary optic configured ([Observatory.Telescope] focal_length_mm); "
            "frames of differing focal length may be stacked together."
        )
        return [None]

    present = sorted({
        round(float(frame.focal_length_mm))
        for target in targets
        for frame in (target.frames or [])
        if getattr(frame, "focal_length_mm", None)
    })
    if not present:
        print("No frame records a focal length yet; run a reindex, or backfill_focal_length.")
        return [None]
    # Primary first so its stack is written before any other optic's,
    # making it the one a reader sees if a later pass is interrupted.
    ordered = [float(value) for value in present]
    if primary and round(primary) in present:
        ordered.remove(float(round(primary)))
        ordered.insert(0, float(round(primary)))
    print(f"Running one pass per optic: {', '.join(f'{value:g}mm' for value in ordered)}")
    return ordered


def _reindex_targets(astrometrics: Astrometrics, targets: list) -> None:
    """Reindex the given targets and refresh their header metadata.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The high-level interface owning the catalog.
    targets : `list`
        Targets to reindex.
    """
    print("Reindexing frames and refreshing acquisition metadata...")
    reindexed_target_count = 0
    for target in targets:
        try:
            astrometrics.targets.reindex_frames(target, refresh_headers=True)
            reindexed_target_count += 1
        except (AstrometricsError, sqlite3.Error, *FITS_READ_ERRORS) as reindex_error:
            print(f"  [{target.id}] Reindex failed, continuing with stored frames: {reindex_error}")
    astrometrics.targets.save()
    print(f"Reindexed {reindexed_target_count} of {len(targets)} target(s).")


def _utc_now_text() -> str:
    """Give the current time as ISO 8601 text in UTC.

    Returns
    -------
    now_text : `str`
        For example ``2026-10-26T21:04:05+00:00``.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def _progress_key(target_id: str, camera_name: str, focal_length_mm: float | None) -> str:
    """Name one unit of work in the progress file.

    Parameters
    ----------
    target_id : `str`
        The target.
    camera_name : `str`
        The camera pass the target ran in.
    focal_length_mm : `float` or `None`
        The optic of the pass, or `None` for "every optic".

    Returns
    -------
    key : `str`
        Text that is the same for the same target, camera and optic.
    """
    optic_text = "any" if focal_length_mm is None else f"{focal_length_mm:g}"
    return f"{target_id.strip().casefold()}|{camera_name.strip().casefold()}|{optic_text}"


def read_progress_records(path: str) -> list[dict]:
    """Read every readable line of a progress file.

    The file is JSON Lines: one record per line. A run that was killed while
    writing can leave a broken last line. That line is skipped with a
    warning, and the earlier lines stay usable.

    Parameters
    ----------
    path : `str`
        The progress file. A missing file gives an empty list.

    Returns
    -------
    records : `list` [`dict`]
        The records, oldest first.
    """
    if not os.path.exists(path):
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"WARNING: skipping an unreadable line {line_number} in {path} "
                    "(a run may have been killed)."
                )
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                print(f"WARNING: skipping line {line_number} in {path}: it is not a JSON object.")
    return records


def append_progress_record(path: str, record: dict) -> None:
    """Append one record to the progress file and force it to disk.

    The line is flushed and synced (``fsync``) before this returns, so a
    kill after the call cannot lose it, and a kill during the call can
    damage only this line. If an earlier kill left the file without a final
    newline, one is added first so the new line does not join the broken one.

    Parameters
    ----------
    path : `str`
        The progress file. It is created if it does not exist.
    record : `dict`
        The record to write, as one JSON line.
    """
    needs_newline = False
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as existing:
            existing.seek(-1, os.SEEK_END)
            needs_newline = existing.read(1) != b"\n"
    with open(path, "a", encoding="utf-8") as handle:
        if needs_newline:
            handle.write("\n")
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def latest_progress_by_key(records: list[dict]) -> dict[str, dict]:
    """Keep the newest record for each target, camera and optic.

    Parameters
    ----------
    records : `list` [`dict`]
        Records from `read_progress_records`, oldest first.

    Returns
    -------
    latest : `dict` [`str`, `dict`]
        The newest record for each key from `_progress_key`.
    """
    latest: dict[str, dict] = {}
    for record in records:
        try:
            key = _progress_key(record["target"], record["camera"], record.get("optic_mm"))
        except KeyError, AttributeError:
            continue
        latest[key] = record
    return latest


class ProgressLog:
    """Write one record per finished target, and answer "is it done already?".

    With no path, every method does nothing, so callers need no checks.

    Parameters
    ----------
    path : `str` or `None`
        The progress file, or `None` for no progress file.
    retry_failed : `bool`
        Whether a target that failed earlier is run again.
    run_started_at : `str`
        When this run began (ISO 8601, UTC). It is stored with each record so
        the review list reads only quality rows made by this run.
    write_enabled : `bool`
        `False` for a dry run, which must write nothing.
    """

    def __init__(
        self, path: str | None, retry_failed: bool, run_started_at: str, write_enabled: bool = True
    ) -> None:
        """Load the records already in the file.

        Parameters
        ----------
        path : `str` or `None`
            The progress file, or `None` for no progress file.
        retry_failed : `bool`
            Whether a target that failed earlier is run again.
        run_started_at : `str`
            When this run began (ISO 8601, UTC).
        write_enabled : `bool`
            `False` for a dry run, which must write nothing.
        """
        self.path = path
        self.retry_failed = retry_failed
        self.run_started_at = run_started_at
        self.write_enabled = write_enabled
        # Run-wide facts stored with every record (see `build_review_row`).
        self.extra_fields: dict[str, Any] = {}
        self.latest = latest_progress_by_key(read_progress_records(path)) if path else {}

    def resume_decision(self, target_id: str, camera_name: str, focal_length_mm: float | None) -> str | None:
        """Say why a target should not run again, if it should not.

        Parameters
        ----------
        target_id : `str`
            The target.
        camera_name : `str`
            The camera pass.
        focal_length_mm : `float` or `None`
            The optic of the pass.

        Returns
        -------
        reason : `str` or `None`
            Text saying why the target is skipped, or `None` to run it.
        """
        record = self.latest.get(_progress_key(target_id, camera_name, focal_length_mm))
        if record is None:
            return None
        status = record.get("status")
        if status in PROGRESS_DONE_STATUSES:
            return f"already {status} in the progress file"
        if status == "failed" and not self.retry_failed:
            return "failed earlier (add --retry-failed to run it again)"
        return None

    def record(
        self,
        target_id: str,
        camera_name: str,
        focal_length_mm: float | None,
        status: str,
        error: str | None = None,
    ) -> None:
        """Append one finished target to the file.

        Parameters
        ----------
        target_id : `str`
            The target.
        camera_name : `str`
            The camera pass.
        focal_length_mm : `float` or `None`
            The optic of the pass.
        status : `str`
            ``success``, ``skipped``, ``failed`` or ``refused``.
        error : `str`, optional
            The error text, or the reason for a refusal.
        """
        if not self.path or not self.write_enabled:
            return
        record = {
            "target": target_id,
            "camera": camera_name,
            "optic_mm": focal_length_mm,
            "status": status,
            "error": (error or "")[:PROGRESS_ERROR_TEXT_LIMIT],
            "time": _utc_now_text(),
            "run_started_at": self.run_started_at,
            **self.extra_fields,
        }
        append_progress_record(self.path, record)
        self.latest[_progress_key(target_id, camera_name, focal_length_mm)] = record


def camera_refusal_reason(camera_name: str, config: Any) -> str | None:
    """Say why a camera's targets must not be restacked, if they must not.

    Only the Nikon D5300 is checked. Its predicted noise needs a measured
    sensor-noise entry for the one setting all its frames use (ISO 800,
    offset 0, binning 1), and its saturation level must be reachable (at or
    below the clip ceiling). Without them the restack would store a record
    that looks complete but is not.

    Parameters
    ----------
    camera_name : `str`
        The camera pass, spelled as frame records spell it.
    config : `AppConfiguration`
        The config that holds the camera profiles.

    Returns
    -------
    reason : `str` or `None`
        The reason for refusing, or `None` when the camera may run.
    """
    profile = resolve_camera_profile(camera_name, config)
    if "nikon" not in f"{profile.camera_name} {camera_name}".lower():
        return None
    problems = []
    if profile.sensor_noise_for(NIKON_REQUIRED_ISO, NIKON_REQUIRED_OFFSET, NIKON_REQUIRED_BINNING) is None:
        problems.append(
            f"the camera profile has no sensor-noise entry for ISO {NIKON_REQUIRED_ISO}, "
            f"offset {NIKON_REQUIRED_OFFSET}, binning {NIKON_REQUIRED_BINNING}"
        )
    if not profile.saturation_threshold_can_be_reached:
        problems.append(
            "the profile's saturation threshold is above the clip ceiling "
            f"({profile.clip_ceiling_adu.value:g} ADU)"
        )
    return "; ".join(problems) or None


def select_camera_names(camera_names: list[str], requested: str | None, config: Any) -> list[str]:
    """Keep the cameras that match a ``--camera`` value.

    A camera matches when it is the same camera under any spelling (see
    `camera_identity`), or when the text is part of its name, ignoring case.

    Parameters
    ----------
    camera_names : `list` [`str`]
        Every camera pass, from `camera_pass_order`.
    requested : `str` or `None`
        The ``--camera`` text. `None` keeps every camera.
    config : `AppConfiguration`
        The config that holds the camera profiles.

    Returns
    -------
    selected : `list` [`str`]
        The matching cameras, in their original order.
    """
    if not requested:
        return list(camera_names)
    wanted_identity = camera_identity(requested, config)
    wanted_text = requested.strip().casefold()
    return [
        name
        for name in camera_names
        if camera_identity(name, config) == wanted_identity or wanted_text in name.casefold()
    ]


def _earlier_nights_frames(astrometrics: Astrometrics, target: Any, since: datetime) -> list:
    """Pick the frames of all observing nights of a new stack but the last.

    The stack's members and their nights are the frames of the
    ``stacking.frame_weight`` rows the stack just stored (each row names its
    frame by subject id and carries the frame's observing night). That is the
    set of frames Siril really stacked, so a frame the quarantine set aside is
    not counted. Each subject id is matched to the target's frame with
    `frame_subject_id`.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface.
    target : `Target`
        The target that was just stacked.
    since : `datetime`
        When the stack began; only rows made at or after it count.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames of every night but the latest, in the target's order. Empty
        when the stack holds fewer than two nights (including a stack that
        stored no weights).
    """
    weight_rows = astrometrics.quality.query(
        metric=STACKING_FRAME_WEIGHT, target_id=target.id, since=since, library_only=True
    )
    night_of_subject = {
        row.subject.subject_id: row.subject.observing_night_id
        for row in weight_rows
        if row.subject.observing_night_id
    }
    if len(set(night_of_subject.values())) < 2:
        return []
    # A night's name is its start date, so the largest name is the last night.
    last_night = max(night_of_subject.values())
    return [
        frame
        for frame in target.frames
        if night_of_subject.get(frame_subject_id(frame.path, target.id), last_night) != last_night
    ]


def _stack_earlier_nights_note(astrometrics: Astrometrics, target: Any, since: datetime) -> str:
    """Stack the earlier nights of a target whose new stack has several nights.

    The subset is stacked with `ProcessingPipelines.stack_subset`, labeled
    `EARLIER_NIGHTS_VARIANT`, with the forecast rows recorded. A failure is a
    by-product: it is reported in the returned text and never raised, so the
    library stack still counts as done.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface.
    target : `Target`
        The target that was just stacked.
    since : `datetime`
        When the stack began; only rows made at or after it count.

    Returns
    -------
    note : `str`
        Empty when the earlier-nights stack was made or was not needed;
        otherwise one sentence saying why it failed.
    """
    try:
        frames = _earlier_nights_frames(astrometrics, target, since)
        if not frames:
            return ""
        if len(frames) < MINIMUM_EARLIER_NIGHTS_FRAMES:
            print(
                f"  [{target.id}] earlier-nights stack skipped ({len(frames)} frames, "
                f"fewer than {MINIMUM_EARLIER_NIGHTS_FRAMES})"
            )
            return ""
        astrometrics.processing.stack_subset(
            target, frames, label=EARLIER_NIGHTS_VARIANT, record_forecast_rows=True
        )
    except Exception as earlier_nights_error:
        # The library stack is already made and recorded; the subset is an
        # extra. Whatever its cause, its failure is noted and the run goes on.
        logging.getLogger(__name__).exception("The earlier-nights stack of '%s' failed", target.id)
        return f"the earlier-nights stack failed: {earlier_nights_error!r}"
    return ""


def _measured_value(rows: list[Any]) -> float | None:
    """Give the value of the first measured row.

    Parameters
    ----------
    rows : `list`
        Quality rows of one metric of the library stack (one row, as the
        default query keeps only the newest for each subject).

    Returns
    -------
    value : `float` or `None`
        The first measured, finite value, or `None` when there is none.
    """
    for row in rows:
        if row.status == MeasurementStatus.MEASURED and row.value is not None and math.isfinite(row.value):
            return float(row.value)
    return None


def _noise_ratio(astrometrics: Any, target_id: str, since: Any) -> float | None:
    """Give the library stack's measured noise over its predicted noise.

    The stored ``stacking.measured_to_predicted_noise_ratio`` row is read
    first. A stack stored before that metric existed has no such row, so the
    ratio is then found from the two noise rows.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface, for ``quality.query``.
    target_id : `str`
        The target.
    since : `datetime` or `str` or `None`
        When the run began; only rows made at or after it count.

    Returns
    -------
    ratio : `float` or `None`
        The measured empty-aperture noise over the predicted one, or `None`
        when either is missing, not measured, or the predicted noise is not
        above zero.
    """
    stored = _measured_value(
        astrometrics.quality.query(
            metric=STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO,
            target_id=target_id,
            since=since,
            library_only=True,
        )
    )
    if stored is not None:
        return stored
    measured, predicted = (
        _measured_value(
            astrometrics.quality.query(metric=metric, target_id=target_id, since=since, library_only=True)
        )
        for metric in (
            STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
            STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
        )
    )
    if measured is None or predicted is None or predicted <= 0:
        return None
    return measured / predicted


def _odd_even_difference_note(astrometrics: Astrometrics, target: Any, since: datetime) -> str:
    """Run the odd–even difference for the stack that was just made.

    It runs for every stack (decision D19 of 2026-10-26): the library refuses,
    with a stored reason, when a half would hold fewer than 8 frames. One line
    is printed with the stack's measured-to-predicted noise ratio. A failure is
    a by-product: it is reported in the returned text and never raised, so the
    library stack still counts as done.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface.
    target : `Target`
        The target that was just stacked.
    since : `datetime`
        When the stack began; only rows made at or after it count.

    Returns
    -------
    note : `str`
        Empty when the measurement ran or was refused with a stored reason;
        otherwise one sentence saying why it failed.
    """
    try:
        ratio = _noise_ratio(astrometrics, target.id, since)
        ratio_text = "no ratio" if ratio is None else f"ratio {ratio:.2f}"
        print(f"  [{target.id}] odd–even difference run ({ratio_text})")
        astrometrics.processing.measure_odd_even_difference_noise(target)
    except Exception as odd_even_difference_error:
        # The library stack is already made and recorded; the measurement is
        # an extra. Whatever its cause, it is noted and the run goes on.
        logging.getLogger(__name__).exception("The odd–even difference measurement of '%s' failed", target.id)
        return f"the odd–even difference measurement failed: {odd_even_difference_error!r}"
    return ""


def _stack_one_target(
    astrometrics: Astrometrics,
    target: Any,
    camera_name: str,
    focal_length_mm: float | None,
    force: bool,
    earlier_nights: bool = True,
    odd_even_difference: bool = True,
) -> tuple[str, str]:
    """Measure and stack one target's imaging frames and do nothing else.

    The frames are chosen as the full pipeline chooses them: one camera, one
    optic, imaging (not spectral) frames. Every one of them is measured first
    (`ProcessingPipelines.record_frame_measurements`), so the stack's
    predicted noise has the sky of each frame. They then go to the public
    `ProcessingPipelines.stack`, which sets aside bad frames, stacks, and
    records the stack's quality rows. No astrometry, photometry or
    spectroscopy runs, and no spectral stack is made.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface.
    target : `Target`
        The target to stack.
    camera_name : `str`
        The camera whose frames to stack.
    focal_length_mm : `float` or `None`
        The optic whose frames to stack, or `None` for every optic.
    force : `bool`
        Rebuild even if nothing changed since the stack on disk.
    earlier_nights : `bool`, optional
        After the stack, also stack the target's earlier nights when the
        stack holds two or more nights (see `_stack_earlier_nights_note`).
        Defaults to `True`.
    odd_even_difference : `bool`, optional
        After the stack (and the earlier-nights stack), run the
        odd–even difference measurement (see `_odd_even_difference_note`).
        Defaults to `True`.

    Returns
    -------
    status, message : `tuple` [`str`, `str`]
        ``"success"`` or ``"skipped"``, and the reason when skipped. A
        ``"success"`` carries a message when the earlier-nights stack or the
        odd–even difference measurement failed.

    Raises
    ------
    RuntimeError
        If another run is measuring the target's frames.
    """
    camera_frames = select_frames_for_processing(target, camera_name, focal_length_mm)
    if camera_frames is None:
        return "skipped", f"No frames for camera '{camera_name}' at this optic"
    standard_frames, _spectral_frames = split_standard_and_spectral_frames(target, camera_frames)
    if not standard_frames:
        return "skipped", "No imaging frames to stack"
    # The stack measures at most `STACKING_STAGE_MEASUREMENT_LIMIT` frames
    # that were never measured, and its predicted noise needs the sky of every
    # frame. So every imaging frame is measured first, with no limit. Frames
    # measured before and unchanged since are skipped, so a resumed run does
    # not repeat the work.
    measurement = astrometrics.processing.record_frame_measurements(target, frames=standard_frames)
    if measurement.already_running:
        raise RuntimeError(f"another run is measuring the frames of '{target.id}'; try again later")
    print(
        f"  [{target.id}] frames measured: {measurement.frames_measured}, unchanged: "
        f"{measurement.frames_skipped_unchanged}, cut short: {measurement.frames_cut_short}, "
        f"failed: {measurement.frames_failed}"
    )
    stack_started_at = datetime.now(UTC)
    astrometrics.processing.stack(target, frames=standard_frames, force=force)
    notes = []
    if earlier_nights:
        notes.append(_stack_earlier_nights_note(astrometrics, target, stack_started_at))
    if odd_even_difference:
        notes.append(_odd_even_difference_note(astrometrics, target, stack_started_at))
    return "success", "; ".join(note for note in notes if note)


def _presence_label(rows: list[Any]) -> str:
    """Say whether quality rows hold a usable value.

    Parameters
    ----------
    rows : `list`
        Quality rows of one metric for one target.

    Returns
    -------
    label : `str`
        ``"yes"`` when a row has a measured value, ``"assumed"`` when rows
        have only assumed values, and ``"no"`` otherwise.
    """
    usable = [row for row in rows if row.value is not None and row.status != MeasurementStatus.NOT_MEASURED]
    if any(row.status == MeasurementStatus.MEASURED for row in usable):
        return "yes"
    return "assumed" if usable else "no"


# The start of the reason the library stores when a half would hold too few
# frames (see `odd_even_difference_run._run_odd_even_difference_noise`).
ODD_EVEN_DIFFERENCE_TOO_FEW_FRAMES_TEXT = "a half would have"


def _odd_even_difference_label(odd_even_difference_rows: list[Any], enabled: bool, stack_made: bool) -> str:
    """Say what happened to a target's odd–even difference measurement.

    Parameters
    ----------
    odd_even_difference_rows : `list`
        The ``stacking.odd_even_difference_noise_stack_unit`` rows made
        since the run began.
    enabled : `bool`
        Whether the run was allowed to make the measurement (no
        ``--no-odd-even-difference``).
    stack_made : `bool`
        Whether the target's stack was made in this run.

    Returns
    -------
    label : `str`
        ``"run"`` when a measured row exists; ``"not run (fewer than 8 frames
        per half)"`` when the library refused for too few frames;
        ``"failed: <reason>"`` for any other stored reason, or when the stack
        was made and no row was stored; ``"skipped
        (--no-odd-even-difference)"`` when the run was told not to make it;
        ``"n/a"`` when the stack was not made.
    """
    if _measured_value(odd_even_difference_rows) is not None:
        return "run"
    if not enabled:
        return "skipped (--no-odd-even-difference)"
    if not stack_made:
        return "n/a"
    if not odd_even_difference_rows:
        return "failed: no result row was stored"
    reason = " ".join((odd_even_difference_rows[0].reason or "no reason was stored").split())
    if reason.startswith(ODD_EVEN_DIFFERENCE_TOO_FEW_FRAMES_TEXT):
        return f"not run (fewer than {ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF} frames per half)"
    return f"failed: {reason}"


def build_review_row(astrometrics: Any, record: dict) -> dict[str, Any]:
    """Describe what one target's restack recorded.

    The rows are read through the public quality query, only those made
    since the run began, so an older stack's rows cannot look like a new one's.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface, for ``quality.query``.
    record : `dict`
        The target's newest progress record.

    Returns
    -------
    row : `dict`
        One value for each name in `REVIEW_COLUMNS`.
    """
    status = record.get("status", "")
    row: dict[str, Any] = {
        "target": record.get("target", ""),
        "camera": record.get("camera", ""),
        "optic_mm": "" if record.get("optic_mm") is None else f"{record['optic_mm']:g}",
        "status": status,
        "refusal_reason": record.get("error", "") if status == "refused" else "",
        "weights_known": "n/a",
        "predicted_noise_present": "n/a",
        "aperture_set_present": "n/a",
        "earlier_nights_present": "n/a",
        "odd_even_difference": "n/a",
        "frames_moved_count": "n/a",
        "frames_moved": "",
    }
    if status == "refused":
        return row

    def query(metric: str) -> list[Any]:
        """Read the run's newest library rows of one metric.

        Parameters
        ----------
        metric : `str`
            The registered metric name.

        Returns
        -------
        rows : `list`
            The newest library rows of that metric for this target.
        """
        return astrometrics.quality.query(
            metric=metric, target_id=row["target"], since=record.get("run_started_at"), library_only=True
        )

    weight_rows = query(STACKING_FRAME_WEIGHT)
    row["weights_known"] = _presence_label(weight_rows)
    row["predicted_noise_present"] = _presence_label(query(STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT))
    row["aperture_set_present"] = _presence_label(query(STACKING_APERTURE_SET_NUMBER))
    # The earlier-nights stack is made only for a stack of two or more nights.
    nights = {
        weight.subject.observing_night_id for weight in weight_rows if weight.subject.observing_night_id
    }
    if len(nights) >= 2:
        # A night's name is its start date, so the largest is the last.
        earlier_frame_count = sum(
            1 for weight in weight_rows if weight.subject.observing_night_id not in (None, max(nights))
        )
    else:
        earlier_frame_count = 0
    if len(nights) >= 2 and earlier_frame_count < MINIMUM_EARLIER_NIGHTS_FRAMES:
        row["earlier_nights_present"] = (
            f"skipped ({earlier_frame_count} frames, fewer than {MINIMUM_EARLIER_NIGHTS_FRAMES})"
        )
    elif len(nights) >= 2:
        earlier_rows = [
            astrometrics.quality.query(
                metric=metric,
                target_id=row["target"],
                since=record.get("run_started_at"),
                variant=EARLIER_NIGHTS_VARIANT,
            )
            for metric in (STACKING_FRAME_WEIGHT, STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT)
        ]
        row["earlier_nights_present"] = (
            "yes" if all(_presence_label(rows) != "no" for rows in earlier_rows) else "no"
        )
    row["odd_even_difference"] = _odd_even_difference_label(
        query(STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT),
        record.get("odd_even_difference_enabled", True),
        status == "success",
    )
    moved_frames = sorted({
        os.path.basename(quarantine_row.subject.subject_id)
        for metric in (FRAME_QUARANTINE_ROUNDNESS, FRAME_QUARANTINE_SKY_MEDIAN)
        for quarantine_row in query(metric)
        if parse_set_aside_action(quarantine_row.action_taken) is not None
    })
    row["frames_moved_count"] = len(moved_frames)
    row["frames_moved"] = ";".join(moved_frames)
    return row


def write_review_list(astrometrics: Any, progress_path: str, review_path: str) -> int:
    """Write the review list (CSV) from a progress file.

    One row per target, camera and optic, using the newest progress record
    of each. The columns are in `REVIEW_COLUMNS`.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface, for ``quality.query``.
    progress_path : `str`
        The progress file to read.
    review_path : `str`
        The CSV file to write.

    Returns
    -------
    row_count : `int`
        How many rows were written.
    """
    latest = latest_progress_by_key(read_progress_records(progress_path))
    rows = [build_review_row(astrometrics, record) for _key, record in sorted(latest.items())]
    with open(review_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _run_stack_only_pass(
    astrometrics: Astrometrics,
    targets: list,
    target_ids: list[str],
    camera_name: str,
    focal_length_mm: float | None,
    force: bool,
    progress: ProgressLog,
    earlier_nights: bool = True,
    odd_even_difference: bool = True,
) -> None:
    """Stack each target of one camera pass, one after the other.

    A failure in one target is printed and recorded, and the next target
    still runs. Each finished target goes into the progress file at once.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library interface.
    targets : `list`
        The catalog targets of this run, to find each id's `Target`.
    target_ids : `list` [`str`]
        The targets to stack, already filtered for resume.
    camera_name : `str`
        The camera of this pass.
    focal_length_mm : `float` or `None`
        The optic of this pass.
    force : `bool`
        Rebuild even if nothing changed since the stack on disk.
    progress : `ProgressLog`
        Where each finished target is recorded.
    earlier_nights : `bool`, optional
        Also stack the earlier nights of each target that holds two or more
        (see `_stack_one_target`). Defaults to `True`.
    odd_even_difference : `bool`, optional
        Run the odd–even difference measurement for each target whose noise
        ratio is in range (see `_stack_one_target`). Defaults to `True`.
    """
    target_by_id = {target.id: target for target in targets}
    failed_ids: list[str] = []
    for position, target_id in enumerate(target_ids, start=1):
        print(f"[{position}/{len(target_ids)}] Stacking {target_id}...")
        try:
            status, message = _stack_one_target(
                astrometrics,
                target_by_id[target_id],
                camera_name,
                focal_length_mm,
                force,
                earlier_nights,
                odd_even_difference,
            )
        except Exception as stacking_error:
            # One target's failure, whatever its cause, must not stop the
            # rest of a night-long run; it is recorded and the run goes on.
            logging.getLogger(__name__).exception("Target '%s' failed to stack", target_id)
            status, message = "failed", f"{stacking_error!r}"
            failed_ids.append(target_id)
        progress.record(target_id, camera_name, focal_length_mm, status, message)
        print(f"  [{target_id}] {status}{': ' + message if message else ''}")
    print(f"Stack-only pass done: {len(target_ids) - len(failed_ids)} of {len(target_ids)} without an error.")
    if failed_ids:
        print(f"Failed: {', '.join(failed_ids)}")


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering target selection and dry-run preview.
    """
    parser = argparse.ArgumentParser(
        prog="run_all_target_processing",
        description="Run the full pipeline over the target catalog, or a chosen subset.",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_ids",
        metavar="TARGET_ID",
        help=(
            "Process only this target; repeatable. Matched case-insensitively against "
            "the catalog id. Defaults to every target."
        ),
    )
    parser.add_argument(
        "--targets-from",
        metavar="PATH",
        help="Read target ids from a file, one per line; blank lines and # comments ignored.",
    )
    parser.add_argument(
        "--skip-target",
        action="append",
        dest="skip_target_ids",
        metavar="TARGET_ID",
        help=(
            "Never process this target; repeatable, matched like --target. Intended for targets "
            "star-based registration cannot handle at all -- a solar or planetary disk carries no "
            "star field, so Siril reports 'not enough stars in reference image' after staging every "
            "frame. Sun, Venus and Jupiter fail this way on every run."
        ),
    )
    parser.add_argument(
        "--skip-targets-from",
        metavar="PATH",
        help="Read target ids to skip from a file, one per line; blank lines and # comments ignored.",
    )
    parser.add_argument(
        "--optic",
        type=float,
        dest="focal_length_mm",
        metavar="MM",
        help=(
            "Process only frames taken at this focal length, in millimetres. Defaults to the "
            "primary optic from [Observatory.Telescope] focal_length_mm, so a target imaged "
            "through two optics stacks the observatory's own rather than blending scales."
        ),
    )
    parser.add_argument(
        "--all-optics",
        action="store_true",
        help=(
            "Run a separate pass per focal length instead of only the primary one. Each optic "
            "gets its own stack; the primary remains the target's stacked_image."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which targets each camera pass would process, then exit without processing.",
    )
    parser.add_argument(
        "--force-restack",
        action="store_true",
        help=(
            "Rebuild every stack, even those whose frames, calibration frames and settings are the "
            "same as when the stack on disk was made. Without this, an unchanged stack is skipped "
            "(setting skip_unchanged_stacks_enabled)."
        ),
    )
    parser.add_argument(
        "--skip-reindex",
        action="store_true",
        help=(
            "Skip the pre-run reindex. Only safe when nothing has changed on disk since "
            "the last run; the reindex costs about 10 seconds for a 4,000-frame catalog."
        ),
    )
    parser.add_argument(
        "--stack-only",
        action="store_true",
        help=(
            "Stack each target's imaging frames and record the stack's quality rows, but run no "
            "astrometry, photometry or spectroscopy and make no spectral stack. Targets run one at "
            "a time in this process. Use with --force-restack for a forced restack."
        ),
    )
    parser.add_argument(
        "--no-earlier-nights",
        action="store_true",
        help=(
            "With --stack-only, do not stack the earlier nights of a target whose stack holds two or "
            "more nights. By default each such target also gets the stack of all nights but the "
            "last (stored as the 'earlier-nights' subset), the earlier member of the forecast check."
        ),
    )
    parser.add_argument(
        "--no-odd-even-difference",
        action="store_true",
        help=(
            "With --stack-only, do not run the odd–even difference noise measurement. By default it runs "
            "for every target (the library refuses it when a half would hold fewer than "
            f"{ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF} frames) and costs about one library stack."
        ),
    )
    parser.add_argument(
        "--progress-file",
        metavar="PATH",
        help=(
            "Append one JSON line per finished target (target, camera, optic_mm, status, error, time). "
            "On start, targets already done in the file are skipped, so an interrupted run resumes. "
            "A failed target is run again only with --retry-failed."
        ),
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="With --progress-file, run again the targets the file records as failed.",
    )
    parser.add_argument(
        "--camera",
        metavar="NAME",
        help=(
            "Process only this camera's pass, such as 'ZWO ASI 533MM Pro'. Any spelling of the name "
            "or part of it is accepted. Targets are still given to cameras as in a full run."
        ),
    )
    parser.add_argument(
        "--review-list",
        metavar="PATH",
        help=(
            "At the end, write a CSV with one row per target in --progress-file: whether frame weights, "
            "predicted noise and the aperture-set row were recorded, whether the earlier-nights stack's "
            "rows are there (yes, no, or n/a for a stack of one night), whether the odd–even difference "
            "measurement ran (run, skipped with its ratio, failed, or n/a), any refusal and its reason, and "
            "the frames the quarantine moved."
        ),
    )
    parser.add_argument(
        "--review-only",
        action="store_true",
        help="Write the --review-list from the existing --progress-file and exit; process nothing.",
    )
    return parser


def _resolve_requested_targets(targets: list, requested_ids: list[str]) -> tuple[list, list[str]]:
    """Narrow the catalog to the requested ids.

    Matching is case-insensitive and ignores surrounding whitespace,
    since ids are typed by hand and carry spaces ("NGC 7023").

    Parameters
    ----------
    targets : `list`
        Every target in the catalog.
    requested_ids : `list` [`str`]
        Ids the caller asked for.

    Returns
    -------
    selected : `list`
        The matching targets, in catalog order.
    unmatched : `list` [`str`]
        Requested ids that matched nothing, so a typo is reported rather
        than silently processing fewer targets than intended.
    """
    wanted = {target_id.strip().casefold() for target_id in requested_ids if target_id.strip()}
    selected = [target for target in targets if target.id.strip().casefold() in wanted]
    matched = {target.id.strip().casefold() for target in selected}
    unmatched = sorted(
        target_id
        for target_id in requested_ids
        if target_id.strip() and target_id.strip().casefold() not in matched
    )
    return selected, unmatched


def _read_target_ids_from_file(path: str) -> list[str]:
    """Read one target id per line, ignoring blanks and comments.

    Parameters
    ----------
    path : `str`
        File to read.

    Returns
    -------
    target_ids : `list` [`str`]
        The ids found, in file order.
    """
    with open(path) as handle:
        return [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]


def run_full_processing(argv: list[str] | None = None) -> None:
    """Run the full processing pipeline for every target in the catalog.

    Runs one pass per camera, in the order given by the config's setups
    (the primary camera first; see `camera_pass_order`). A later pass runs
    only across targets no earlier pass touched
    -- `Target.stacking.stacked_image`/`processed_image` and every
    quality-summary field are single-valued, not per-camera, so running a
    second camera's pass on a target an earlier pass already processed
    would silently overwrite that target's first-camera stack reference
    and quality summaries with the second camera's results (the first
    camera's FITS files themselves stay on disk; the target record would
    simply stop pointing at them). Some targets have frames from several
    cameras; giving each to the first camera that has frames of it keeps
    the earlier pass's results intact.
    """
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.review_only and not (arguments.progress_file and arguments.review_list):
        parser.error("--review-only needs both --progress-file and --review-list.")
    if arguments.retry_failed and not arguments.progress_file:
        parser.error("--retry-failed needs --progress-file.")
    if arguments.review_list and not arguments.progress_file:
        parser.error("--review-list needs --progress-file, which says which targets to list.")
    run_started_at = _utc_now_text()
    if arguments.force_restack and not arguments.review_only:
        # An environment variable, because each target runs in a worker
        # process that starts from this process's environment.
        from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import (
            FORCE_RESTACK_ENVIRONMENT_VARIABLE,
        )

        os.environ[FORCE_RESTACK_ENVIRONMENT_VARIABLE] = "1"
    configure_logging("run_all_target_processing", level=logging.INFO, log_dir="", console_stream=sys.stdout)

    print("Initializing Astrometrics...")
    astrometrics = Astrometrics()

    if arguments.review_only:
        row_count = write_review_list(astrometrics, arguments.progress_file, arguments.review_list)
        print(f"Wrote {row_count} row(s) to {arguments.review_list}.")
        return

    targets = astrometrics.targets.list()
    if not targets:
        print("No targets found in the database catalog.")
        return

    requested_ids = list(arguments.target_ids or [])
    if arguments.targets_from:
        requested_ids.extend(_read_target_ids_from_file(arguments.targets_from))

    skip_ids = list(arguments.skip_target_ids or [])
    if arguments.skip_targets_from:
        skip_ids.extend(_read_target_ids_from_file(arguments.skip_targets_from))
    if skip_ids:
        skipped, unmatched_skips = _resolve_requested_targets(targets, skip_ids)
        skipped_names = {target.id for target in skipped}
        if skipped_names:
            targets = [target for target in targets if target.id not in skipped_names]
            print(
                f"Skipping {len(skipped_names)} target(s) at the caller's request: "
                f"{', '.join(sorted(skipped_names))}"
            )
        if unmatched_skips:
            print(f"WARNING: nothing to skip for {len(unmatched_skips)} id(s): {', '.join(unmatched_skips)}")

    if requested_ids:
        targets, unmatched = _resolve_requested_targets(targets, requested_ids)
        if unmatched:
            # Reported rather than ignored: a mistyped id would otherwise
            # quietly shrink the run and look like a clean pass.
            print(f"WARNING: no target matches {len(unmatched)} requested id(s): {', '.join(unmatched)}")
        if not targets:
            print("None of the requested targets exist in the catalog; nothing to do.")
            return
        print(f"Processing {len(targets)} requested target(s) of the catalog.")
    else:
        print(f"Found {len(targets)} target(s) in the catalog.")

    # Reindex before processing so the run works from a current view of
    # what is actually on disk, and so every frame carries the
    # header-derived acquisition conditions the analyses expect.
    #
    # refresh_headers is the load-bearing part: scanning alone only
    # builds records for files it has not seen before, so a field added
    # to FrameRecord after a frame was first indexed stays None on that
    # frame forever. Reading headers costs ~10ms per frame -- under a
    # minute for this whole catalog -- because nothing here touches
    # pixel data.
    #
    # prune_missing is deliberately NOT set: it deletes frame records
    # whose files are not currently readable, and this library lives on
    # an external drive. A drive that is slow to mount would silently
    # erase real frame history rather than fail loudly.
    # Which cameras to process, and which targets each one gets, come from the
    # setups in the config file. Ids are passed explicitly to every pass:
    # omitting them makes `process_target(None, ...)` walk the entire
    # catalog, which would silently ignore a --target selection and
    # reprocess everything.
    camera_names = camera_pass_order(
        astrometrics.config.get_observatory_setups(),
        astrometrics.config.get_primary_camera_name(),
        astrometrics.config,
    )
    if not camera_names:
        print(
            "The config lists no [Observatory.Setups], so there are no cameras to process. "
            "Add the camera-and-optic pairings you use (see astrometrics.config.example.toml)."
        )
        return
    # Targets are given to cameras using every camera, so a target imaged
    # by both still belongs to the earlier camera when --camera names the
    # later one.
    assignments = assign_targets_to_cameras(targets, camera_names)
    all_camera_names = camera_names
    camera_names = select_camera_names(camera_names, arguments.camera, astrometrics.config)
    if not camera_names:
        print(
            f"No camera matches --camera {arguments.camera!r}. The cameras are: {', '.join(all_camera_names)}"
        )
        return
    progress = ProgressLog(
        arguments.progress_file, arguments.retry_failed, run_started_at, write_enabled=not arguments.dry_run
    )
    progress.extra_fields["odd_even_difference_enabled"] = not arguments.no_odd_even_difference

    if arguments.dry_run:
        # Resolved here too, so a preview shows which optic a real run
        # would use -- the whole point of previewing before a long job.
        focal_lengths_for_progress = _resolve_focal_lengths(astrometrics, targets, arguments)
        for focal_length_mm in focal_lengths_for_progress:
            if focal_length_mm:
                matching = sum(
                    1
                    for t in targets
                    for f in (t.frames or [])
                    if getattr(f, "focal_length_mm", None)
                    and round(float(f.focal_length_mm)) == round(focal_length_mm)
                )
                print(f"  optic {focal_length_mm:g}mm -> {matching} frame(s) in this selection")
        target_by_id = {target.id: target for target in targets}
        for pass_number, camera_name in enumerate(camera_names, start=1):
            camera_target_ids = assignments[camera_name]
            print(
                f"\nPass {pass_number} ({camera_name}) would process {len(camera_target_ids)}: "
                f"{', '.join(camera_target_ids) or '-'}"
            )
            refusal = camera_refusal_reason(camera_name, astrometrics.config)
            if refusal and camera_target_ids:
                print(f"  REFUSED: {refusal}")
                continue
            for target_id in camera_target_ids:
                camera_frames = select_frames_for_camera(target_by_id[target_id], camera_name)
                first_optic = focal_lengths_for_progress[0] if focal_lengths_for_progress else None
                skip_reason = progress.resume_decision(target_id, camera_name, first_optic)
                note = f" -- would skip: {skip_reason}" if skip_reason else ""
                if arguments.stack_only:
                    # A stack-only run stacks the imaging frames alone, so the
                    # preview counts those and not the spectral frames.
                    imaging_frames, _spectral_frames = split_standard_and_spectral_frames(
                        target_by_id[target_id], camera_frames or []
                    )
                    print(f"  {target_id}: {len(imaging_frames)} imaging frame(s) from this camera{note}")
                else:
                    print(f"  {target_id}: {len(camera_frames or [])} frame(s) from this camera{note}")
        assigned_ids = {target_id for ids in assignments.values() for target_id in ids}
        unassigned = [t.id for t in targets if t.id not in assigned_ids]
        if unassigned:
            print(f"No frames for any configured camera ({len(unassigned)}): {', '.join(unassigned)}")
        print("\nDry run: nothing was processed or written.")
        return

    # Reclaim scratch space left by earlier failed or interrupted runs before
    # this one starts staging its own files. A successful stack removes its own
    # work directory, so anything old enough to be swept here belongs to
    # a run that crashed or was cancelled.
    from astrometricslib.drivers.siril_interface import purge_stale_work_directories

    siril_work_directory = siril_work_directory_path()
    purged_count, reclaimed_bytes = purge_stale_work_directories(siril_work_directory)
    if purged_count:
        print(
            f"Reclaimed {reclaimed_bytes / 1_000_000_000:.1f}GB from {purged_count} stale "
            f"work director{'y' if purged_count == 1 else 'ies'}."
        )

    if arguments.skip_reindex:
        print("Skipping reindex at the caller's request.")
    else:
        _reindex_targets(astrometrics, targets)

    if arguments.stack_only:
        print("Stacking only (no astrometry, photometry, spectroscopy or spectral stacks) for each target...")
    else:
        print("Running the full pipeline (stacking, astrometry, photometry, spectroscopy) for each target...")

    # Which optic(s) to run. Defaults to the observatory's primary --
    # frames of different focal length image at different scales and must
    # never share a stack, so processing every optic at once is not an
    # option; the choice is which one a plain run produces.
    focal_lengths = _resolve_focal_lengths(astrometrics, targets, arguments)

    for focal_length_mm in focal_lengths:
        optic_label = f" @ {focal_length_mm:g}mm" if focal_length_mm else ""
        for pass_number, camera_name in enumerate(camera_names, start=1):
            earlier_note = (
                f" (targets without frames from {', '.join(camera_names[: pass_number - 1])} only)"
                if pass_number > 1
                else ""
            )
            print(f"\n--- Pass {pass_number}: {camera_name}{optic_label}{earlier_note} ---")
            camera_target_ids = assignments[camera_name]
            if not camera_target_ids:
                print(f"No targets assigned to {camera_name} in this selection; skipping this pass.")
                continue
            refusal = camera_refusal_reason(camera_name, astrometrics.config)
            if refusal:
                # Recorded per target, so the review list shows each
                # one and why.
                print(f"REFUSED: {camera_name} targets are not run: {refusal}")
                for target_id in camera_target_ids:
                    progress.record(target_id, camera_name, focal_length_mm, "refused", refusal)
                continue
            runnable_ids = []
            for target_id in camera_target_ids:
                skip_reason = progress.resume_decision(target_id, camera_name, focal_length_mm)
                if skip_reason:
                    print(f"  [{target_id}] not run: {skip_reason}")
                else:
                    runnable_ids.append(target_id)
            if not runnable_ids:
                print(f"Nothing left to run for {camera_name}{optic_label}.")
                continue
            if arguments.stack_only:
                _run_stack_only_pass(
                    astrometrics,
                    targets,
                    runnable_ids,
                    camera_name,
                    focal_length_mm,
                    arguments.force_restack,
                    progress,
                    not arguments.no_earlier_nights,
                    not arguments.no_odd_even_difference,
                )
                continue

            def record_finished_target(
                target_id: str,
                result: dict,
                completed_count: int,
                total_count: int,
                camera_name: str = camera_name,
                focal_length_mm: float | None = focal_length_mm,
            ) -> None:
                """Append a finished target's outcome to the progress file."""
                progress.record(
                    target_id, camera_name, focal_length_mm, result["status"], result.get("error")
                )

            # The full camera name is passed rather than a partial match:
            # `process_target` matches it as a case-insensitive substring
            # against each frame's camera, so a partial string risks matching
            # more than one camera.
            summary = astrometrics.processing.process_target(
                runnable_ids,
                camera_id=camera_name,
                focal_length_mm=focal_length_mm,
                on_item_complete=record_finished_target,
            )
            _print_pass_summary(f"{camera_name}{optic_label}", summary)

    if arguments.review_list:
        row_count = write_review_list(astrometrics, arguments.progress_file, arguments.review_list)
        print(f"\nWrote {row_count} row(s) to {arguments.review_list}.")


# This guard is REQUIRED, not stylistic boilerplate: `process_target`, given
# several targets, runs them across a `ProcessPoolExecutor`, and Python 3.14
# on Linux defaults to the "forkserver" start method, which re-imports this
# module inside every worker process. Without the guard, each worker
# re-executes the whole script body, tries to start its own worker pool, and
# the run dies immediately with "An attempt has been made to start a new
# process before the current process has finished its bootstrapping phase."
# The script really is imported -- by multiprocessing itself.
if __name__ == "__main__":
    run_full_processing()
