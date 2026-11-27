"""The stacking methods of the processing sub-API (`ProcessingPipelines`).

These methods stack a target's frames, predict a stack and its frame
weights, measure a stack's noise, read a stack's summary, find a stack by
its camera and optics, and open or stop the stacking programs. They are part
of `ProcessingPipelines` (in `processing.py`), which binds them as its methods.
"""

import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal

# `ProcessingPipelines` binds the functions below as its methods, so this
# module and `processing.py` import each other. An annotation is read
# only when asked for, after both modules have loaded.
from astrometricslib.api import processing as _processing
from astrometricslib.foundation.arguments import (
    TimeInput,
    check_choice,
    check_include,
    reject_unused_arguments,
)
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, current_job_id, registered_job
from astrometricslib.models.processing_results import (
    StackPrediction,
    StackResult,
    StackWeightPrediction,
)
from astrometricslib.models.quality.quality_reports import (
    StackSummary,
)
from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
from astrometricslib.models.quality.subset_stacks import (
    NightContributionResult,
    NoiseGrowthResult,
    OddEvenDifferenceNoiseResult,
)
from astrometricslib.models.target import FrameRecord, StackConfigurationResult, Target
from astrometricslib.pipelines.shared.analysis_configuration import (
    choose_stack_configuration,
)
from astrometricslib.pipelines.shared.api_arguments import STACK_KINDS, StackKind, make_frame_selection
from astrometricslib.pipelines.shared.frame_choice import FrameChoice, check_frame_list, frames_from_choice
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.target_records import save_quality_stamps

logger = logging.getLogger(__name__)


NOISE_KINDS = ("growth", "night_contributions", "odd_even_difference")


NOISE_SECTIONS = ("night_contributions", "odd_even_difference")


_NOISE_ARGUMENTS = {
    "growth": ("maximum_steps", "include"),
    "night_contributions": (),
    "odd_even_difference": ("keep_halves",),
}


