"""Running a stacking engine, with the two safeguards spectroscopy needs.

`run_stack` is what the stacking stage calls instead of talking to the
engine directly (the engine is Siril today, see
`drivers/interfaces/stacking_driver.py`).
It adds two things the engine alone does not do:

1. Registration fallback. Spectral frames are lined up by matching stars
   against a reference frame. If the standard star detection cannot match
   enough frames, the stack is tried again with a more relaxed detection,
   and if that still is not enough, with phase correlation in Python instead
   of star matching at all (see `spectral_frame_alignment`, and
   `_stack_phase_correlation_aligned_spectral_frames` in
   `siril.stacking.spectral_stacker`) -- the better of the three attempts
   is kept. Losing frames used to happen
   silently; now it is retried and, if it still happens, logged as a
   warning.
2. Exposure groups. A spectral session shot at several exposure lengths is
   stacked one exposure length at a time and the results are combined (see
   `exposure_groups` for why the usual all-at-once recipe wastes the light).
"""

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    DARK_FRAME_PATHS_KEY,
    FLAT_BIAS_FRAME_PATHS_KEY,
    FLAT_FRAME_PATHS_KEY,
)
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib._internal.drivers.interfaces.stacking_driver import (
    PipelineSteps,
    StackingDriver,
    StackSettings,
)
from astrometricslib._internal.drivers.siril.core.session import SIRIL_COMMUNICATION_ERROR_KEY
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import StarWidthEstimator, record_star_width
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.pre_processing.flat_calibration import assess_flats
from astrometricslib.pipelines.stacking.processing.spectral_frame_alignment import align_calibrated_frames
from astrometricslib.utilities.exceptions import DATA_ERRORS

if TYPE_CHECKING:
    from astrometricslib.pipelines.stacking.processing.exposure_groups import ExposureGroup

logger = logging.getLogger(__name__)

# The pipeline's own steps the engine calls in the middle of a run: judging
# the flat frames (and how much to blur a noisy master flat), and lining up
# spectral frames by phase correlation when star matching fails. They are
# handed to the engine on every run, because a driver may not import
# pipeline code.
PIPELINE_STEPS = PipelineSteps(assess_flats=assess_flats, align_frames=align_calibrated_frames)

# The share of frames that must register for a spectral stack to be accepted
# as it is. Below this, the stack is retried with relaxed star detection and
# the run that registered more frames is kept; if both fall below it, a
# warning is logged. Unvalidated: chosen so that losing one frame in ten is
# tolerated (a stray cloudy frame) while losing a third is not. On the Vega
# session the standard detection registered 140 of 140 frames (100%) and the
# relaxed one 94 of 140 (67%). It has not been tuned on other sessions.
MINIMUM_REGISTERED_FRACTION = TunedParameter(
    name="stacking.minimum_registered_fraction",
    default=0.9,
    unit="fraction",
    description="Share of frames that must register for a spectral stack to be accepted.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-12",
)

# The star-detection settings to try for spectral frames, in order (see
# `siril.stacking.script_builders.SPECTRAL_STAR_DETECTION_COMMANDS`).
# "phase_correlation" is not a detection setting at all -- it tells
# `process_target` to skip
# Siril's own star-based registration entirely and align the frames in
# Python instead (see `spectral_frame_alignment`). It is tried last, not
# first, because it is unvalidated against the wide, star-rich fields
# "standard" already handles well (99-125 stars/frame in a real SA200
# session), and because it cannot benefit from Siril's own two-pass
# reference selection or frame filtering the way star-based registration
# can.
SPECTRAL_STAR_DETECTION_ORDER = ("standard", "relaxed", "phase_correlation")


def split_into_stack_batches(frames: list[Any], is_spectral: bool) -> list[ExposureGroup]:
    """Split the frames of a stack into the batches it stacks one by one.

    Frames of different exposure lengths are stacked one length at a time,
    for spectra and for images. Each length needs the dark frames of its own
    length, and the normalization and rejection of one stack cannot treat
    frames of different lengths as one population (see `exposure_groups`).
    The stack check (`predict_stack`) uses this same split, so it reports the
    batches the stack will make.

    Parameters
    ----------
    frames : `list`
        The frame records to stack.
    is_spectral : `bool`
        Whether they are spectroscopy frames. Those are also split by night:
        the zero order sits on a different pixel each night and registering
        two nights together can lose most frames (see
        `split_groups_by_night`).

    Returns
    -------
    groups : `list` [`ExposureGroup`]
        The batches, shortest exposure first. Always at least one.
    """
    from astrometricslib.pipelines.stacking.processing.exposure_groups import (
        split_frames_by_exposure,
        split_groups_by_night,
    )

    groups = split_frames_by_exposure(frames)
    if is_spectral:
        groups = split_groups_by_night(groups)
    return groups


def run_stack(
    engine: StackingDriver,
    frames: list[Any],
    target_id: str,
    output_file: str | None,
    log_file: str | None,
    is_spectral: bool,
    job_id: str | None = None,
    drivers: Drivers | None = None,
    **stack_options: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Stack frames with Siril, one exposure length at a time if they differ.

    Every stack's diagnostics carry an ``"exposure_group_summaries"`` list (one
    entry per exposure length, see `exposure_group_report`).

    Parameters
    ----------
    engine : `StackingDriver`
        The program that stacks each batch of frames.
    frames : `list`
        The frame records to stack (or dictionaries of them).
    target_id : `str`
        The target's name, used for Siril's working folder.
    output_file : `str` or `None`
        The file name of the stacked image, which is written to the target's
        library folder. `None` uses the driver's own name for the target.
    log_file : `str` or `None`
        Where Siril's output is logged.
    is_spectral : `bool`
        Whether these are spectroscopy frames. The registration fallback
        applies only to them.
    job_id : `str`, optional
        The tracked job the stack belongs to, passed to the engine so it can
        report progress.
    drivers : `Drivers`, optional
        The outside services the caller chose. Used where stacking measures
        spectra, such as lining up exposure groups. The stacking program is
        `engine`, not ``drivers.stacking``.
    **stack_options
        The fields of `StackSettings` (``rejection_sigma``,
        ``filter_wfwhm``, ``filter_round``, ``stack_weight`` and
        ``generate_rejmap``).

    Returns
    -------
    stacked_path : `str` or `None`
        Where the stacked image was written, or `None` if stacking failed.
    diagnostics : `dict`
        What the driver reported about the run. For a stack made from several
        exposure groups, the per-frame lists are joined in group order and an
        ``"exposure_groups"`` entry describes each group.
    """
    if output_file is None:
        output_file = f"{target_id.replace(' ', '_')}_Stacked.fits"
    groups = split_into_stack_batches(frames, is_spectral)
    if sum(len(group.frames) for group in groups) < len(frames):
        # The frame screening records these frames (see `frame_screening`);
        # a caller that skipped it still must not stack them.
        logger.warning(
            "Leaving %d frame(s) out of the stack: their exposure has no group near enough.",
            len(frames) - sum(len(group.frames) for group in groups),
        )
        frames = [frame for group in groups for frame in group.frames]
    if len(groups) > 1:
        stacked_path, diagnostics = _stack_exposure_groups(
            engine,
            groups,
            target_id,
            output_file,
            log_file,
            is_spectral,
            job_id=job_id,
            drivers=drivers,
            **stack_options,
        )
    else:
        stacked_path, diagnostics = _stack_one_batch(
            engine, frames, target_id, output_file, log_file, is_spectral, job_id=job_id, **stack_options
        )
        if stacked_path is not None:
            _describe_single_group(frames, diagnostics, stacked_path)
    if stacked_path is not None:
        # Read once, from the final files, so a stack made of several groups
        # reports the combined registration and rejection results.
        diagnostics.update(engine.read_stack_artifacts(stacked_path))
    return stacked_path, diagnostics


def run_siril_stack(
    siril_driver: Any,
    frames: list[Any],
    target_id: str,
    output_file: str | None,
    log_file: str | None,
    is_spectral: bool,
    job_id: str | None = None,
    **stack_options: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Stack frames with a Siril driver (`run_stack` with Siril as the engine).

    Kept for callers that hold an `ImageProcessing` driver. The driver is
    wrapped in `SirilStackingDriver`. A caller with another engine uses
    `run_stack` directly.

    Returns
    -------
    stacked_path : `str` or `None`
        Where the stacked image was written, or `None` if stacking failed.
    diagnostics : `dict`
        What the run reported (see `run_stack`).
    """
    from astrometricslib._internal.drivers.siril.stacking.stacking_driver import SirilStackingDriver

    return run_stack(
        SirilStackingDriver(siril_driver),
        frames,
        target_id,
        output_file,
        log_file,
        is_spectral,
        job_id=job_id,
        **stack_options,
    )


