"""Purpose: Record how the stacking program registered each frame.

Description: Registration lines every frame up with one reference frame. The
stacking program reports facts about each frame while it does so: star width,
roundness, shift and more. This module copies those facts onto each frame's
record, finds the reference frame, and checks how well the zero-order star
of spectral frames was tracked. It sets fields on the frames and on the
stack's quality summary.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrometricslib.models.quality.quality_summary import StackQualitySummary

__all__ = ["check_spectral_registration_quality", "update_frame_registration_results"]


def update_frame_registration_results(
    summary: StackQualitySummary,
    stacked_path: str,
    target_frames: list[Any],
    diagnostics: dict,
) -> None:
    """Record Siril's per-frame registration facts, find the reference frame.

    Per-frame registration facts (Siril's own findstar pass, computed
    for free during registration) are recorded onto each `FrameRecord`
    for later pipelines/runs to read, not used for any verdict here.
    Frame order in the .seq file follows Siril's own symlink submission
    order, which can differ from `target_frames`' order (corrupt/wrong-
    camera frames get filtered during symlinking) --
    `symlinked_light_paths` is the authoritative alignment reference,
    same discipline the spectral registration check uses.

    Also identifies the registration reference frame -- the one Siril
    aligned every other frame to, i.e. the frame whose transform is the
    identity (no shift of its own) -- and records it onto
    `summary.stacking_metrics`.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
        registration_reference_from_diagnostics,
    )

    registration_frames = diagnostics.get("registration_frames", [])
    registration_frame_paths = diagnostics.get("symlinked_light_paths", [])

    # Siril aligns every frame to one reference, so a poor reference fails the
    # whole stack -- M 42 aborted with "Found 0 stars in reference" and
    # nothing recorded which frame that was. The reference is the frame whose
    # transform is the identity; `registration_reference_from_diagnostics`
    # names it only when it is certain.
    reference = registration_reference_from_diagnostics(diagnostics)
    if reference is not None:
        summary.stacking_metrics.registration_reference_frame = reference[0]
        summary.stacking_metrics.registration_reference_star_count = reference[1]

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
            has_existing_facts = frame.measurements.registration_fwhm_x_px is not None
            stored_shift_is_degenerate = (
                not frame.measurements.registration_dx_px and not frame.measurements.registration_dy_px
            )
            run_offers_real_shift = bool(registration_facts["dx"] or registration_facts["dy"])
            if has_existing_facts and not (stored_shift_is_degenerate and run_offers_real_shift):
                # A frame stacked before these fields existed keeps its
                # first-run facts. Its empty fields are filled, so a restack
                # records them.
                _fill_empty_registration_fields(frame.measurements, registration_facts)
                continue
            # `registration_fwhm_y_px` and `registration_rmse` are no longer
            # filled: their old values were the weighted FWHM and the sky
            # background level (see `FrameMeasurements`). The field named
            # `..._fwhm_x_px` holds Siril's plain FWHM.
            frame.measurements.registration_fwhm_x_px = registration_facts["fwhm_px"]
            frame.measurements.registration_weighted_fwhm_px = registration_facts["weighted_fwhm_px"]
            frame.measurements.registration_roundness = registration_facts["roundness"]
            frame.measurements.registration_quality = registration_facts["quality"]
            frame.measurements.registration_background_level = registration_facts["background_level"]
            frame.measurements.registration_rotation_deg = registration_facts["rotation_deg"]
            frame.measurements.registration_star_count = registration_facts["star_count"]
            frame.measurements.registration_dx_px = registration_facts["dx"]
            frame.measurements.registration_dy_px = registration_facts["dy"]


# The registration fields that later versions added to a frame's record, and
# the key of Siril's per-frame facts that fills each. The first-run fields
# (`registration_fwhm_x_px`, `registration_dx_px` and `registration_dy_px`) are
# not in this list because a restack never overwrites them.
_LATER_REGISTRATION_FIELDS: dict[str, str] = {
    "registration_weighted_fwhm_px": "weighted_fwhm_px",
    "registration_roundness": "roundness",
    "registration_quality": "quality",
    "registration_background_level": "background_level",
    "registration_rotation_deg": "rotation_deg",
    "registration_star_count": "star_count",
}


def _fill_empty_registration_fields(measurements: Any, registration_facts: dict[str, Any]) -> None:
    """Fill a frame's empty later registration fields, and no others.

    Frames stacked before the later fields existed keep their first-run
    shift and width. A restack gives them the new fields, because it is the
    only time Siril measures them again.

    Parameters
    ----------
    measurements : `FrameMeasurements`
        The frame's measurements. Only fields that are `None` change.
    registration_facts : `dict`
        Siril's facts for the frame in this run, as `parse_seq_file` returns.
    """
    for field_name, fact_key in _LATER_REGISTRATION_FIELDS.items():
        if getattr(measurements, field_name) is None and registration_facts.get(fact_key) is not None:
            setattr(measurements, field_name, registration_facts[fact_key])


def check_spectral_registration_quality(
    summary: StackQualitySummary, stacked_path: str, diagnostics: dict
) -> None:
    """Flag frames with spectral zero-order-star tracking concerns.

    Checks matched star count, fit RMSE, and position/brightness
    stability for the zero-order star -- not a whole-field FWHM
    comparison -- see `registration_quality.py`'s module docstring for
    why. Requires `frame_paths`, `seq_frames`, and `zero_order_stars`
    to be index-aligned; all three are populated by
    the `siril_interface` package during the same registration pass the real
    stack already ran (`parse_seq_file` reads the preserved .seq file,
    mirroring the standard-imaging FWHM check; `zero_order_stars` comes
    from `diagnostics` since spectral .lst files aren't preserved as
    files, only parsed in place before the scratch directory is
    cleaned up).

    Sets `summary.stacking_metrics.spectral_registration_flags` in place.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.spectroscopy.utilities.registration_quality import (
        evaluate_spectral_registration_quality,
    )

    seq_frames = diagnostics.get("registration_frames", [])
    zero_order_stars = diagnostics.get("zero_order_stars", [])
    frame_paths = diagnostics.get("symlinked_light_paths", [])

    if len(frame_paths) == len(seq_frames) == len(zero_order_stars) and frame_paths:
        flagged = evaluate_spectral_registration_quality(frame_paths, seq_frames, zero_order_stars)
        summary.stacking_metrics.spectral_registration_flags = [ExcludedFrame(**entry) for entry in flagged]
        summary.stacking_metrics.spectral_registration_checked = True