@background_job("stacking", grace_period_seconds=8.0)
def stack(
    self: _processing.ProcessingPipelines,
    target: str | Target,
    frames: FrameChoice | None = None,
    filter_name: str | None = None,
    first_file: str | None = None,
    last_file: str | None = None,
    since: TimeInput | None = None,
    until: TimeInput | None = None,
    kind: StackKind = "imaging",
    camera_id: str | None = None,
    focal_length_mm: float | None = None,
    force: bool = False,
    denoise: bool | None = None,
    denoise_strength: float | None = None,
    star_toning: bool | None = None,
    rejection_sigma: tuple[float, float] | None = None,
    filter_wfwhm: str | None = None,
    filter_round: str | None = None,
    stack_weight: str | None = None,
    generate_rejmap: bool | None = None,
    output_file: str | None = None,
    log_file: str | None = None,
) -> StackResult:
    """Stack a target's light frames, choosing which frames go in.

    This runs the same stacking stage as the Stack button in the app:
    it sets aside bad frames, stacks, trims the noisy edges, records the
    quality summary and the preview, and saves the target. Imaging and
    spectroscopy frames are never mixed, so ``kind`` picks one, and
    frames from two cameras, or from two optics (focal lengths), are
    never stacked together. An unchanged
    stack is kept unless ``force`` is set. A target, camera or frame
    choice that matches nothing raises `NotFoundError`; a stacking stage
    that finishes without making a stack raises `ProcessingError`.

    Parameters
    ----------
    target : `str` or `Target`
        The target to stack.
    frames : `FrameChoice`, optional
        The exact frames to stack: the `Page` a frame query
        returns, a list of frame records, or a list of the
        target's frame paths. A page cut short (one with a
        ``next_offset``) is refused. Cannot be combined with the frame
        choices below.
    filter_name : `str`, optional
        Only frames of this filter, such as ``"L"`` or ``"Luminance"``.
    first_file : `str`, optional
        Only frames from this file onward. A bare number such as
        ``"013"`` means frame 013.
    last_file : `str`, optional
        Only frames up to this file or number.
    since : `str`, `datetime` or `Time`, optional
        Only frames taken at or after this time (UTC if no offset).
    until : `str`, `datetime` or `Time`, optional
        Only frames taken at or before this time.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"`` frames.
    camera_id : `str`, optional
        Only frames from this camera, such as ``"ASI 533MM"``. Chosen
        before any other frame check. A target with frames from more
        than one camera needs this; without it the answer is an error
        that names the cameras and how many frames each took.
    focal_length_mm : `float`, optional
        Only frames taken at this focal length, such as ``405``, rounded
        to whole millimeters. Chosen with ``camera_id``, before any other
        frame check. A target with frames from more than one optic needs
        this; without it the answer is an error that names the focal
        lengths and how many frames each has.
    force : `bool`, optional
        Rebuild even if nothing changed since the stack on disk.
    denoise : `bool`, optional
        For this run's preview picture: `False` skips Cosmic Clarity,
        `True` runs it even if the settings have it switched off. Left
        out, the settings apply. Never written to the settings.
    denoise_strength : `float`, optional
        Cosmic Clarity's denoise strength for this run, from 0 to 1.
    star_toning : `bool`, optional
        Turn the preview's star toning on or off for this run.
    rejection_sigma : `tuple` [`float`, `float`], optional
        Low and high sigma-clipping bounds for pixel rejection.
    filter_wfwhm : `str`, optional
        Siril's weighted-FWHM frame filter.
    filter_round : `str`, optional
        Siril's star-roundness frame filter.
    stack_weight : `str`, optional
        How Siril weights each frame.
    generate_rejmap : `bool`, optional
        Also write the rejection maps.
    output_file : `str`, optional
        Where to write the stack instead of the usual place.
    log_file : `str`, optional
        Where to write Siril's log.

    Returns
    -------
    result : `StackResult`
        The frames chosen, the stack's path and its quality summary
        (``quality_summary``). To see the frame choice, or what the stack
        would set aside, without stacking, use `predict_stack`. To compare
        the stack with the previous one, use
        `Astrometrics.quality.measure_stack`.

    Raises
    ------
    InvalidArgumentError
        If ``kind`` or ``denoise_strength`` is out of range, a time
        cannot be read, ``frames`` is combined with a frame choice, the
        frames come from more than one camera or optic, or ``frames``
        mixes imaging and spectral frames (the message gives the counts;
        nothing is stacked).
    """
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording
    from astrometricslib.pipelines.stacking.post_processing.preview_settings import PreviewSettings
    from astrometricslib.pipelines.stacking.stack_request import SirilOptions, run_stack

    check_choice("kind", kind, STACK_KINDS)
    if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
        raise InvalidArgumentError("denoise_strength must be between 0 and 1.")
    resolved = self._resolve(target)
    chosen = self._choose_frames(
        resolved,
        frames,
        kind,
        filter_name,
        first_file,
        last_file,
        since,
        until,
        camera_id,
        focal_length_mm,
    )
    return run_stack(
        resolved,
        chosen,
        kind=kind,
        force=force,
        preview_settings=PreviewSettings(
            denoise=denoise, denoise_strength=denoise_strength, star_toning=star_toning
        ),
        siril=SirilOptions(
            rejection_sigma=rejection_sigma,
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
            stack_weight=stack_weight,
            generate_rejmap=generate_rejmap,
            output_file=output_file,
            log_file=log_file,
        ),
        stacking_slot=self.acquire_job_slot,
        save_targets=self._targets._save,
        drivers=self._drivers,
        measured_at=datetime.now(UTC),
        quality_recording=FrameQualityRecording(
            self._config,
            self._calibration._calibration_library,
            save_target=lambda: save_quality_stamps(self._targets, resolved),
        ),
    )


