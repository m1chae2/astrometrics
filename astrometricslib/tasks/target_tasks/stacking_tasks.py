"""Purpose: Image Stacking & Spectroscopy Extraction.

Description: Extracted image processing operations for running the Siril
headless stacking driver and executing the target-centric spectroscopy
analysis pipeline.
"""

import logging
from typing import Any

from astrometricslib.utilities.enums import FilterType

logger = logging.getLogger(__name__)


def stack_frames(
    target,  # ruff: ignore[missing-type-function-argument]
    log_file: str | None = None,
    frames_to_stack: list[Any] | None = None,
    filter_type: Any | None = None,
    rejection_sigma: tuple[float, float] | None = None,
    filter_wfwhm: str | None = None,
    filter_round: str | None = None,
    stack_weight: str | None = None,
    generate_rejmap: bool | None = None,
    output_file: str | None = None,
) -> str | None:
    """Run the stacking pipeline via the Siril ImageProcessing driver.

    This function combines multiple individual exposures into a
    single, high-signal "master" image. By averaging frames together,
    the random camera noise cancels out while the true astronomical
    signal builds up. It also applies pixel-rejection algorithms to
    throw out anomalies like satellite trails or cosmic rays that only
    appear in a few frames.

    Parameters
    ----------
    target : Target
        The astronomical target whose frames should be stacked.
    log_file : `str` or `None`, optional
        Path to a log file for Siril output.
    frames_to_stack : `list` of `Any` or `None`, optional
        Explicit list of frames to stack. If None, resolves from the target.
    filter_type : `Any` or `None`, optional
        The specific optical filter to stack (e.g., 'L', 'HA').
    rejection_sigma : `tuple` of (`float`, `float`) or `None`, optional
        The lower and upper sigma limits for pixel rejection.
    filter_wfwhm : `str` or `None`, optional
        Condition string to filter out frames with poor FWHM (blurriness).
    filter_round : `str` or `None`, optional
        Condition string to filter out frames with poor star roundness.
    stack_weight : `str` or `None`, optional
        Weighting formula for how frames are averaged.
    generate_rejmap : `bool` or `None`, optional
        Whether to generate a rejection map showing which pixels were
        discarded.
    output_file : `str` or `None`, optional
        Explicit path for the output stacked image.

    Returns
    -------
    stacked_path : `str` or `None`
        The path to the stacked output file, or `None` if stacking
        did not produce an output.

    Raises
    ------
    ValueError
        If the target has no frames available to stack, or its
        frames mix spectral and standard filter types.
    """
    if frames_to_stack is not None:
        target_frames = frames_to_stack
    elif filter_type is not None:
        from astrometricslib.models.target import FrameRecord

        norm_filter = FrameRecord.normalize_filter(filter_type)
        if not isinstance(norm_filter, FilterType):
            for ft in FilterType:
                if (
                    ft.name.upper() == str(filter_type).upper()
                    or ft.value.upper() == str(filter_type).upper()
                ):
                    norm_filter = ft
                    break
        target_frames = [
            f
            for f in target.frames
            if f.filter == norm_filter
            and not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]
    else:
        target_frames = [
            f
            for f in target.frames
            if not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]

    if not target_frames:
        raise ValueError("Target has no frames available to stack.")

    # Validate that only homogeneous frame types are stacked (no mixed
    # spectral/standard frames)
    has_spectral = any(getattr(f, "filter", None) in ("SPEC", FilterType.SPEC) for f in target_frames)
    has_standard = any(getattr(f, "filter", None) not in ("SPEC", FilterType.SPEC) for f in target_frames)
    if has_spectral and has_standard:
        raise ValueError(
            "Target contains a mixed set of spectral ('SPEC') and standard imaging frames. "
            "Stacking mixed frame types is not permitted."
        )

    if output_file is None:
        safe_target_id = target.id.replace(" ", "_")
        filter_tag = None
        if has_spectral:
            filter_tag = "SPEC"
        elif filter_type is not None:
            if hasattr(filter_type, "name"):
                filter_tag = filter_type.name.upper()
            else:
                filter_tag = str(filter_type).upper()
        else:
            filters = {getattr(f, "filter", None) for f in target_frames}
            filter_names = {
                f.name.upper() if hasattr(f, "name") else str(f).upper() for f in filters if f is not None
            }
            if len(filter_names) == 1:
                filter_tag = next(iter(filter_names))

        if filter_tag:
            canonical_mapping = {
                "LUMINANCE": "L",
                "RED": "R",
                "GREEN": "G",
                "BLUE": "B",
                "STAR ANALYZER 200": "SPEC",
                "SPECTROSCOPY": "SPEC",
            }
            filter_tag = canonical_mapping.get(filter_tag, filter_tag)
            output_file = f"{safe_target_id}_{filter_tag}_Stacked.fits"
        else:
            output_file = f"{safe_target_id}_Stacked.fits"

    frames_submitted = len(target_frames)
    from astrometricslib.models.quality_summary import ExcludedFrame

    excluded_frames: list[ExcludedFrame] = []

    # Mixed-gain stacking corrupts noise statistics (each gain setting has
    # its own read-noise/dark-current profile), so only the dominant-gain
    # subset is stacked -- fail closed by excluding the minority rather
    # than silently mixing gains.
    from astrometricslib.tasks.target_tasks.frame_homogeneity import find_dominant_gain_subset

    target_frames, excluded_by_gain = find_dominant_gain_subset(target_frames)
    if excluded_by_gain:
        logger.warning(
            f"Excluding {len(excluded_by_gain)} frame(s) with a minority gain setting from "
            f"the stack for target '{target.id}': "
            f"{[f.path for f in excluded_by_gain]}"
        )
        excluded_frames.extend(
            ExcludedFrame(path=f.path, reason="minority gain setting") for f in excluded_by_gain
        )
    if not target_frames:
        raise ValueError("Target has no frames available to stack after gain-homogeneity filtering.")

    # Detects a session silently mixing two different sky conditions (e.g.
    # thin clouds rolling in partway through and never clearing) -- a real
    # problem found on NGC 2403 that rejected-fraction and
    # gain/calibration checks alone didn't catch. Adds real per-frame I/O
    # cost (~1.4s/frame), so it's config-gated; see
    # config_loader.get_background_homogeneity_check_enabled's docstring
    # for the tradeoff.
    from astrometricslib.utilities.config_loader import get_configuration

    background_split = None
    if get_configuration().get_background_homogeneity_check_enabled():
        from astrometricslib.data_access.background_homogeneity import (
            measure_frame_background_level,
            measure_frame_saturated_pixel_fraction,
        )
        from astrometricslib.tasks.target_tasks.background_homogeneity_tasks import (
            detect_background_split,
        )

        background_levels = []
        for frame in target_frames:
            try:
                # Computed once, persisted onto the FrameRecord (which
                # rides along with Target's normal save path) -- later
                # pipelines/runs read these instead of recomputing them.
                if frame.background_level is None:
                    frame.background_level = measure_frame_background_level(frame.path)
                if frame.saturated_pixel_fraction is None:
                    frame.saturated_pixel_fraction = measure_frame_saturated_pixel_fraction(frame.path)
                if frame.background_level is not None:
                    background_levels.append(frame.background_level)
            except Exception as exc:
                logger.debug("Skipping background/saturation measurement for '%s': %s", frame.path, exc)
                continue
        background_split = detect_background_split(background_levels)
        if background_split:
            logger.warning(
                f"Background-homogeneity split detected for target '{target.id}': {background_split}"
            )

    from astrometricslib.drivers.siril_interface import ImageProcessing

    siril_driver = ImageProcessing()
    stacked_path = siril_driver.process_target(
        id=target.id,
        image_files=[f.model_dump() for f in target_frames],
        output_file=output_file,
        log_file=log_file,
        is_spectral=has_spectral,
        rejection_sigma=rejection_sigma,
        filter_wfwhm=filter_wfwhm,
        filter_round=filter_round,
        stack_weight=stack_weight,
        generate_rejmap=generate_rejmap,
    )

    diagnostics = siril_driver.last_run_diagnostics
    excluded_frames.extend(
        ExcludedFrame(path=path, reason="corrupt or unreadable FITS file")
        for path in diagnostics.get("corrupt_frames_skipped", [])
    )

    summary = _build_stack_quality_summary(
        target=target,
        is_spectral=has_spectral,
        frames_submitted=frames_submitted,
        target_frames=target_frames,
        excluded_frames=excluded_frames,
        diagnostics=diagnostics,
        background_split=background_split,
        stacked_path=stacked_path,
    )

    if has_spectral:
        target.spectral_stack_quality_summary = summary
    else:
        target.stack_quality_summary = summary

    if stacked_path:
        if has_spectral:
            target.stacked_spectral_target = stacked_path
        elif _record_configuration_stack(target, target_frames, stacked_path):
            target.stacked_image = stacked_path

    return stacked_path