def _stack_one_batch(
    engine: StackingDriver,
    frames: list[Any],
    target_id: str,
    output_file: str,
    log_file: str | None,
    is_spectral: bool,
    job_id: str | None = None,
    **stack_options: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Run Siril once for frames that can be stacked together.

    For spectral frames the standard star detection is tried first. If too
    few frames register, the relaxed detection is tried as well and the run
    that registered more frames is kept. The relaxed detection is not tried
    when Siril failed to answer a command: that is a Siril problem, not a
    star detection problem.

    Returns
    -------
    stacked_path : `str` or `None`
        Where the stacked image was written, or `None` if every attempt failed.
    diagnostics : `dict`
        The driver's diagnostics for the kept run, with
        ``"spectral_star_detection"`` and ``"registered_fraction"`` added for
        spectral frames.
    """
    minimum_registered_fraction = resolve(MINIMUM_REGISTERED_FRACTION).value
    image_files = [frame.model_dump() if hasattr(frame, "model_dump") else frame for frame in frames]
    modes = SPECTRAL_STAR_DETECTION_ORDER if is_spectral else (None,)
    settings = StackSettings(**stack_options)
    attempts = []
    for attempt_index, mode in enumerate(modes):
        attempt_output = output_file if attempt_index == 0 else _with_suffix(output_file, f"_{mode}")
        attempt_log = log_file if attempt_index == 0 else _with_suffix(log_file, f"_{mode}")
        result = engine.stack_batch(
            image_files,
            target_id,
            attempt_output,
            attempt_log,
            is_spectral,
            settings,
            registration=mode,
            job_id=job_id,
            steps=PIPELINE_STEPS,
        )
        path = result.stacked_path
        diagnostics = dict(result.diagnostics)
        diagnostics["stacking_engine"] = result.engine_name
        diagnostics["stacking_engine_version"] = result.engine_version
        if path and _is_stack_blank(path):
            # A blank stack is not a result. Without this its unreported
            # registration counts as 100% (see `_registered_fraction`), so
            # it would beat a real stack that registered fewer frames.
            logger.warning(
                "The stack of %d frames for '%s' with %s star detection is blank; discarding it.",
                len(frames),
                target_id,
                mode or "the default",
            )
            path = None
        fraction = _registered_fraction(diagnostics)
        if mode is not None:
            diagnostics["spectral_star_detection"] = mode
            diagnostics["registered_fraction"] = fraction
        attempts.append((path, diagnostics, fraction))
        communication_error = diagnostics.get(SIRIL_COMMUNICATION_ERROR_KEY)
        if communication_error and not path:
            # Siril stopped answering. Other star detection settings would
            # not help, so the run is not repeated with them.
            logger.error(
                "Stacking %d frames for '%s' stopped because Siril did not answer: %s",
                len(frames),
                target_id,
                communication_error,
            )
            break
        if path and fraction >= minimum_registered_fraction:
            break
        if mode is not None and attempt_index + 1 < len(modes):
            if path:
                logger.warning(
                    "Only %.0f%% of %d frames registered for '%s' with %s star detection; trying %s.",
                    100 * fraction,
                    len(frames),
                    target_id,
                    mode,
                    modes[attempt_index + 1],
                )
            else:
                # Siril gave up before it reported any registration totals.
                logger.warning(
                    "Stacking %d frames for '%s' failed with %s star detection; trying %s.",
                    len(frames),
                    target_id,
                    mode,
                    modes[attempt_index + 1],
                )

    usable = [attempt for attempt in attempts if attempt[0]]
    if not usable:
        return None, attempts[-1][1]
    kept = max(usable, key=lambda attempt: (attempt[2], _registered_count(attempt[1])))
    if len(attempts) > 1:
        _keep_only(kept[0], [attempt[0] for attempt in attempts if attempt[0]], output_file)
        kept_path = os.path.join(os.path.dirname(kept[0]), output_file)
    else:
        kept_path = kept[0]
    if is_spectral and kept[2] < minimum_registered_fraction:
        logger.warning(
            "Only %.0f%% of the %d frames for '%s' could be registered (%s star detection); "
            "the stack is built from the rest.",
            100 * kept[2],
            len(frames),
            target_id,
            kept[1].get("spectral_star_detection"),
        )
    return kept_path, kept[1]


def _is_stack_blank(stack_path: str) -> bool:
    """Tell whether a stacked file holds no data.

    Parameters
    ----------
    stack_path : `str`
        The stacked image Siril wrote.

    Returns
    -------
    is_blank : `bool`
        `True` when almost none of the pixels hold data (see
        `MINIMUM_STACK_DATA_FRACTION`), `False` otherwise or when the file
        cannot be read (an unreadable file is left for later steps to report).
    """
    import numpy as np

    from astrometricslib._internal.drivers.fits_access import read_data
    from astrometricslib.pipelines.stacking.processing.group_gains import (
        MINIMUM_STACK_DATA_FRACTION,
        stack_data_fraction,
    )

    try:
        return (
            stack_data_fraction(np.asarray(read_data(stack_path)))
            < resolve(MINIMUM_STACK_DATA_FRACTION).value
        )
    except OSError:
        return False


def _registered_fraction(diagnostics: dict[str, Any]) -> float:
    """Work out what share of frames Siril registered.

    Returns
    -------
    fraction : `float`
        The share from 0 to 1, or 1.0 when Siril did not report it (so an
        unreported run is not retried).
    """
    failed = diagnostics.get("registration_failed_frames")
    registered = diagnostics.get("registered_frames")
    if failed is None or registered is None or failed + registered == 0:
        return 1.0
    return registered / (failed + registered)


def _registered_count(diagnostics: dict[str, Any]) -> int:
    """Read how many frames Siril registered.

    Returns
    -------
    count : `int`
        The number registered, or 0 when Siril did not say.
    """
    return int(diagnostics.get("registered_frames") or 0)


def _with_suffix(name: str | None, suffix: str) -> str | None:
    """Add a suffix before a file name's extension.

    Returns
    -------
    new_name : `str` or `None`
        The changed name, or `None` when `name` is `None`.
    """
    if name is None:
        return None
    stem, extension = os.path.splitext(name)
    return f"{stem}{suffix}{extension}"


def _sibling_paths(stack_path: str) -> list[str]:
    """List the files the driver writes next to a stacked image.

    Returns
    -------
    paths : `list` [`str`]
        The stack itself, its rejection map and its registration sequence.
    """
    stem = os.path.splitext(stack_path)[0]
    return [stack_path, f"{stem}_RejMap.fits", f"{stem}_Registration.seq"]


def _keep_only(kept_path: str, all_paths: list[str], output_file: str) -> None:
    """Keep one attempt's files under the requested name and delete the others.

    Every attempt wrote its own stack, rejection map and registration
    sequence. The kept attempt's files are moved to the names asked for, and
    the rest are removed.

    Parameters
    ----------
    kept_path : `str`
        The stack of the attempt to keep.
    all_paths : `list` [`str`]
        The stacks of every attempt that produced one.
    output_file : `str`
        The file name the caller asked for.
    """
    directory = os.path.dirname(kept_path)
    final_path = os.path.join(directory, output_file)
    # The losers go first. One of them may hold the very names the kept
    # attempt is about to be moved to, and deleting afterwards would delete
    # the kept attempt's files.
    for path in all_paths:
        if path == kept_path:
            continue
        for leftover in _sibling_paths(path):
            if os.path.exists(leftover):
                os.remove(leftover)
    for source, destination in zip(_sibling_paths(kept_path), _sibling_paths(final_path), strict=True):
        if source != destination and os.path.exists(source):
            shutil.move(source, destination)


def _describe_single_group(frames: list[Any], diagnostics: dict[str, Any], stacked_path: str) -> None:
    """Add the group entry and recommended exposure of a one-length stack.

    A set of frames that `split_frames_by_exposure` left as one group is
    almost always one exposure length, but it can also hold a few frames of
    another length that were too few to form a group of their own. The group
    is named after the most common length (`representative_exposure_seconds`).

    Two measurements read raw frames: saturation, and the share of pixels at
    exactly zero (a frame clipped at zero cannot show the faint sky, see
    `CLIPPED_FRAME_ZERO_FRACTION`). The zero share reads
    `FRAMES_SAMPLED_PER_GROUP` frames of the representative length, so it
    costs the same as one group of a multi-length stack. A failure in either
    measurement must never cost the stack, so problems are logged and the
    entry is left without that reading (and the stack's raw-clipping gate is
    then "not checked").

    Parameters
    ----------
    frames : `list`
        The frames that were stacked (one exposure length, give or take a
        few strays).
    diagnostics : `dict`
        The run's diagnostics, added to in place. Gets
        ``"exposure_group_summaries"``
        and, when the zero share was measured,
        ``"exposure_group_frame_zero_fractions"`` and
        ``"clipped_exposure_groups"``.
    stacked_path : `str`
        The stack, recorded as the group's path.
    """
    from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import (
        build_group_summary,
        measure_group_saturation,
    )
    from astrometricslib.pipelines.stacking.processing.exposure_groups import (
        ExposureGroup,
        representative_exposure_frames,
        representative_exposure_seconds,
    )
    from astrometricslib.pipelines.stacking.processing.group_combination import (
        CLIPPING_FLOOR_SIGMAS,
        measure_group_frames,
    )
    from astrometricslib.pipelines.stacking.processing.group_gains import describe_group_clipping

    clipping_floor_sigmas = resolve(CLIPPING_FLOOR_SIGMAS).value

    exposure = representative_exposure_seconds(frames)
    try:
        saturation = measure_group_saturation(frames)
    except (*FITS_READ_ERRORS, *DATA_ERRORS) as measurement_error:  # a measurement must not cost the stack
        logger.warning("Could not measure saturation for '%s': %s", stacked_path, measurement_error)
        saturation = []

    clipped_at_zero = False
    try:
        _, zero_fractions = measure_group_frames([
            ExposureGroup(exposure, representative_exposure_frames(frames))
        ])
    except (*FITS_READ_ERRORS, *DATA_ERRORS) as measurement_error:  # a measurement must not cost the stack
        logger.warning(
            "Could not measure the clipping of the raw frames of '%s': %s", stacked_path, measurement_error
        )
    else:
        measured, clipped = describe_group_clipping([exposure], zero_fractions)
        diagnostics["exposure_group_frame_zero_fractions"] = measured
        diagnostics["clipped_exposure_groups"] = clipped
        clipped_at_zero = bool(clipped)
        for entry in clipped:
            # There is no other exposure group to take the faint pixels from.
            logger.warning(
                "The %g s frames of '%s' are clipped at zero (%.0f%% of pixels), and the stack has no "
                "other exposure group to take its faint pixels from (a pixel less than %g frame noises "
                "above zero reads too high). Longer exposures or a higher camera offset avoid this.",
                entry["exposure_s"],
                stacked_path,
                100 * entry["frame_zero_fraction"],
                clipping_floor_sigmas,
            )
    diagnostics["exposure_group_summaries"] = [
        build_group_summary(
            exposure,
            frames,
            diagnostics,
            saturation,
            stack_path=stacked_path,
            clipped_at_zero=clipped_at_zero,
        )
    ]


@dataclass
class GroupResult:
    """Everything the combine learns about one exposure group that was stacked.

    One of these is made when a group's own stack is written. Each later
    phase of `_stack_exposure_groups` fills in more of it, so no phase has to
    keep parallel lists indexed by position. A group is combined when
    ``left_out_reason`` is still `None` after lining up and the brightness
    checks (see `used_groups`).

    Attributes
    ----------
    group : `ExposureGroup`
        The group's frames and exposure length.
    path : `str`
        The group's own stack, where the engine wrote it.
    diagnostics : `dict`
        What the engine reported for the group's run.
    summary_index : `int`
        The group's place in the ``"exposure_group_summaries"`` list.
    exposure_s : `float`
        The exposure length of one frame, in seconds.
    count : `int`
        How many frames the group's stack holds.
    artifacts : `dict`
        The registration facts read next to the stack.
    image : `numpy.ndarray`
        The stack's pixels (``float32``), derotated if the groups were
        lined up onto one trail tilt.
    frame_noise : `float`
        The noise of one raw frame in counts (a stand-in of 1 until measured).
    frame_zero_fraction : `float`
        The share of raw pixels clipped at zero (0 until measured).
    weight : `float`
        The combine weight (see `group_weight`), set when lining up.
    aligned_image : `numpy.ndarray` or `None`
        The stack lined up with the reference, or `None` if that failed.
    covered_mask : `numpy.ndarray` or `None`
        Where the lined-up stack has data.
    alignment : `GroupAlignment` or `None`
        How the stack was moved onto the reference.
    left_out_reason : `str` or `None`
        Why the group is not combined, or `None` if it is.
    gain_measurement : `GroupGainMeasurement` or `None`
        The brightness check of the group, if one was made.
    gain_applied : `float` or `None`
        The gain the combine applied, if one was measured.
    kept_path : `str` or `None`
        Where the group's stack was moved to, in the ``groups`` folder.
    """

    group: Any
    path: str
    diagnostics: dict[str, Any]
    summary_index: int
    exposure_s: float
    count: int
    artifacts: dict[str, Any] = field(default_factory=dict)
    image: Any = None
    frame_noise: float = 1.0
    frame_zero_fraction: float = 0.0
    weight: float = 0.0
    aligned_image: Any = None
    covered_mask: Any = None
    alignment: Any = None
    left_out_reason: str | None = None
    gain_measurement: Any = None
    gain_applied: float | None = None
    kept_path: str | None = None


def used_groups(results: list[GroupResult]) -> list[GroupResult]:
    """Pick the groups that are combined, in group order.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        Every group that was stacked.

    Returns
    -------
    used : `list` [`GroupResult`]
        The groups that have no ``left_out_reason``.
    """
    return [result for result in results if result.left_out_reason is None]


@dataclass
class _GroupStackMeasurements:
    """What `_measure_group_stacks` learned that belongs to all the groups.

    Attributes
    ----------
    clip_ceiling_adu : `float` or `None`
        The camera's clip ceiling, which sets the saturation level of a group
        stack that shows no plateau.
    clipped_groups : `list` [`dict`]
        The groups whose raw frames are clipped at zero.
    measured_zero_fractions : `list` [`dict`]
        Every group's measured zero fraction (empty when the frames could not
        be read).
    frame_noises_read : `bool`
        Whether the frame noises are real measurements, not stand-ins.
    """

    clip_ceiling_adu: float | None
    clipped_groups: list[dict[str, Any]]
    measured_zero_fractions: list[dict[str, Any]]
    frame_noises_read: bool


def _stack_exposure_groups(
    engine: StackingDriver,
    groups: list[Any],
    target_id: str,
    output_file: str,
    log_file: str | None,
    is_spectral: bool,
    job_id: str | None = None,
    drivers: Drivers | None = None,
    **stack_options: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Stack each exposure group on its own and combine the results.

    Each group is stacked with the dark frames of its own exposure length. The
    group stacks are kept in the ``groups`` folder next to the combined stack,
    lined up with the group that carries the most weight, and combined (see
    `combine_exposure_group_images`). A group that cannot be stacked or lined
    up is left out and named in the diagnostics. So is a group whose
    brightness is not one scale factor of the reference group's: the flux
    ratios of its faint and bright stars (or, for a field with too few stars,
    its mid-range and bright-end pixel ratios) differ by more than
    `AppConfiguration.get_exposure_group_gain_tolerance`. So is a group whose
    sky is more than `SKY_RATIO_LIMIT` times brighter than the reference's.
    Its entry in ``"exposure_group_summaries"`` records the ratios and the
    reason.

    ``drivers`` is the outside services the caller chose, passed to the
    trail measurements that line spectral groups up.

    The work runs in phases, each in its own function, and each group's data
    travels in one `GroupResult`: stack each group (`_stack_each_group`),
    measure the group stacks (`_measure_group_stacks`), line them up and
    screen them (`_align_and_screen_groups`), write the combined stack
    (`_write_combined_stack`), describe every group (`_describe_groups`) and
    join the diagnostics (`_join_group_diagnostics`).

    Returns
    -------
    stacked_path : `str` or `None`
        Where the combined image was written, or `None` if no group stacked.
    diagnostics : `dict`
        The groups' diagnostics joined in group order, with an
        ``"exposure_groups"`` entry per group, an
        ``"exposure_group_summaries"`` list (one entry per exposure length,
        including any left out), a ``"clipped_exposure_groups"``
        list of the groups whose raw frames are clipped at zero, and an
        ``"exposure_group_frame_zero_fractions"`` list with every group's
        measured zero fraction (empty when the frames could not be read), an
        ``"exposure_group_weights"`` list with one entry for each group that
        was combined (see `_exposure_group_weight_entries`), an
        ``"exposure_group_alignments"`` list (each combined group's frame
        count and its position map onto the reference group's grid, see
        `GroupPositionMap.to_record`), and a
        ``"covered_in_every_group"`` mask of the pixels every combined group
        has data at.
    """
    results, failed_groups = _stack_each_group(
        engine, groups, target_id, output_file, log_file, is_spectral, job_id, stack_options
    )
    if not results:
        return None, {}
    measurements = _measure_group_stacks(engine, results, target_id, is_spectral)
    _align_and_screen_groups(results, measurements, target_id, is_spectral, drivers)
    final_path, combination, stacked_fwhm = _write_combined_stack(
        results, measurements, output_file, is_spectral
    )
    summaries = _describe_groups(results, failed_groups, final_path, target_id)
    merged_diagnostics = _join_group_diagnostics(
        results,
        summaries,
        measurements,
        combination,
        stacked_fwhm,
        stack_options.get("stack_weight"),
    )
    return final_path, merged_diagnostics