@background_job("stacking", grace_period_seconds=8.0)
def _choose_frames(
    self: _processing.ProcessingPipelines,
    target: Target,
    frames: FrameChoice | None,
    kind: StackKind,
    filter_name: str | None,
    first_file: str | None,
    last_file: str | None,
    since: TimeInput | None,
    until: TimeInput | None,
    camera_id: str | None,
    focal_length_mm: float | None,
) -> list[FrameRecord]:
    """Choose the frames of a stack, from an exact list or from choices.

    `stack` and `predict_stack` both use this, so a prediction is made
    for the frames the stack would be given.

    Parameters
    ----------
    target : `Target`
        The target.
    frames : `FrameChoice` or `None`
        The exact frames (a page, records or paths; see
        `frames_from_choice`), or `None` to choose them with the rest.
    kind : `str`
        ``"imaging"`` or ``"spectral"``.
    filter_name : `str` or `None`
        Only frames of this filter.
    first_file : `str` or `None`
        Only frames from this file onward.
    last_file : `str` or `None`
        Only frames up to this file or number.
    since : `str`, `datetime`, `Time` or `None`
        Only frames taken at or after this time.
    until : `str`, `datetime`, `Time` or `None`
        Only frames taken at or before this time.
    camera_id : `str` or `None`
        Only frames from this camera.
    focal_length_mm : `float` or `None`
        Only frames taken at this focal length.

    Returns
    -------
    chosen : `list` [`FrameRecord`]
        The frames, from one camera and one optic.

    Raises
    ------
    InvalidArgumentError
        If ``frames`` is combined with a choice or is empty, the
        frames come from more than one camera or optic, or they mix
        imaging and spectral frames. The message gives the counts.
    """
    from astrometricslib.pipelines.stacking.stack_request import choose_frames_to_stack

    if frames is not None:
        choices = (filter_name, first_file, last_file, since, until, camera_id, focal_length_mm)
        if any(value is not None for value in choices):
            raise InvalidArgumentError(
                "Pass frames, or choose them with filter_name, first_file, last_file, since, until, "
                "camera_id and focal_length_mm, not both."
            )
        chosen = frames_from_choice(frames, target)
        check_frame_list(chosen, target_id=target.id)
        return chosen
    selection = make_frame_selection(filter_name, first_file, last_file, since, until, kind == "spectral")
    return choose_frames_to_stack(target, kind, selection, camera_id, focal_length_mm)


def predict_stack(
    self: _processing.ProcessingPipelines,
    target: str | Target,
    frames: FrameChoice | None = None,
    filter_name: str | None = None,
    first_file: str | None = None,
    last_file: str | None = None,
    since: TimeInput | None = None,
    until: TimeInput | None = None,
    kind: StackKind = "imaging",
    camera_id: str | None = None,
    focal_length_mm: float | None = None,
    checks: bool = True,
) -> StackPrediction:
    """Say what a stack would do with its frames, without stacking.

    The frames are chosen as in `stack`. Then the same frame checks the
    stack runs decide which frames it would set aside: clouds, trailed
    stars, a frame off the field, a minority gain setting, a different
    sky background, and frames an earlier run already set aside. A frame
    with an uneven sky but normal stars is kept and listed as flagged.
    For each exposure group of the frames that remain, the same
    calibration choice the stack makes names the dark, bias and flat
    frames it would use, with the library's flags for a near match.
    Nothing is stacked, moved, stored or saved, so the answer matches the
    stack as long as the frames, the settings and the calibration library
    stay the same. The checks read every frame, so a large target takes
    a while. With ``checks=False`` only the frames are chosen, which is
    quick and reads no frame: use it to see which frames a stack would get,
    or to have a frame choice refused before a stack starts.

    Parameters
    ----------
    target : `str` or `Target`
        The target to check.
    frames : `FrameChoice`, optional
        The exact frames to check, in any form `stack` takes. Cannot be
        combined with the frame choices below.
    filter_name : `str`, optional
        Only frames of this filter.
    first_file : `str`, optional
        Only frames from this file onward.
    last_file : `str`, optional
        Only frames up to this file or number.
    since : `str`, `datetime` or `Time`, optional
        Only frames taken at or after this time.
    until : `str`, `datetime` or `Time`, optional
        Only frames taken at or before this time.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"`` frames.
    camera_id : `str`, optional
        Only frames from this camera.
    focal_length_mm : `float`, optional
        Only frames taken at this focal length.
    checks : `bool`, optional
        Run the frame checks and the calibration choice. Defaults to `True`.
        With `False`, the answer has only the frame choice (``checks_run``
        is `False`).

    Returns
    -------
    prediction : `StackPrediction`
        The counts, the first and last chosen file, the frames set aside
        and flagged with their reasons, each group's calibration choice,
        and the checks' warnings.

    Notes
    -----
    The errors are those of `stack`: `InvalidArgumentError` for a kind
    out of range, a time that cannot be read, ``frames`` combined with a
    frame choice, or frames from more than one camera or optic, and
    `NotFoundError` when the target, camera or frame choice matches
    nothing.
    """
    from astrometricslib.pipelines.stacking.stack_prediction import list_chosen_frames, predict_stack

    check_choice("kind", kind, STACK_KINDS)
    resolved = self._resolve(target)
    chosen = self._choose_frames(
        resolved,
        frames,
        kind,
        filter_name,
        first_file,
        last_file,
        since,
        until,
        camera_id,
        focal_length_mm,
    )
    if not checks:
        return list_chosen_frames(resolved, chosen, kind)
    # A prediction is a read, not a pipeline run, so it records no job.
    return predict_stack(resolved, chosen, kind, self._calibration._calibration_library)