def _record_configuration_stack(target, target_frames, stacked_path) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Record this stack under its configuration and say if it is preferred.

    Additive to `stacked_image`, which still names a single stack so
    every existing reader and the UI are unaffected. Without this, a
    target imaged through two optics keeps only one stack and the other
    run's work is lost -- NGC 7023 has 424 frames at one focal length
    and 111 at another, and both are worth keeping.

    Parameters
    ----------
    target : `Target`
        The target to record against.
    target_frames : `list`
        The frames this stack was built from, used to identify the
        configuration and count what contributed.
    stacked_path : `str`
        Path of the stack just produced.

    Returns
    -------
    should_become_stacked_image : `bool`
        `True` when `stacked_image` should point at this stack: either
        this is the observatory's own camera and optic, or nothing
        preferred has been recorded and some stack is better than none.
    """
    from astrometricslib.models.target import StackConfigurationResult
    from astrometricslib.tasks.target_tasks.pipeline_tasks import frame_configuration_key

    keys = {frame_configuration_key(frame) for frame in target_frames}
    keys.discard(None)
    if len(keys) != 1:
        # Either no frame carried a focal length, or -- the case this
        # whole mechanism exists to prevent -- the stack blended several
        # optics. Recording it under a single key would assert something
        # untrue, but the stack itself is still the only one there is.
        logger.debug(
            "Not recording a per-configuration stack for '%s': %d configurations present.",
            getattr(target, "id", "?"),
            len(keys),
        )
        return True

    configuration_key = next(iter(keys))
    camera = (target_frames[0].camera or "") if target_frames else ""
    focal_length = next((frame.focal_length_mm for frame in target_frames if frame.focal_length_mm), None)

    primary_camera = None
    primary_focal_length = None
    try:
        from astrometricslib.utilities.config_loader import get_configuration

        configuration = get_configuration()
        primary_camera = configuration.get_primary_camera_name()
        primary_focal_length = configuration.get_primary_focal_length_mm()
    except Exception as configuration_error:
        logger.debug("Could not read the primary camera or optic: %s", configuration_error)

    # Both must match. Focal length alone would mark two cameras sharing
    # one focal length as equally preferred, and camera alone would do
    # the same for one camera used through two optics -- and this
    # library has both cases.
    optic_matches = bool(
        focal_length and primary_focal_length and round(focal_length) == round(primary_focal_length)
    )
    camera_matches = bool(primary_camera and camera and _camera_names_match(camera, primary_camera))
    is_preferred = optic_matches and camera_matches

    target.stacks_by_configuration[configuration_key] = StackConfigurationResult(
        configuration_key=configuration_key,
        camera=camera,
        focal_length_mm=focal_length,
        frames_stacked=len(target_frames),
        stacked_image=stacked_path,
        is_preferred=is_preferred,
    )

    if is_preferred:
        return True

    # Nothing preferred exists, so this stack is the best answer
    # available -- a target whose only optic is not the observatory's
    # primary must still have a stacked_image.
    return not any(
        recorded.is_preferred
        for key, recorded in target.stacks_by_configuration.items()
        if key != configuration_key
    )


def _camera_names_match(first: str, second: str) -> bool:
    """Compare two camera names tolerantly.

    Configuration and FITS headers spell the same camera differently --
    "ZWO ASI533MM Pro" against the header-derived "ZWO ASI 533MM Pro" --
    so spaces and case are ignored rather than requiring the observer to
    match the header exactly.

    Parameters
    ----------
    first, second : `str`
        Camera names to compare.

    Returns
    -------
    matches : `bool`
        `True` when the names denote the same camera.
    """
    return "".join(first.split()).casefold() == "".join(second.split()).casefold()


def _build_stack_quality_summary(  # ruff: ignore[missing-return-type-private-function]
    target,  # ruff: ignore[missing-type-function-argument]
    is_spectral: bool,
    frames_submitted: int,
    target_frames: list[Any],
    excluded_frames: list[Any],
    diagnostics: dict,
    background_split: dict | None,
    stacked_path: str | None,
):
    """Assemble a StackQualitySummary from data gathered in stack_frames.

    Standard stacks get a whole-field FWHM sanity check (comparing the
    stacked image against the median FWHM of a sample of the raw inputs,
    both measured the same way); spectral stacks get a
    zero-order-star-tracking check instead (see
    spectral_registration_quality.py) -- a whole-field FWHM doesn't
    capture what matters for a spectral trace.

    Returns
    -------
    summary : `StackQualitySummary`
        The assembled stack quality summary, with flags set for any
        detected degradation.
    """
    from astrometricslib.data_access.image_quality_metrics import (
        measure_image_fwhm,
        measure_rejected_fraction,
        measure_saturated_pixel_fraction,
        parse_seq_file,
    )
    from astrometricslib.models.quality_summary import (
        ExcludedFrame,
        StackingPipelineQualityMetrics,
        StackQualitySummary,
        TargetSessionContribution,
    )
    from astrometricslib.tasks.shared.saturation_analysis import is_saturation_significant
    from astrometricslib.tasks.target_tasks.stack_quality_tasks import (
        is_rejected_fraction_significant,
        is_stacked_fwhm_degraded,
    )
    from astrometricslib.tasks.target_tasks.target_session_tasks import derive_target_sessions

    frames_stacked = len(target_frames) - len(diagnostics.get("corrupt_frames_skipped", []))

    target_sessions = derive_target_sessions(target.id, target_frames)
    excluded_paths = {excluded_frame.path for excluded_frame in excluded_frames}
    target_session_breakdown = [
        TargetSessionContribution(
            session_id=session.id,
            frames_contributed=len(session.frame_paths),
            frames_clipped=sum(1 for path in session.frame_paths if path in excluded_paths),
        )
        for session in target_sessions
    ]

    summary = StackQualitySummary(
        target_id=target.id,
        target_session_ids=[session.id for session in target_sessions],
        target_session_breakdown=target_session_breakdown,
        resolved_parameters={
            "rejection_sigma_low": diagnostics.get("rejection_sigma_low", 0.0),
            "rejection_sigma_high": diagnostics.get("rejection_sigma_high", 0.0),
            "rejection_sigma_mode": diagnostics.get("rejection_sigma_mode", "unknown"),
            "filter_wfwhm_requested": diagnostics.get("filter_wfwhm_requested"),
            "filter_wfwhm_effective": diagnostics.get("filter_wfwhm_effective"),
            "filter_wfwhm_loosened": diagnostics.get("filter_wfwhm_loosened", False),
        },
        quality_processing_applied=frames_stacked > 1,
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=is_spectral,
            frames_submitted=frames_submitted,
            frames_stacked=frames_stacked,
            excluded_frames=excluded_frames,
            calibration_mismatch_flags=diagnostics.get("calibration_mismatch_flags", []),
            stacking_duration_seconds=diagnostics.get("stacking_duration_seconds"),
            debayer_applied=diagnostics.get("debayer_applied"),
        ),
    )

    if background_split:
        summary.stacking_metrics.background_split_detected = True
        summary.stacking_metrics.background_split_detail = (
            f"{background_split['low_group_count']} frame(s) at background~"
            f"{background_split['low_group_median']:.0f} vs {background_split['high_group_count']} "
            f"frame(s) at ~{background_split['high_group_median']:.0f} (gap ratio "
            f"{background_split['gap_ratio']:.1f})"
        )

    if stacked_path:
        rejected_fraction = measure_rejected_fraction(stacked_path)
        if rejected_fraction is not None:
            summary.stacking_metrics.rejected_pixel_fraction = rejected_fraction
            summary.stacking_metrics.rejected_fraction_flagged = is_rejected_fraction_significant(
                rejected_fraction
            )

        saturated_fraction = measure_saturated_pixel_fraction(stacked_path)
        if saturated_fraction is not None:
            summary.stacking_metrics.saturated_pixel_fraction = saturated_fraction
            summary.stacking_metrics.saturation_flagged = is_saturation_significant(saturated_fraction)

        if not is_spectral and summary.quality_processing_applied:
            # Per-frame registration facts (Siril's own findstar pass,
            # computed for free during registration) -- these are
            # distinct from the measure_image_fwhm-based comparison below
            # (see that block's docstring for why the two aren't on the
            # same scale) and are persisted as facts on FrameRecord for
            # later pipelines/runs to read, not used for any verdict
            # here. Frame order in the .seq file follows Siril's own
            # symlink submission order, which can differ from
            # target_frames' order (corrupt/wrong-camera frames get
            # filtered during symlinking) -- symlinked_light_paths is the
            # authoritative alignment reference, same discipline the
            # spectral branch below already uses.
            seq_path = f"{stacked_path.rsplit('.', 1)[0]}_Registration.seq"
            registration_frames = parse_seq_file(seq_path)
            registration_frame_paths = diagnostics.get("symlinked_light_paths", [])

            # Siril aligns every frame to one reference, so a poor
            # reference fails the whole stack -- M 42 aborted with "Found
            # 0 stars in reference" and nothing recorded which frame that
            # was. The reference is the frame whose transform is the
            # identity, i.e. the one with no shift of its own.
            for index, facts in enumerate(registration_frames):
                if not facts["dx"] and not facts["dy"]:
                    if index < len(registration_frame_paths):
                        summary.stacking_metrics.registration_reference_frame = registration_frame_paths[
                            index
                        ]
                    summary.stacking_metrics.registration_reference_star_count = facts["nb_stars"]
                    break

            if len(registration_frame_paths) == len(registration_frames):
                frame_by_path = {frame.path: frame for frame in target_frames}
                for frame_path, registration_facts in zip(
                    registration_frame_paths, registration_frames, strict=False
                ):
                    frame = frame_by_path.get(frame_path)
                    if frame is None:
                        continue
                    # Normally the first run's measurements win, so a
                    # target stacked twice (standard then spectral) does
                    # not have its facts clobbered by the second pass.
                    # The exception is a frame carrying shifts that are
                    # known to be degenerate: runs before the
                    # registration-sequence fix preserved the *registered*
                    # sequence, whose frames are already aligned and so
                    # recorded dx=dy=0 for every frame. A non-zero shift
                    # now available for that same frame is real data
                    # replacing a known-bad zero, so it is allowed
                    # through. A genuine reference frame also has
                    # dx=dy=0, but this run offers 0 for it too, so it is
                    # never rewritten.
                    has_existing_facts = frame.registration_fwhm_x_px is not None
                    stored_shift_is_degenerate = not frame.registration_dx_px and not frame.registration_dy_px
                    run_offers_real_shift = bool(registration_facts["dx"] or registration_facts["dy"])
                    if has_existing_facts and not (stored_shift_is_degenerate and run_offers_real_shift):
                        continue
                    frame.registration_fwhm_x_px = registration_facts["fwhm_x"]
                    frame.registration_fwhm_y_px = registration_facts["fwhm_y"]
                    frame.registration_roundness = registration_facts["roundness"]
                    frame.registration_rmse = registration_facts["rmse"]
                    frame.registration_star_count = registration_facts["nb_stars"]
                    frame.registration_dx_px = registration_facts["dx"]
                    frame.registration_dy_px = registration_facts["dy"]

            # Measured with the same measure_image_fwhm function used on
            # the stacked result below, not Siril's own PSF-fit FWHM from
            # the preserved .seq file -- those two methods aren't on the
            # same absolute scale (confirmed empirically: Siril's fit
            # reported ~2.6px median on a real M 13 session where
            # measure_image_fwhm reported ~4.25px on the *same raw input
            # frames*), so comparing across methods produced a false
            # "degraded" flag on every stack rather than a real signal.
            # Capped at 15 frames (matching FWHM_MEASUREMENT_STAR_COUNT's
            # existing per-image star-count cap) since a median only
            # needs a representative sample, unlike the background-split
            # check above which needs every frame to avoid missing a
            # split.
            input_fwhms = []
            for frame in target_frames[:15]:
                try:
                    fwhm = measure_image_fwhm(frame.path)
                    if fwhm is not None:
                        input_fwhms.append(fwhm)
                except Exception as exc:
                    logger.debug("Skipping FWHM measurement for '%s': %s", frame.path, exc)
                    continue
            if input_fwhms:
                import statistics as _statistics

                summary.stacking_metrics.median_input_fwhm_px = _statistics.median(input_fwhms)

            stacked_fwhm = measure_image_fwhm(stacked_path)
            if stacked_fwhm is not None:
                summary.stacking_metrics.stacked_fwhm_px = stacked_fwhm
                if summary.stacking_metrics.median_input_fwhm_px is not None:
                    summary.stacking_metrics.fwhm_degraded = is_stacked_fwhm_degraded(
                        stacked_fwhm, summary.stacking_metrics.median_input_fwhm_px
                    )

        if is_spectral and summary.quality_processing_applied:
            # Zero-order-star-tracking check (matched star count, fit
            # RMSE, position/brightness stability), not a whole-field
            # FWHM comparison -- see spectral_registration_quality.py's
            # module docstring for why. Requires frame_paths, seq_frames,
            # and zero_order_stars to be index-aligned; both are
            # populated by siril_interface.py during the same
            # registration pass the real stack already ran
            # (parse_seq_file reads the preserved .seq file, mirroring
            # the standard-imaging FWHM check above; zero_order_stars
            # comes from self.last_run_diagnostics since spectral .lst
            # files aren't preserved as files, only parsed in place
            # before the scratch directory is cleaned up).
            from astrometricslib.tasks.target_tasks.spectral_registration_quality import (
                evaluate_spectral_registration_quality,
            )

            seq_path = f"{stacked_path.rsplit('.', 1)[0]}_Registration.seq"
            seq_frames = parse_seq_file(seq_path)
            zero_order_stars = diagnostics.get("zero_order_stars", [])
            frame_paths = diagnostics.get("symlinked_light_paths", [])

            if len(frame_paths) == len(seq_frames) == len(zero_order_stars) and frame_paths:
                flagged = evaluate_spectral_registration_quality(frame_paths, seq_frames, zero_order_stars)
                summary.stacking_metrics.spectral_registration_flags = [
                    ExcludedFrame(**entry) for entry in flagged
                ]

    metrics = summary.stacking_metrics
    flag_reasons = []
    if metrics.background_split_detected:
        flag_reasons.append(f"background split: {metrics.background_split_detail}")
    if metrics.rejected_fraction_flagged:
        flag_reasons.append(
            f"rejected pixel fraction {metrics.rejected_pixel_fraction:.1%} at or above threshold"
        )
    if metrics.fwhm_degraded:
        flag_reasons.append(
            f"stacked FWHM {metrics.stacked_fwhm_px:.2f}px degraded vs median input "
            f"{metrics.median_input_fwhm_px:.2f}px"
        )
    if metrics.spectral_registration_flags:
        flag_reasons.append(
            f"{len(metrics.spectral_registration_flags)} frame(s) with spectral registration concerns"
        )
    if metrics.calibration_mismatch_flags:
        flag_reasons.append(f"{len(metrics.calibration_mismatch_flags)} calibration metadata mismatch(es)")
    if metrics.saturation_flagged:
        flag_reasons.append(
            f"saturated pixel fraction {metrics.saturated_pixel_fraction:.2%} at or above threshold"
        )
    if not summary.quality_processing_applied:
        flag_reasons.append("single-frame stack: no rejection/registration quality processing applied")

    summary.flagged = bool(flag_reasons)
    summary.flag_reasons = flag_reasons
    return summary


def add_frame(  # ruff: ignore[missing-return-type-undocumented-public-function]
    target,  # ruff: ignore[missing-type-function-argument]
    path: str,
    role: str = "LIGHT",
    filter_type: str | None = None,
    camera: str | None = None,
):
    """Add a frame to the target, or update it in place if already present.

    Lazily loads FITS metadata, parses the FITS fields, checks for
    spectral/standard homogeneity, appends a FrameRecord to
    target.frames (or updates the matching existing record by path),
    and recalculates the target's total exposure time.

    Returns
    -------
    frame_record : `FrameRecord`
        The newly appended frame record, or the existing record that
        was updated in place.

    Raises
    ------
    ValueError
        If adding `path` would mix spectral ("SPEC") and standard
        imaging frames on the same target.
    """
    from astrometricslib.data_access.frame_scanning import create_frame_record_from_fits
    from astrometricslib.models.target import FrameRecord

    record = create_frame_record_from_fits(path, camera)
    record.role = role
    if filter_type is not None:
        record.filter = FrameRecord.normalize_filter(filter_type)

    is_spectral = record.filter == FilterType.SPEC
    has_spectral = any(f.filter == FilterType.SPEC for f in target.frames)
    has_standard = any(f.filter != FilterType.SPEC for f in target.frames)

    if is_spectral and has_standard:
        raise ValueError(
            "Target contains a mixed set of spectral ('SPEC') and standard imaging frames. "
            "Stacking mixed frame types is not permitted."
        )
    if not is_spectral and has_spectral:
        raise ValueError(
            "Target contains a mixed set of spectral ('SPEC') and standard imaging frames. "
            "Stacking mixed frame types is not permitted."
        )

    for f in target.frames:
        if f.path == path:
            f.role = role
            if filter_type is not None:
                f.filter = record.filter
            target.recalculate_total_exposure()
            return f

    target.frames.append(record)
    target.recalculate_total_exposure()
    return record


def analyze_frame_spectroscopy(target, path: str, limit: int = 10) -> tuple[Any, list[Any]]:  # ruff: ignore[missing-type-function-argument]
    """Run spectroscopy analysis on a single frame in this target's context.

    Returns
    -------
    result : `tuple[Any, list[Any]]`
        A tuple ``(context, stellar_objects)`` of the astrometry
        pipeline context and the extracted stellar objects.
    """
    if not any(f.path == path for f in target.frames):
        add_frame(target, path)

    from astrometricslib.drivers import disk_interface
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import AstrometryPipeline
    from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import (
        SpectroscopyPipeline,
    )
    from astrometricslib.utilities.config_loader import get_configuration

    config = get_configuration()
    astrometry = AstrometryPipeline(config)
    context = astrometry.prepare_image(path, attempt_plate_solving=False)

    from astrometricslib.utilities import ConfigLoader

    spec_config = ConfigLoader.load_spectroscopy_config(app_config=config)
    spectroscopy = SpectroscopyPipeline(spec_config)
    stellar_objects = spectroscopy.process(context, limit=limit)

    for obj in stellar_objects:
        if target.id not in obj.target_ids:
            obj.target_ids.append(target.id)

    existing = disk_interface.load_stellar_objects(config) or []
    existing_map = {obj.id: obj for obj in existing}

    for obj in stellar_objects:
        if obj.id in existing_map:
            for tid in obj.target_ids:
                if tid not in existing_map[obj.id].target_ids:
                    existing_map[obj.id].target_ids.append(tid)
            existing_map[obj.id].spectrum_data_processed = obj.spectrum_data_processed
        else:
            existing_map[obj.id] = obj

    disk_interface.save_stellar_objects(config, list(existing_map.values()))

    return context, stellar_objects