def _stack_each_group(
    engine: StackingDriver,
    groups: list[Any],
    target_id: str,
    output_file: str,
    log_file: str | None,
    is_spectral: bool,
    job_id: str | None,
    stack_options: dict[str, Any],
) -> tuple[list[GroupResult], list[tuple[Any, dict[str, Any]]]]:
    """Stack every exposure group on its own with the engine.

    Parameters
    ----------
    engine : `StackingDriver`
        The stacking engine.
    groups : `list` [`ExposureGroup`]
        The exposure groups to stack.
    target_id : `str`
        The target's name, for the log.
    output_file : `str`
        The name of the combined stack; each group's file adds a suffix.
    log_file : `str` or `None`
        The engine log name; each group's log adds a suffix.
    is_spectral : `bool`
        Whether the frames are spectral.
    job_id : `str` or `None`
        The job these stacks belong to.
    stack_options : `dict`
        Further options for `_stack_one_batch`.

    Returns
    -------
    results : `list` [`GroupResult`]
        One entry for each group that stacked, in group order.
    failed_groups : `list` [`tuple`]
        The group and the engine's diagnostics for each group that did not
        stack.
    """
    logger.info(
        "Stacking '%s' as %d exposure groups (%s s) and combining them.",
        target_id,
        len(groups),
        ", ".join(f"{group.exposure_s:g}" + (f" ({group.night})" if group.night else "") for group in groups),
    )
    results: list[GroupResult] = []
    failed_groups = []
    # Groups at one exposure length but different ISOs would share a file
    # name, so the ISO joins the name whenever the groups' ISOs differ.
    mixed_isos = len({group.gain for group in groups}) > 1
    for group in groups:
        tag = f"exp{group.exposure_s:g}s".replace(".", "p")
        if mixed_isos and group.gain is not None:
            tag += f"_iso{group.gain:g}".replace(".", "p")
        if group.night is not None:
            tag += f"_{group.night}"
        path, diagnostics = _stack_one_batch(
            engine,
            group.frames,
            target_id,
            _with_suffix(output_file, f"_{tag}"),
            _with_suffix(log_file, f"_{tag}"),
            is_spectral,
            job_id=job_id,
            **stack_options,
        )
        if path is None:
            logger.warning(
                "The %g s exposure group (%d frames) of '%s' could not be stacked; leaving it out.",
                group.exposure_s,
                len(group.frames),
                target_id,
            )
            failed_groups.append((group, diagnostics))
            continue
        results.append(
            GroupResult(
                group=group,
                path=path,
                diagnostics=diagnostics,
                summary_index=len(results),
                exposure_s=group.exposure_s,
                count=int(diagnostics.get("images_stacked") or len(group.frames)),
            )
        )
    return results, failed_groups