def measure_noise(
    self: _processing.ProcessingPipelines,
    target: str | Target,
    kind: Literal["growth", "night_contributions", "odd_even_difference"] = "growth",
    *,
    maximum_steps: int | None = None,
    include: list[str] | None = None,
    keep_halves: bool = False,
) -> NoiseGrowthResult | NightContributionResult | OddEvenDifferenceNoiseResult:
    """Stack parts of a target's library stack and measure the noise.

    Each kind stacks some of the library stack's frames (read from the
    stack's ``_inputs.json``) into the target's ``_subsets`` folder with
    the normal settings (without the frame quarantine step), and measures
    the noise in the same sky apertures as the library stack, so the
    numbers compare. Rows are stored at level ``stack``. The library
    stack, its previews and its record are never changed. None of the
    kinds starts by itself after a stack, and none runs inside the stack's
    own process; a caller starts it. The target must already have the
    noise apertures that its library stack stored, so stack it once first
    if it was stacked before they were recorded. A target with no stack,
    or a stack with no ``_inputs.json``, raises `NotFoundError`.

    Arguments used by each kind (any other argument is refused):

    - ``"growth"``: ``maximum_steps`` and ``include``; ``include`` may
      hold ``"night_contributions"`` and ``"odd_even_difference"``.
    - ``"night_contributions"``: none.
    - ``"odd_even_difference"``: ``keep_halves``.

    Parameters
    ----------
    target : `str` or `Target`
        The target.
    kind : `str`, optional
        ``"growth"`` (default): the noise against the number of frames.
        The frames are split by observing night. The subsets are the first
        night, the first two nights, and so on, up to one night fewer than
        the stack. A stack of one night uses the first quarter, half and
        three quarters of its frames instead. At most ``maximum_steps``
        subsets are made, spaced evenly through the possible ones, and a
        subset with fewer than four frames is left out. The library stack
        is the last point of the curve and is not stacked again. The
        noise, the reference flux, the effective frame count and the
        effective time are stored with the subset's label in the subject.
        The cost is one stack for each subset, four by default, each with
        fewer frames than the library stack, plus the odd–even difference
        noise (about one library stack's time and Siril's temporary disk).
        Expect the time of about two to four library stacks (a design
        estimate, not a measurement).

        ``"night_contributions"``: what each observing night gave to the
        library stack. For every night, the stack's frames without that
        night are stacked, and four numbers are stored on that leave-out
        stack, with the night in the subject's observing night: the
        night's share of the library stack's total frame weight
        (``stacking.night_weight_fraction``), the noise of the stack
        without the night (``stacking.noise_without_night``), the paired
        difference from the library stack's noise
        (``stacking.noise_difference_without_night``) and the ratio the
        two stacks' effective frame counts predict
        (``stacking.subset_expected_noise_ratio``). The cost is one stack
        for every night, each close to the library stack's own cost. A
        stack of one night has nothing to leave out. A share is stored as
        not measured when the stack's weights are unknown (Siril weighted
        the frames itself).

        ``"odd_even_difference"``: the library stack's random noise from
        two halves. The frames are put in order of capture time and split
        into the 1st, 3rd, ... and the 2nd, 4th, ... frame. The library
        stack's registration reference frame goes into both, and it is
        handed to the stacker as the frame to line up with, so a half
        cannot choose its own and the two half stacks have the same pixel
        grid. Each half is stacked (without the edge trim), the
        difference is taken, and its noise is measured in the library
        stack's stored empty apertures. The difference cancels what the
        frames share (sky structure, faint stars, nebula) and keeps their
        random noise, so it is the more honest noise of a deep or crowded
        field. Nine rows are stored on the library stack, beside its
        ``stacking.empty_aperture_noise_*`` rows and never replacing
        them: the noise (``..._stack_unit`` and ``..._normalized``), the
        reference flux the normalized value divides by, the two halves'
        effective frame counts, the variance factor, the variance ratio
        with each frame's modeled noise, and the shared frame's two
        weight shares (all ``stacking.odd_even_difference_*``). Five more
        rows say what the two halves share at the scale of a few pixels
        (an upper limit of a fixed pattern on the sensor) and how that
        differs along and across the pointing drift (the
        ``stacking.half_shared_fine_scale_*`` and
        ``stacking.walking_noise_*`` metrics). The halves are deleted
        afterwards. The cost is two stacks of half the frames each, about
        one library stack's time in all, plus the temporary disk space
        Siril needs for each. While it runs, no library stack and no
        other subset job of the target can run. When the measurement
        cannot be made (a half with under 8 frames, no recorded
        registration reference, other apertures than the library stack's,
        a Siril failure), the rows are stored as not measured with the
        reason, and the result says why.
    maximum_steps : `int`, optional
        Applies to ``"growth"`` only. The most subsets to stack. Defaults
        to the registered ``stacking.default_growth_steps`` (4).
    include : `list` [`str`], optional
        Applies to ``"growth"`` only. Sections to run in the same job,
        under the same lock, after the series: ``"night_contributions"``
        (one more stack for every night) and ``"odd_even_difference"``
        (about one library stack's time, and the noise measure that
        ignores structure the frames share; its result is in
        ``odd_even_difference`` and its rows are counted there). `None`
        means ``["odd_even_difference"]``; pass an empty list for neither.
    keep_halves : `bool`, optional
        Applies to ``"odd_even_difference"`` only. Keep the two half
        stacks in the ``_subsets`` folder. Defaults to `False`.

    Returns
    -------
    result : `NoiseGrowthResult`, `NightContributionResult`, ...
        For ``"growth"`` a `NoiseGrowthResult` (the subsets, smallest
        first, with notes about steps left out); for
        ``"night_contributions"`` a `NightContributionResult` (one entry
        for each night); for ``"odd_even_difference"`` an
        `OddEvenDifferenceNoiseResult` (the difference noise, the halves'
        effective frame counts and the library stack's own noise that the
        difference implies, or the reason there are none).

    Raises
    ------
    InvalidArgumentError
        If the kind or an include section is unknown, an argument the
        kind does not use is given, or ``maximum_steps`` is below 1.
    """
    from astrometricslib.pipelines.stacking.odd_even_difference_run import run_odd_even_difference_noise
    from astrometricslib.pipelines.stacking.subset_noise_run import (
        run_night_contributions,
        run_noise_growth,
    )
    from astrometricslib.pipelines.stacking.target_stack_guard import subset_job_guard

    check_choice("kind", kind, NOISE_KINDS)
    sections = check_include(include, NOISE_SECTIONS)
    reject_unused_arguments(
        kind,
        _NOISE_ARGUMENTS,
        {
            "maximum_steps": maximum_steps is not None,
            "include": bool(sections),
            "keep_halves": keep_halves,
        },
    )
    if maximum_steps is not None and maximum_steps < 1:
        raise InvalidArgumentError("maximum_steps must be at least 1.")
    if kind == "growth" and include is None:
        sections = frozenset(("odd_even_difference",))
    resolved = self._resolve(target)
    library_stack_path = self._library_stack_path(resolved, "imaging")
    with registered_job(job_type="stacking", target_id=resolved.id) as job:
        context = self._subset_context(resolved, job.job_id)
        shared = {
            "context": context,
            "configuration": self._config,
            "drivers": self._drivers,
            "stacking_slot": self.acquire_job_slot,
        }
        if kind == "night_contributions":
            return run_night_contributions(resolved, library_stack_path, **shared)
        if kind == "odd_even_difference":
            return run_odd_even_difference_noise(
                resolved, library_stack_path, keep_halves=keep_halves, **shared
            )
        with subset_job_guard(resolved.id):
            result = run_noise_growth(resolved, library_stack_path, maximum_steps=maximum_steps, **shared)
            if "night_contributions" in sections:
                result.night_contributions = run_night_contributions(resolved, library_stack_path, **shared)
            if "odd_even_difference" in sections:
                try:
                    result.odd_even_difference = run_odd_even_difference_noise(
                        resolved, library_stack_path, **shared
                    )
                except Exception as error:  # the series is already stored; report, do not lose it
                    logger.exception("The odd–even difference noise of %s failed", resolved.id)
                    reason = f"{type(error).__name__}: {error}"
                    result.notes.append(f"The odd–even difference noise was not measured ({reason}).")
            return result


