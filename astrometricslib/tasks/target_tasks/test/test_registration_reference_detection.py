"""Tests for identifying the registration reference frame in a stack.

`_build_stack_quality_summary` looks for the one frame in a stack's
.seq file with an identity transform (dx=dy=0) and records it as
`registration_reference_frame`/`registration_reference_star_count`, so
a failed solve ("Found 0 stars in reference") can be traced back to the
actual frame. Two ways that can go wrong if unguarded:

1. `registration_frame_paths` (from `symlinked_light_paths`) and
   `registration_frames` (from `parse_seq_file`) can have different
   lengths -- the pairing loop just below already refuses to zip them
   in that case, and reference detection must refuse the same way,
   since indexing into `registration_frame_paths` by a `registration_frames`
   position is only valid when the two lists correspond 1:1.
2. A legacy `r_` sequence (already-aligned frames, preserved before the
   registration-sequence fix) records dx=dy=0 for *every* frame, not
   just the reference -- there is no single frame identifiable as "the"
   reference in that case.
"""

from unittest.mock import patch

from astrometricslib.models.target import FrameRecord, Target


def _registration_fact(
    dx: float = 0.0,
    dy: float = 0.0,
    nb_stars: int = 100,
    fwhm_x: float = 3.0,
    fwhm_y: float = 3.0,
    roundness: float = 0.9,
    rmse: float = 0.5,
) -> dict:
    """Build one parse_seq_file-shaped registration fact dict.

    Returns
    -------
    fact : `dict`
        A registration fact dict with the given field values.
    """
    return {
        "dx": dx,
        "dy": dy,
        "nb_stars": nb_stars,
        "fwhm_x": fwhm_x,
        "fwhm_y": fwhm_y,
        "roundness": roundness,
        "rmse": rmse,
    }


def _build_summary(  # ruff: ignore[missing-return-type-private-function]
    target_frames: list[FrameRecord],
    registration_frames: list[dict],
    symlinked_light_paths: list[str],
    stacked_path: str = "/lib/out.fits",
):
    """Call `_build_stack_quality_summary` with I/O calls patched out.

    Returns
    -------
    summary : `StackQualitySummary`
        The assembled summary.
    """
    from astrometricslib.tasks.target_tasks import stacking_tasks

    target = Target(id="TestTarget", frames=target_frames)
    diagnostics = {"symlinked_light_paths": symlinked_light_paths}

    with (
        patch(
            "astrometricslib.image_processing.quality_metrics.parse_seq_file",
            return_value=registration_frames,
        ),
        patch(
            "astrometricslib.image_processing.quality_metrics.measure_rejected_fraction",
            return_value=None,
        ),
        patch(
            "astrometricslib.image_processing.quality_metrics.measure_saturated_pixel_fraction",
            return_value=None,
        ),
        patch(
            "astrometricslib.image_processing.quality_metrics.measure_image_fwhm",
            return_value=None,
        ),
    ):
        return stacking_tasks._build_stack_quality_summary(
            target,
            is_spectral=False,
            frames_submitted=len(target_frames),
            target_frames=target_frames,
            excluded_frames=[],
            diagnostics=diagnostics,
            background_split=None,
            stacked_path=stacked_path,
        )


def test_reference_frame_is_recorded_for_a_normal_sequence():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The one zero-shift frame in a well-formed sequence is the reference."""
    paths = ["/lib/a.fits", "/lib/b.fits", "/lib/c.fits"]
    frames = [FrameRecord(path=p, role="LIGHT", camera="c", exposure="1.0") for p in paths]
    registration_frames = [
        _registration_fact(dx=0.0, dy=0.0, nb_stars=250),
        _registration_fact(dx=1.2, dy=-0.4),
        _registration_fact(dx=2.4, dy=-0.8),
    ]

    summary = _build_summary(frames, registration_frames, paths)

    assert summary.stacking_metrics.registration_reference_frame == "/lib/a.fits"
    assert summary.stacking_metrics.registration_reference_star_count == 250


def test_length_mismatch_does_not_name_a_reference():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A path/registration-fact length mismatch must not pick a wrong frame.

    Without the guard, positional indexing into `registration_frame_paths`
    by a `registration_frames` index would name whichever frame happens
    to sit at that position -- not necessarily the true reference.
    """
    paths = ["/lib/a.fits", "/lib/b.fits"]  # one fewer path than facts
    frames = [FrameRecord(path=p, role="LIGHT", camera="c", exposure="1.0") for p in paths]
    registration_frames = [
        _registration_fact(dx=0.0, dy=0.0, nb_stars=250),
        _registration_fact(dx=1.2, dy=-0.4),
        _registration_fact(dx=2.4, dy=-0.8),
    ]

    summary = _build_summary(frames, registration_frames, paths)

    assert summary.stacking_metrics.registration_reference_frame is None
    assert summary.stacking_metrics.registration_reference_star_count is None


def test_all_zero_shifts_does_not_fabricate_a_reference():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A legacy already-aligned sequence has no identifiable reference.

    Every frame in a preserved `r_` sequence records dx=dy=0, so no
    single frame can be named as the one true reference -- recording
    the first would be fabricated, not measured.
    """
    paths = ["/lib/a.fits", "/lib/b.fits", "/lib/c.fits"]
    frames = [FrameRecord(path=p, role="LIGHT", camera="c", exposure="1.0") for p in paths]
    registration_frames = [
        _registration_fact(dx=0.0, dy=0.0, nb_stars=100),
        _registration_fact(dx=0.0, dy=0.0, nb_stars=110),
        _registration_fact(dx=0.0, dy=0.0, nb_stars=120),
    ]

    summary = _build_summary(frames, registration_frames, paths)

    assert summary.stacking_metrics.registration_reference_frame is None
    assert summary.stacking_metrics.registration_reference_star_count is None