def _measure_group_stacks(
    engine: StackingDriver, results: list[GroupResult], target_id: str, is_spectral: bool
) -> _GroupStackMeasurements:
    """Read the group stacks and measure the raw frames behind them.

    Reads each group's registration facts and pixels and measures each
    group's frame noise and zero clipping. Fills in ``artifacts``,
    ``image``, ``frame_noise`` and ``frame_zero_fraction`` of every
    `GroupResult`.

    Parameters
    ----------
    engine : `StackingDriver`
        The stacking engine, which reads the registration facts.
    results : `list` [`GroupResult`]
        The stacked groups; updated in place.
    target_id : `str`
        The target's name, for the log.
    is_spectral : `bool`
        Whether the frames are spectral.

    Returns
    -------
    measurements : `_GroupStackMeasurements`
        The measurements that belong to all the groups.
    """
    import numpy as np

    from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
    from astrometricslib._internal.drivers.fits_access import read_data
    from astrometricslib.pipelines.stacking.processing.group_combination import (
        CLIPPING_FLOOR_SIGMAS,
        measure_group_frames,
    )
    from astrometricslib.pipelines.stacking.processing.group_gains import describe_group_clipping

    clipping_floor_sigmas = resolve(CLIPPING_FLOOR_SIGMAS).value

    # Each group's own registration facts, read while its sequence file is
    # still next to its stack: the frame weights and the reference frame of
    # the combined stack are worked out per group (see
    # `stack_membership.group_frame_weights`).
    for result in results:
        result.artifacts = _read_group_artifacts(engine, result.path, result.diagnostics)
    for result in results:
        result.image = np.asarray(read_data(result.path), dtype=np.float32)
    kept_groups = [result.group for result in results]

    camera_name = _first_camera_name(kept_groups)

    # The camera's clip ceiling sets the saturation level of a group stack
    # that shows no plateau (a 14-bit Nikon never reaches 0.95 of full scale).
    clip_ceiling_adu = resolve_camera_profile(camera_name).clip_ceiling_adu.value if camera_name else None

    zero_fractions_measured = True
    frame_noises_read = True
    try:
        frame_noises, frame_zero_fractions = measure_group_frames(kept_groups)
    except OSError as read_error:
        # The weights should never cost the stack: without frame noises every
        # group is treated as equally noisy per frame, which weights by
        # frames x exposure^2.
        logger.warning(
            "Could not read frames to measure their noise (%s); weighting by exposure only.", read_error
        )
        frame_noises = [1.0] * len(kept_groups)
        frame_zero_fractions = [0.0] * len(kept_groups)
        zero_fractions_measured = False
        # The 1.0 above only keeps the combine going. It is not a noise, so
        # the frame weights of the stack are unknown (see
        # `_exposure_group_weight_entries`), never built from it.
        frame_noises_read = False
    for result, frame_noise, frame_zero_fraction in zip(
        results, frame_noises, frame_zero_fractions, strict=True
    ):
        result.frame_noise = frame_noise
        result.frame_zero_fraction = frame_zero_fraction
    measured_zero_fractions, clipped_groups = describe_group_clipping(
        [result.exposure_s for result in results], frame_zero_fractions
    )
    if not zero_fractions_measured:
        # The zeros above are stand-ins, not measurements: the raw-clipping
        # gate must read "not checked", not "passed".
        measured_zero_fractions = []
    for entry in clipped_groups:
        logger.warning(
            "The %g s frames of '%s' are clipped at zero (%.0f%% of pixels): where a pixel is less than "
            "%g frame noises above zero it is taken from the other exposure groups.",
            entry["exposure_s"],
            target_id,
            100 * entry["frame_zero_fraction"],
            clipping_floor_sigmas,
        )
    if clipped_groups and len(clipped_groups) == len(kept_groups):
        logger.warning(
            "Every exposure group of '%s' is clipped at zero, so its faint pixels read too high and no "
            "other group can replace them. Longer exposures or a higher camera offset avoid this.",
            target_id,
        )
    return _GroupStackMeasurements(
        clip_ceiling_adu=clip_ceiling_adu,
        clipped_groups=clipped_groups,
        measured_zero_fractions=measured_zero_fractions,
        frame_noises_read=frame_noises_read,
    )


def alignment_left_out_reason(reference_exposure: float, alignment: Any) -> str:
    """Say why a group could not be lined up with the reference group.

    The reason is the one the method that failed gave: Siril's own account of
    a stack it could not solve, or the correlation of a shift-only
    measurement.

    Parameters
    ----------
    reference_exposure : `float`
        The exposure time, in seconds, of the group the others are lined up
        with.
    alignment : `AlignmentResult`
        The failed alignment of the group.

    Returns
    -------
    reason : `str`
        The sentence stored as the group's left-out reason.
    """
    cause = alignment.failure_reason or f"correlation {alignment.correlation:.2f}"
    return f"could not be lined up with the {reference_exposure:g} s group ({cause})"


def _first_camera_name(groups: list[Any]) -> str | None:
    """Give the camera that took the first frame that names one.

    Parameters
    ----------
    groups : `list` [`ExposureGroup`]
        The exposure groups.

    Returns
    -------
    camera_name : `str` or `None`
        The camera's name, or `None` if no frame names one.
    """
    return next(
        (
            getattr(frame, "camera", None)
            for group in groups
            for frame in group.frames
            if getattr(frame, "camera", None)
        ),
        None,
    )