def _library_stack_path(
    self: _processing.ProcessingPipelines, target: Target, kind: StackKind, *, must_exist: bool = True
) -> str:
    """Find the path of a target's library stack for the subset jobs.

    Parameters
    ----------
    target : `Target`
        The target.
    kind : {"imaging", "spectral"}
        Which stack.
    must_exist : `bool`, optional
        Raise when the target has no stack. When `False`, a target
        without a stack gets the path its stack would have, so a
        subset can still be stacked beside it.

    Returns
    -------
    path : `str`
        The library stack's path.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

    if must_exist:
        return stack_path_of(target, kind)
    stacking = target.spectral_stacking if kind == "spectral" else target.stacking
    if stacking.stacked_image:
        return str(stacking.stacked_image)
    folder = os.path.join(str(self._config.get_stacks_path()), "lights", target.id)
    tag = "_SPEC" if kind == "spectral" else ""
    return os.path.join(folder, f"{target.id.replace(' ', '_')}{tag}_Stacked.fits")


def _subset_context(
    self: _processing.ProcessingPipelines, target: Target, job_id: str | None
) -> PipelineRunContext | None:
    """Make the shared facts of one subset-stack run.

    Parameters
    ----------
    target : `Target`
        The target.
    job_id : `str` or `None`
        The run's job id, if it recorded a job.

    Returns
    -------
    context : `PipelineRunContext`
        The context, stamped with the time now (the API layer reads the
        clock, the pipelines do not).
    """
    return PipelineRunContext.from_options(
        target.id,
        "stacking",
        STACKING_PIPELINE_VERSION,
        {"job_id": job_id or current_job_id(), "measured_at": datetime.now(UTC)},
    )


def find_stack_configuration(
    self: _processing.ProcessingPipelines,
    target: str | Target,
    camera_id: str | None = None,
    focal_length_mm: float | None = None,
) -> StackConfigurationResult:
    """Find the target's one imaging stack of a camera and optic.

    A target stacked with several cameras or optics keeps one stack for
    each (``stacking.stacks_by_configuration``). This picks the one a
    camera and focal length name, the way `process_target` does: the
    camera by its full name, an alias or part of its name, the focal
    length after rounding to whole millimeters.

    Parameters
    ----------
    target : `str` or `Target`
        The target.
    camera_id : `str`, optional
        The camera. Left out, any camera.
    focal_length_mm : `float`, optional
        The optic's focal length in millimeters. Left out, any optic.

    Returns
    -------
    record : `StackConfigurationResult`
        The stack's record: ``configuration_key``, ``camera``,
        ``focal_length_mm`` and ``stacked_image``. When neither is
        given, or no stack or more than one matches,
        `InvalidArgumentError` is raised, and its message lists the
        target's configurations.
    """
    return choose_stack_configuration(self._resolve(target), camera_id, focal_length_mm, self._config)


def stack_summary(
    self: _processing.ProcessingPipelines, target: str | Target, kind: StackKind = "imaging"
) -> StackSummary:
    """Summarize a target's current stack in one short answer.

    Reads the numbers the stacking stage saved with the stack, so nothing
    is measured again. The answer covers how many frames went in and how
    many were set aside, the share of pixels rejected, the star width
    against what the input frames predict, and what happened
    to each exposure group (its frames, whether it clipped, how far it
    was moved to line up, and why it was left out if it was).

    Parameters
    ----------
    target : `str` or `Target`
        The target to summarize.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"`` stack.

    Returns
    -------
    summary : `StackSummary`
        The summary.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import summarize_stack

    check_choice("kind", kind, STACK_KINDS)
    return summarize_stack(self._resolve(target), kind)