def _shift_alignment(
    images: list[Any], reference_index: int, crop_fraction: float | None, prefer_star_position: bool
) -> tuple[list[Any], list[Any], list[Any]]:
    """Line the images up with the reference by a shift, and name the move.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The group stacks.
    reference_index : `int`
        The place of the image the others are lined up with.
    crop_fraction : `float` or `None`
        The share of each side used to measure a spectral offset.
    prefer_star_position : `bool`
        Whether to measure the offset on the zero-order star.

    Returns
    -------
    aligned, covered, alignments : `list`
        As `align_images_to_reference` gives them, except that the reference
        has an `AlignmentResult` (no shift, ``method="none"``, marked as the
        reference) instead of `None`.
    """
    from astrometricslib.pipelines.stacking.processing.group_alignment import (
        AlignmentResult,
        align_images_to_reference,
    )

    aligned, covered, alignments = align_images_to_reference(
        images, reference_index, crop_fraction=crop_fraction, prefer_star_position=prefer_star_position
    )
    alignments[reference_index] = AlignmentResult(0.0, 0.0, 1.0, method="none", is_reference=True)
    return aligned, covered, alignments


def _siril_alignment(
    results: list[GroupResult], reference_index: int, target_id: str
) -> tuple[list[Any], list[Any], list[Any]]:
    """Line the group stacks up with Siril's plate-solve registration.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups. Their stack files are read by Siril.
    reference_index : `int`
        The place of the group the others are lined up with.
    target_id : `str`
        The target's name, for the log.

    Returns
    -------
    aligned, covered, alignments : `list`
        The lined-up stacks, their data masks and the moves, from
        `align_group_stacks_with_siril`. An entry is `None` (and its move
        carries Siril's reason) for a stack Siril could not solve.
    """
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.stacking.processing.siril_group_alignment import (
        align_group_stacks_with_siril,
    )

    siril_executable = get_configuration().get_siril_executable()
    aligned, covered, alignments = align_group_stacks_with_siril(
        [result.path for result in results], reference_index, siril_executable
    )
    for result, alignment in zip(results, alignments, strict=True):
        if alignment.failure_reason is None:
            logger.info(
                "Siril lined the %g s exposure group of '%s' up with %d of %s stars matched to Gaia "
                "(%.2f px left, fit order %s): shift (%.2f, %.2f) px, rotation %.4f degrees, scale %.5f.",
                result.exposure_s,
                target_id,
                alignment.matched_stars or 0,
                alignment.stars_found,
                alignment.fit_residual_pixels or float("nan"),
                alignment.fit_order,
                alignment.shift_rows_pixels,
                alignment.shift_columns_pixels,
                alignment.rotation_degrees,
                alignment.scale,
            )
    return aligned, covered, alignments


def _keep_reference_unmoved(
    results: list[GroupResult],
    reference_index: int,
    aligned: list[Any],
    covered: list[Any],
    alignments: list[Any],
) -> None:
    """Keep a reference group that Siril could not solve as it is.

    Every other stack is lined up with the reference, so when Siril cannot
    solve the reference no stack is lined up and the others are left out. The
    reference itself still stands, unmoved, so the combined stack is the
    reference group alone instead of nothing. The reference's alignment keeps
    Siril's reason and the group's own image becomes its lined-up image.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups.
    reference_index : `int`
        The place of the reference group.
    aligned, covered, alignments : `list`
        Siril's lists, changed in place at the reference's place.
    """
    import numpy as np

    from astrometricslib.pipelines.stacking.processing.group_alignment import AlignmentResult

    reason = alignments[reference_index].failure_reason
    aligned[reference_index] = np.asarray(results[reference_index].image, dtype=np.float32)
    covered[reference_index] = np.ones(aligned[reference_index].shape[-2:], dtype=bool)
    alignments[reference_index] = AlignmentResult(0.0, 0.0, 1.0, method="none", is_reference=True)
    logger.warning(
        "Siril could not solve the reference group (%s), so no group could be lined up with it.", reason
    )