def predict_stack_weights(
    exposure_seconds: Sequence[float | None],
    configured_weight: str | None = None,
    *,
    is_spectral: bool = False,
    star_width_px: Sequence[float | None] | None = None,
    rate_variance: Sequence[float | None] | None = None,
    group_frame_noise_adu: Mapping[float, float | None] | None = None,
) -> StackWeightPrediction:
    """Predict how the stacker would weight a set of frames.

    This runs the stacker's own rules on the frame facts you give, so a
    forecast made before stacking agrees with the stack made later.
    Nothing is read from disk or saved.

    Parameters
    ----------
    exposure_seconds : `Sequence` [`float` or `None`]
        The exposure length of each frame, in seconds. `None` marks a
        frame whose length cannot be read.
    configured_weight : `str`, optional
        The ``Processing.Siril.stack_weight`` setting. A configured mode
        wins over the stacker's own choice.
    is_spectral : `bool`, optional
        Whether the stack is spectral. Those keep their configured weight.
    star_width_px : `Sequence` [`float` or `None`], optional
        The weighted star width of each frame, in pixels. Needed for the
        ``wfwhm`` weights and the weights inside each exposure group.
    rate_variance : `Sequence` [`float` or `None`], optional
        The variance of each frame in rate units. Needed for the
        ``noise`` weights.
    group_frame_noise_adu : `dict` [`float`, `float` or `None`], optional
        The noise of one raw frame of each exposure group, in counts,
        keyed by the group's length (``folded_exposure_lengths`` of the
        result). Needed for the weights of an exposure-group stack.

    Returns
    -------
    prediction : `StackWeightPrediction`
        The weighting mode, the exposure group of each frame, and the
        weight of each frame (or the reasons they are unknown).

    Notes
    -----
    The call reads no stored data, so it is a static method. Code that
    holds no `Astrometrics` handle can call it on the class,
    ``ProcessingPipelines.predict_stack_weights(...)``.
    """
    from astrometricslib.pipelines.stacking.stack_weight_prediction import predict_stack_weights

    return predict_stack_weights(
        exposure_seconds,
        configured_weight,
        is_spectral=is_spectral,
        star_width_px=star_width_px,
        rate_variance=rate_variance,
        group_frame_noise_adu=group_frame_noise_adu,
    )


def open_in_siril(self: _processing.ProcessingPipelines, file_path: str) -> None:
    """Open a stack in the Siril program's window, for a person to inspect.

    This uses the stacking program chosen for this API (Siril unless a
    caller chose another one that has a window; one without a window raises
    `ProcessingError`). Siril starts as its
    own process and keeps running after the call returns. If the program
    cannot start, the failure is logged and nothing is raised. Nothing is
    stacked, read or saved.

    Parameters
    ----------
    file_path : `str`
        The FITS file to open. An empty string opens Siril alone.
    """
    self._drivers.stacking_or_default().open_in_viewer(file_path)


def stop_stacking_programs(self: _processing.ProcessingPipelines) -> None:
    """Stop every stacking program this library started and still runs.

    The backend calls this when it stops, so no Siril process outlives
    it. A person's own Siril window, opened by hand, is not touched.
    Safe to call more than once, and when nothing is running.
    """
    self._drivers.stacking_or_default().stop_running_programs()