def _line_up_spectral_groups(
    results: list[GroupResult],
    reference_index: int,
    target_id: str,
    crop_fraction: float,
    drivers: Drivers | None,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Line spectral group stacks up by Siril, or by the zero order.

    1. Siril solves every group stack and moves the solved ones onto the
       reference's grid (see `siril_group_alignment`).
    2. The stacks are turned about their zero-order stars onto one trail tilt
       (see `group_derotation`). The tilt is measured on the lined-up stacks,
       so a turn Siril made is not counted twice.
    3. A group that Siril could not solve is shifted so its zero-order star
       lands on the reference's (`group_alignment.measure_alignment`). That is
       the case for the shortest exposures, where the stack holds too few
       stars to match. Which groups go this way is decided by Siril's own
       answer for each group, not by a star count of ours, and each such group
       is logged with Siril's reason.

    When the reference itself is not solved, Siril lines nothing up and every
    group takes the zero-order path.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups, with ``image`` set.
    reference_index : `int`
        The place of the reference group.
    target_id : `str`
        The target's name, for the log.
    crop_fraction : `float`
        The share of each side used to measure a zero-order offset.
    drivers : `Drivers`, optional
        The outside services the caller chose, for the trail measurements.

    Returns
    -------
    aligned, covered, alignments : `list`
        One entry for each group, like `align_images_to_reference`'s lists.
    """
    import numpy as np

    from astrometricslib._internal.drivers.fits_access import read_header, write_image
    from astrometricslib.foundation.paths import scratch_directory
    from astrometricslib.pipelines.stacking.processing.group_derotation import derotate_groups_to_common_tilt

    count = len(results)
    aligned, covered, alignments = _siril_alignment(results, reference_index, target_id)
    solved = [image is not None for image in aligned]
    images = [aligned[index] if solved[index] else results[index].image for index in range(count)]

    camera_name = _first_camera_name([result.group for result in results])
    if camera_name:
        with tempfile.TemporaryDirectory(prefix="group_tilt_", dir=scratch_directory()) as folder:
            # The trail is measured in a file, so a stack Siril moved is
            # written out under its group's header.
            paths = []
            for index, result in enumerate(results):
                if solved[index]:
                    path = os.path.join(folder, f"group_{index}.fits")
                    write_image(path, images[index], read_header(result.path))
                    paths.append(path)
                else:
                    paths.append(result.path)
            derotated, trail_angles = derotate_groups_to_common_tilt(
                images, paths, camera_name, drivers=drivers
            )
        for result, angle in zip(results, trail_angles, strict=True):
            if angle is None:
                logger.warning(
                    "The %g s exposure group of '%s' had no trustworthy trail tilt to line up; "
                    "it is combined without derotation.",
                    result.exposure_s,
                    target_id,
                )
        images = derotated

    if solved[reference_index]:
        shifted = [index for index in range(count) if not solved[index]]
    else:
        shifted = [index for index in range(count) if index != reference_index]
        alignments[reference_index] = None
    if shifted:
        sub_images = [images[reference_index], *(images[index] for index in shifted)]
        sub_aligned, sub_covered, sub_alignments = _shift_alignment(
            sub_images, 0, crop_fraction=crop_fraction, prefer_star_position=True
        )
        for position, index in enumerate(shifted, start=1):
            siril_reason = alignments[index].failure_reason if alignments[index] is not None else None
            aligned[index], covered[index], alignments[index] = (
                sub_aligned[position],
                sub_covered[position],
                sub_alignments[position],
            )
            alignments[index].siril_reason = siril_reason or "Siril did not line it up"
            if alignments[index].trusted:
                logger.warning(
                    "The %g s exposure group of '%s' was lined up by its zero-order star, not by Siril: %s.",
                    results[index].exposure_s,
                    target_id,
                    siril_reason or "Siril did not line it up",
                )
        if not solved[reference_index]:
            aligned[reference_index] = sub_aligned[0]
            covered[reference_index] = sub_covered[0]
            alignments[reference_index] = sub_alignments[0]
    for index in range(count):
        if solved[index]:
            aligned[index] = np.asarray(images[index], dtype=np.float32)
    alignments[reference_index].is_reference = True
    return aligned, covered, alignments


def _line_up_groups(
    results: list[GroupResult],
    reference_index: int,
    target_id: str,
    is_spectral: bool,
    drivers: Drivers | None = None,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Line the group stacks up with the reference by the method that fits.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups, with ``image`` set.
    reference_index : `int`
        The place of the group the others are lined up with.
    target_id : `str`
        The target's name, for the log and to tell a body of the solar system.
    is_spectral : `bool`
        Whether the frames are spectral.
    drivers : `Drivers`, optional
        The outside services the caller chose, for the spectral trail
        measurements.

    Returns
    -------
    aligned, covered, alignments : `list`
        One entry for each group: the lined-up stack (`None` for a group that
        could not be lined up), its data mask and its `AlignmentResult`.
    """
    from astrometricslib.pipelines.stacking.processing.group_alignment import (
        SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION,
    )
    from astrometricslib.utilities.solar_system_targets import is_solar_system_target

    if len(results) == 1:
        return _shift_alignment([results[0].image], 0, crop_fraction=None, prefer_star_position=False)
    if is_spectral:
        return _line_up_spectral_groups(
            results,
            reference_index,
            target_id,
            resolve(SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION).value,
            drivers,
        )
    if is_solar_system_target(target_id):
        return _shift_alignment(
            [result.image for result in results],
            reference_index,
            crop_fraction=None,
            prefer_star_position=False,
        )
    aligned, covered, alignments = _siril_alignment(results, reference_index, target_id)
    if aligned[reference_index] is None:
        _keep_reference_unmoved(results, reference_index, aligned, covered, alignments)
    return aligned, covered, alignments


def _align_and_screen_groups(
    results: list[GroupResult],
    measurements: _GroupStackMeasurements,
    target_id: str,
    is_spectral: bool,
    drivers: Drivers | None = None,
) -> None:
    """Line the group stacks up and leave out the groups that do not fit.

    Sets ``weight``, ``aligned_image``, ``covered_mask`` and ``alignment`` of
    every group, then ``left_out_reason`` and ``gain_measurement`` of the
    groups that cannot be lined up or whose brightness is not one scale
    factor of the reference group's.

    Every group is lined up with the group that carries the most weight (the
    one `combine_exposure_group_images` also uses as its brightness
    reference), so the combined image is not smeared by the offsets Siril
    leaves between groups. The method depends on the stack:

    - Imaging stacks are lined up by Siril, which plate-solves each group
      stack against the Gaia catalog and moves it onto the reference's grid.
      A group Siril cannot solve is left out, with Siril's reason; there is
      no other method for it.
    - Stacks of a body of the solar system (the Moon) have no stars to solve,
      so they are shifted by the offset phase correlation finds.
    - Spectral stacks use Siril for the groups it solves and the zero-order
      star for the others (`_line_up_spectral_groups`).

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups; updated in place.
    measurements : `_GroupStackMeasurements`
        The measurements that belong to all the groups.
    target_id : `str`
        The target's name, for the log.
    is_spectral : `bool`
        Whether the frames are spectral.
    drivers : `Drivers`, optional
        The outside services the caller chose, for the spectral trail
        measurements.
    """
    import numpy as np

    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.stacking.processing.exposure_groups import exposure_group_iso
    from astrometricslib.pipelines.stacking.processing.group_combination import (
        group_weight,
        measure_exposure_group_gains,
    )
    from astrometricslib.pipelines.stacking.processing.group_gains import (
        describe_bright_sky_group,
        describe_nonlinear_group,
        describe_unbelievable_gain_group,
        normalize_sky_ratios_by_iso,
    )

    for result in results:
        result.weight = group_weight(result.count, result.exposure_s, result.frame_noise)
    reference_index = int(np.argmax([result.weight for result in results]))
    reference = results[reference_index]
    aligned_images, covered_masks, alignments = _line_up_groups(
        results, reference_index, target_id, is_spectral, drivers
    )
    for result, aligned_image, covered_mask, alignment in zip(
        results, aligned_images, covered_masks, alignments, strict=True
    ):
        result.aligned_image = aligned_image
        result.covered_mask = covered_mask
        result.alignment = alignment
    for result in results:
        if result.aligned_image is None:
            reason = alignment_left_out_reason(reference.exposure_s, result.alignment)
            result.left_out_reason = reason
            logger.warning(
                "The %g s exposure group of '%s' %s; leaving it out.", result.exposure_s, target_id, reason
            )
    used = used_groups(results)
    if len(used) > 1:
        # A group whose brightness is not one scale factor of the reference
        # (its bright stars' flux ratio disagrees with its faint stars') is
        # marked nonlinear, whether its bright end is clipped or not (the
        # reason says which). Rescaling it would change
        # its faint stars by the wrong amount, so it is left out and the
        # others are combined, the same fallback as for a group that cannot
        # be lined up. So is a group whose sky is far brighter than the
        # reference's (see `SKY_RATIO_LIMIT`).
        reference_in_used, gain_measurements = measure_exposure_group_gains(
            [result.aligned_image for result in used],
            [result.exposure_s for result in used],
            [result.count for result in used],
            frame_noises=[result.frame_noise for result in used],
            frame_zero_fractions=[result.frame_zero_fraction for result in used],
            covered_masks=[result.covered_mask for result in used],
            tolerance=get_configuration().get_exposure_group_gain_tolerance(),
            clip_ceiling_adu=measurements.clip_ceiling_adu,
            use_star_fluxes=not is_spectral,
        )
        # Groups of different ISO have raw skies that differ by the ISO ratio
        # alone, so the sky is compared per unit ISO.
        normalize_sky_ratios_by_iso(
            gain_measurements,
            [exposure_group_iso(result.group.frames) for result in used],
            reference_in_used,
        )
        for result, gain_measurement in zip(used, gain_measurements, strict=True):
            result.gain_measurement = gain_measurement
        for result, gain_measurement in zip(used, gain_measurements, strict=True):
            if not (
                gain_measurement.nonlinear
                or gain_measurement.sky_too_bright
                or gain_measurement.gain_rejected
            ):
                continue
            reference_exposure = used[reference_in_used].exposure_s
            if gain_measurement.nonlinear:
                reason = describe_nonlinear_group(
                    gain_measurement,
                    reference_exposure,
                    get_configuration().get_exposure_group_gain_tolerance(),
                )
            elif gain_measurement.gain_rejected:
                reason = describe_unbelievable_gain_group(gain_measurement, reference_exposure)
            else:
                reason = describe_bright_sky_group(gain_measurement, reference_exposure)
            result.left_out_reason = reason
            logger.warning("The %g s exposure group of '%s' %s.", result.exposure_s, target_id, reason)


def _write_combined_stack(
    results: list[GroupResult], measurements: _GroupStackMeasurements, output_file: str, is_spectral: bool
) -> tuple[str, Any, float | None]:
    """Combine the used groups and write the combined files.

    Writes the combined image, the merged rejection map and the merged
    registration sequence, then moves each group's own stack into the
    ``groups`` folder and removes what else its run wrote. Sets
    ``gain_applied`` and ``kept_path`` of the groups.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups; updated in place.
    measurements : `_GroupStackMeasurements`
        The measurements that belong to all the groups.
    output_file : `str`
        The name of the combined stack.
    is_spectral : `bool`
        Whether the frames are spectral.

    Returns
    -------
    final_path : `str`
        Where the combined image was written.
    combination : `ExposureGroupCombination`
        The result of the combine.
    stacked_fwhm : `float` or `None`
        The star width of the combined image (`None` for spectra or when it
        could not be measured).
    """
    from astrometricslib._internal.drivers.fits_access import read_header, write_image
    from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import groups_directory
    from astrometricslib.pipelines.stacking.processing.group_combination import combine_exposure_group_images
    from astrometricslib.pipelines.stacking.processing.group_file_merging import (
        merge_registration_sequences,
        merge_rejection_maps,
    )

    used = used_groups(results)
    combination = combine_exposure_group_images(
        [result.aligned_image for result in used],
        [result.exposure_s for result in used],
        [result.count for result in used],
        frame_noises=[result.frame_noise for result in used],
        frame_zero_fractions=[result.frame_zero_fraction for result in used],
        covered_masks=[result.covered_mask for result in used],
        clip_ceiling_adu=measurements.clip_ceiling_adu,
        use_star_fluxes=not is_spectral,
    )
    combined = combination.image

    stacked_fwhm = None
    if not is_spectral:
        stacked_fwhm = _measure_combined_fwhm(
            combined, [result.aligned_image for result in used], measurements.clip_ceiling_adu
        )

    directory = os.path.dirname(results[0].path)
    final_path = os.path.join(directory, output_file)
    # The header comes from the heaviest group, the one every other group was
    # lined up with and whose frame is the registration reference.
    heaviest = used[combination.reference_index]
    header = read_header(heaviest.path)
    header["EXPTIME"] = float(sum(result.count * result.exposure_s for result in used))
    header["STACKCNT"] = int(sum(result.count for result in used))
    header["HISTORY"] = "Combined from exposure groups: " + ", ".join(
        f"{result.count} x {result.exposure_s:g} s" for result in used
    )
    write_image(final_path, combined, header)

    stem = os.path.splitext(final_path)[0]
    merge_rejection_maps(
        [_sibling_paths(result.path)[1] for result in used],
        [result.count for result in used],
        f"{stem}_RejMap.fits",
        alignments=[result.alignment for result in used],
    )
    merge_registration_sequences(
        [_sibling_paths(result.path)[2] for result in used], f"{stem}_Registration.seq"
    )

    # Keep each group's own stack in the groups folder; remove the rest of what
    # its run wrote next to the combined stack.
    kept_directory = groups_directory(final_path)
    for result in results:
        destination = os.path.join(kept_directory, os.path.basename(result.path))
        shutil.move(result.path, destination)
        result.kept_path = destination
        for leftover in _sibling_paths(result.path)[1:]:
            if os.path.exists(leftover) and leftover != final_path:
                os.remove(leftover)

    # The gain the combination applied to each group it measured one for.
    for position, result in enumerate(used):
        if combination.gains_measured[position]:
            result.gain_applied = combination.gains[position]
    return final_path, combination, stacked_fwhm


def _alignment_details(alignment: Any) -> dict[str, Any]:
    """Describe how one group was lined up, for the group manifest.

    Every field says what it measures. ``alignment_fit_residual_pixels`` and
    ``alignment_matched_stars`` come from Siril's plate solve of the group
    stack, so they count the stars matched to the Gaia catalog and say how far
    those stars lie from their catalog positions; they are not pairs between
    two stacks. A group that was lined up another way (``zero_order`` or
    ``correlation``) has none of these.

    Parameters
    ----------
    alignment : `AlignmentResult` or `None`
        The group's alignment, or `None` if it has none.

    Returns
    -------
    details : `dict`
        The ``alignment_*`` fields. All are `None` without an alignment.
    """
    if alignment is None:
        return {
            "alignment_method": None,
            "alignment_correlation": None,
            "alignment_rotation_degrees": None,
            "alignment_scale": None,
            "alignment_stars_found": None,
            "alignment_matched_stars": None,
            "alignment_fit_residual_pixels": None,
            "alignment_fit_residual_arcsec": None,
            "alignment_fit_order": None,
            "alignment_failure_reason": None,
            "alignment_siril_reason": None,
        }
    return {
        "alignment_method": alignment.method,
        "alignment_correlation": alignment.correlation if alignment.method == "correlation" else None,
        "alignment_rotation_degrees": alignment.rotation_degrees if alignment.method == "siril" else None,
        "alignment_scale": alignment.scale if alignment.method == "siril" else None,
        "alignment_stars_found": alignment.stars_found,
        "alignment_matched_stars": alignment.matched_stars,
        "alignment_fit_residual_pixels": alignment.fit_residual_pixels,
        "alignment_fit_residual_arcsec": alignment.fit_residual_arcsec,
        "alignment_fit_order": alignment.fit_order,
        "alignment_failure_reason": alignment.failure_reason,
        "alignment_siril_reason": alignment.siril_reason,
    }


def _describe_groups(
    results: list[GroupResult],
    failed_groups: list[tuple[Any, dict[str, Any]]],
    final_path: str,
    target_id: str,
) -> list[dict[str, Any]]:
    """Describe every group, including any not stacked or not lined up.

    Writes the group manifest next to the combined stack.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups.
    failed_groups : `list` [`tuple`]
        The group and the engine's diagnostics for each group that did not
        stack.
    final_path : `str`
        Where the combined image was written.
    target_id : `str`
        The target's name, for the log.

    Returns
    -------
    summaries : `list` [`dict`]
        One summary per group, stacked groups first.
    """
    from astrometricslib.pipelines.shared.quality.clipped_frames import CLIPPED_FRAME_ZERO_FRACTION
    from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import (
        build_group_summary,
        measure_group_saturation,
        write_group_manifest,
    )

    clipped_frame_zero_fraction = resolve(CLIPPED_FRAME_ZERO_FRACTION).value

    summaries, details = [], []
    for result in results:
        try:
            saturation = measure_group_saturation(result.group.frames)
        except (*FITS_READ_ERRORS, *DATA_ERRORS) as measurement_error:
            # A measurement must not cost the stack.
            logger.warning("Could not measure saturation for '%s': %s", target_id, measurement_error)
            saturation = []
        alignment = result.alignment
        moved = alignment is not None and not alignment.is_reference and alignment.failure_reason is None
        summaries.append(
            build_group_summary(
                result.exposure_s,
                result.group.frames,
                result.diagnostics,
                saturation,
                stack_path=result.kept_path,
                clipped_at_zero=result.frame_zero_fraction > clipped_frame_zero_fraction,
                alignment_shift_pixels=[
                    round(alignment.shift_rows_pixels, 3),
                    round(alignment.shift_columns_pixels, 3),
                ]
                if moved
                else None,
                left_out_reason=result.left_out_reason,
                gain_measurement=result.gain_measurement,
                gain_applied=result.gain_applied,
            )
        )
        details.append({
            "weight": result.weight,
            "frame_noise_counts": result.frame_noise,
            "frame_zero_fraction": result.frame_zero_fraction,
            **_alignment_details(alignment),
        })
    for group, diagnostics in failed_groups:
        summaries.append(
            build_group_summary(
                group.exposure_s,
                group.frames,
                diagnostics,
                [],
                left_out_reason="could not be stacked",
            )
        )
        details.append({})
    write_group_manifest(final_path, summaries, details)
    return summaries


def _join_group_diagnostics(
    results: list[GroupResult],
    summaries: list[dict[str, Any]],
    measurements: _GroupStackMeasurements,
    combination: Any,
    stacked_fwhm: float | None,
    stack_weight: str | None,
) -> dict[str, Any]:
    """Join the used groups' diagnostics and add what the combine found.

    Parameters
    ----------
    results : `list` [`GroupResult`]
        The stacked groups.
    summaries : `list` [`dict`]
        The summaries from `_describe_groups`.
    measurements : `_GroupStackMeasurements`
        The measurements that belong to all the groups.
    combination : `ExposureGroupCombination`
        The result of the combine.
    stacked_fwhm : `float` or `None`
        The star width of the combined image.
    stack_weight : `str` or `None`
        The Siril ``-weight`` mode the groups were stacked with.

    Returns
    -------
    merged_diagnostics : `dict`
        The diagnostics described in `_stack_exposure_groups`.
    """
    import numpy as np

    used = used_groups(results)
    merged_diagnostics = _merge_diagnostics([
        (result.group, result.path, result.diagnostics) for result in used
    ])
    merged_diagnostics["clipped_exposure_groups"] = measurements.clipped_groups
    merged_diagnostics["exposure_group_frame_zero_fractions"] = measurements.measured_zero_fractions
    merged_diagnostics["exposure_group_summaries"] = summaries
    # What each combined group counted for, so the quality record can give
    # every frame its share (the weight is per pixel in the combine only where
    # a group is saturated or empty).
    merged_diagnostics["exposure_group_weights"] = _exposure_group_weight_entries(
        [result.group for result in used],
        [result.artifacts for result in used],
        [result.weight for result in used],
        [result.count for result in used],
        [result.exposure_s for result in used],
        [result.frame_noise if measurements.frame_noises_read else None for result in used],
        combination,
        stack_weight,
        [result.summary_index for result in used],
    )
    # How each group's stack was moved onto the reference group's grid, with
    # the number of frames the group registered, so the crop can count how many
    # frames cover each pixel of the combined stack (see `stack_crop`).
    merged_diagnostics["exposure_group_alignments"] = [
        {
            "frame_count": len(result.artifacts.get("registration_frames") or []),
            "alignment": None
            if result.alignment is None or result.aligned_image is None or result.alignment.method == "none"
            else result.alignment.position_map_for(result.aligned_image.shape).to_record(),
        }
        for result in used
    ]
    # Where every combined group has data. Empty-sky apertures keep out of the
    # rest (see `aperture_placement.uncovered_clearance_px()`).
    merged_diagnostics["covered_in_every_group"] = np.logical_and.reduce(combination.covered_masks)
    if stacked_fwhm is not None:
        merged_diagnostics["stacked_fwhm_px"] = stacked_fwhm
    return merged_diagnostics


def _read_group_artifacts(
    engine: StackingDriver, group_stack_path: str, group_diagnostics: dict[str, Any]
) -> dict[str, Any]:
    """Read the registration facts the engine saved next to one group's stack.

    Parameters
    ----------
    engine : `StackingDriver`
        The stacking engine.
    group_stack_path : `str`
        One group's stack, whose registration sequence is beside it.
    group_diagnostics : `dict`
        What the engine reported for the group's run. ``symlinked_light_paths``
        names the frames in the order of the registration facts.

    Returns
    -------
    artifacts : `dict`
        What `StackingDriver.read_stack_artifacts` gave, with the group's
        ``symlinked_light_paths``. The registration lists are missing when the
        files cannot be read. A missing reading never costs the stack; the
        group's frame weights are then unknown.
    """
    artifacts: dict[str, Any] = {}
    try:
        artifacts = dict(engine.read_stack_artifacts(group_stack_path))
    except (OSError, *DATA_ERRORS) as read_error:
        logger.warning("Could not read the registration facts of '%s': %s", group_stack_path, read_error)
    artifacts["symlinked_light_paths"] = list(group_diagnostics.get("symlinked_light_paths") or [])
    return artifacts


def _exposure_group_weight_entries(
    groups: list[Any],
    artifacts: list[dict[str, Any]],
    weights: list[float],
    counts: list[int],
    exposures: list[float],
    frame_noises: list[float | None],
    combination: Any,
    stack_weight: str | None,
    summary_indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Describe what each combined group counted for in the stack.

    Every list has one item per combined group, in the order the groups were
    combined. The entries keep the group's own frame lists, registration facts
    and inclusion flags, so the frame weights can be worked out per group (see
    `stack_membership.group_frame_weights`). The merged diagnostics keep their
    joined lists as well; these entries do not replace them.

    Parameters
    ----------
    groups : `list`
        The combined `ExposureGroup` objects.
    artifacts : `list` [`dict`]
        What the engine read next to each group's stack
        (``"registration_frames"`` and ``"registration_included"``).
    weights : `list` [`float`]
        The combine weight of each group, ``N t^2 / s^2``.
    counts : `list` [`int`]
        The number of frames stacked in each group (``N``).
    exposures : `list` [`float`]
        The exposure length of each group in seconds (``t``).
    frame_noises : `list` [`float` or `None`]
        The noise of one raw frame of each group in counts (``s``), or `None`
        when it could not be read.
    combination : `ExposureGroupCombination`
        The result of the combine, for each group's applied gain, whether it
        was measured, and its sky level.
    stack_weight : `str` or `None`
        The Siril ``-weight`` mode the groups were stacked with.
    summary_indices : `list` [`int`], optional
        The place of each group in the ``"exposure_group_summaries"`` list,
        where the group's own quality rows are filed. Left out, the groups are
        taken to be its first entries in order.

    Returns
    -------
    entries : `list` [`dict`]
        One entry per group with ``summary_index`` (the group's place in the
        summaries), ``paths`` (every frame of the group),
        ``frame_paths``, ``registration_frames`` and ``registration_included``
        (Siril's lists for the group, in the engine's order), ``weight``
        (``W_g``), ``frame_count`` (``N_g``), ``exposure_s`` (``t_g``),
        ``frame_noise_counts`` (``s_g`` or `None`), ``gain`` (the applied
        ``G_g``), ``gain_measured``, ``sky_level_per_second``,
        ``stack_weight`` and ``holds_folded_frames``.
    """
    from astrometricslib.pipelines.stacking.processing.exposure_groups import (
        exposure_group_holds_folded_frames,
    )

    entries = []
    for position, group in enumerate(groups):
        entries.append({
            "summary_index": position if summary_indices is None else int(summary_indices[position]),
            "paths": [
                str(path)
                for frame in group.frames
                if (path := frame.get("path") if isinstance(frame, dict) else getattr(frame, "path", None))
            ],
            "frame_paths": list(artifacts[position].get("symlinked_light_paths") or []),
            "registration_frames": list(artifacts[position].get("registration_frames") or []),
            "registration_included": list(artifacts[position].get("registration_included") or []),
            "weight": float(weights[position]),
            "frame_count": int(counts[position]),
            "exposure_s": float(exposures[position]),
            "frame_noise_counts": None if frame_noises[position] is None else float(frame_noises[position]),
            "gain": float(combination.gains[position]),
            "gain_measured": bool(combination.gains_measured[position]),
            "sky_level_per_second": float(combination.sky_levels[position]),
            "stack_weight": stack_weight,
            "holds_folded_frames": exposure_group_holds_folded_frames(group.frames),
        })
    return entries


def _measure_combined_fwhm(
    combined: Any, used_images: list[Any], clip_ceiling_adu: float | None = None
) -> float | None:
    """Measure the star width of a combined stack, away from patched cores.

    Where a group's stack is saturated, the combined image takes another
    group's data, so a bright star's core is patched and the combined image has
    no saturated plateau left for the usual check to skip. Measured as it
    stands, the brightest stars are those patched cores and read too wide (3.40
    px against 2.38 px for the rest, on M 57). This measures with those stars
    left out (see `measure_fwhm_from_data`).

    Parameters
    ----------
    combined : `numpy.ndarray`
        The combined image.
    used_images : `list` [`numpy.ndarray`]
        The group stacks that went into it, as they were given to the combine.
    clip_ceiling_adu : `float`, optional
        The camera's clip ceiling, as given to the combine.

    Returns
    -------
    fwhm : `float` or `None`
        The median star width in pixels, or `None` if it could not be
        measured. A failed measurement never costs the stack.
    """
    import numpy as np

    from astrometricslib._internal.drivers.fits_access import collapse_to_2d
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
    from astrometricslib.pipelines.stacking.processing.group_gains import saturated_pixel_mask

    try:
        plane = collapse_to_2d(np.asarray(combined, dtype=float))
        fwhm = measure_fwhm_from_data(
            plane, excluded_mask=saturated_pixel_mask(used_images, clip_ceiling_adu=clip_ceiling_adu)
        )
    except DATA_ERRORS as measurement_error:
        logger.warning("Could not measure the star width of the combined stack: %s", measurement_error)
        return None
    record_star_width(fwhm, StarWidthEstimator.STACK_GAUSSIAN)
    return None if fwhm is None else float(fwhm)


def _merge_diagnostics(results: list[tuple[Any, str, dict[str, Any]]]) -> dict[str, Any]:
    """Join the diagnostics of several exposure-group runs.

    The per-frame lists are joined in group order, which is also the order of
    the merged registration sequence, so the two stay lined up.

    Returns
    -------
    merged : `dict`
        The first group's diagnostics with the per-frame lists joined, the
        durations added, the ``"combined_frame_paths"`` of the groups given,
        and an ``"exposure_groups"`` entry per group.
    """
    merged = dict(results[0][2])
    for key in (
        "corrupt_frames_skipped",
        "calibration_mismatch_flags",
        "calibration_blocking_flags",
        "calibration_match_blocking_flags",
        "symlinked_light_paths",
        "zero_order_stars",
    ):
        merged[key] = [item for _, _, diagnostics in results for item in diagnostics.get(key, [])]
    # The calibration files every group applied, each once: groups of other
    # exposure lengths use other darks, and the quality step must see them all.
    for key in (FLAT_FRAME_PATHS_KEY, BIAS_FRAME_PATHS_KEY, FLAT_BIAS_FRAME_PATHS_KEY, DARK_FRAME_PATHS_KEY):
        joined = [item for _, _, diagnostics in results for item in diagnostics.get(key, [])]
        if joined:
            merged[key] = list(dict.fromkeys(joined))
    durations = [diagnostics.get("stacking_duration_s") for _, _, diagnostics in results]
    if any(duration is not None for duration in durations):
        merged["stacking_duration_s"] = float(sum(duration or 0.0 for duration in durations))
    # Every count below describes a whole session, so add up all the groups
    # (the copy of the first group would report only its own frames).
    for key in ("images_stacked", "num_lights", "registered_frames", "registration_failed_frames"):
        if any(diagnostics.get(key) is not None for _, _, diagnostics in results):
            merged[key] = sum(int(diagnostics.get(key) or 0) for _, _, diagnostics in results)
    # Siril's negative-pixel warnings: the worst share over all groups, and how
    # many frames were warned about in total.
    warned = [d for _, _, d in results if d.get("negative_pixel_max_pct") is not None]
    if warned:
        merged["negative_pixel_max_pct"] = max(d["negative_pixel_max_pct"] for d in warned)
        merged["negative_pixel_frames"] = sum(int(d.get("negative_pixel_frames") or 0) for d in warned)
    merged["registered_fraction"] = _registered_fraction(merged)
    # The frames that really went into the combined stack, so the quality
    # summary counts and samples only these and not the groups left out.
    merged["combined_frame_paths"] = [
        str(path)
        for group, _, _ in results
        for frame in group.frames
        if (path := frame.get("path") if isinstance(frame, dict) else getattr(frame, "path", None))
    ]
    merged["exposure_groups"] = [
        {
            "exposure_s": group.exposure_s,
            "frames": len(group.frames),
            "images_stacked": diagnostics.get("images_stacked"),
            "registered_fraction": diagnostics.get("registered_fraction"),
            "spectral_star_detection": diagnostics.get("spectral_star_detection"),
        }
        for group, _, diagnostics in results
    ]
    return merged
