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

import pytest

from astrometricslib.models.target import FrameRecord, Target


def _registration_fact(
    dx: float = 0.0,
    dy: float = 0.0,
    nb_stars: int = 100,
    fwhm_px: float = 3.0,
    weighted_fwhm_px: float = 3.0,
    roundness: float = 0.9,
    background_level: float = 0.02,
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
        "star_count": nb_stars,
        "fwhm_px": fwhm_px,
        "weighted_fwhm_px": weighted_fwhm_px,
        "roundness": roundness,
        "quality": 0.0,
        "background_level": background_level,
        "rotation_deg": 0.0,
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
    from astrometricslib.pipelines.stacking import stage as stacking_tasks

    target = Target(id="TestTarget", frames=target_frames)
    diagnostics = {
        "symlinked_light_paths": symlinked_light_paths,
        "registration_frames": registration_frames,
    }

    with (
        patch(
            "astrometricslib.pipelines.shared.quality.quality_metrics.measure_saturated_pixel_fraction",
            return_value=None,
        ),
        patch(
            "astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm",
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


def test_reference_frame_is_recorded_for_a_normal_sequence() -> None:
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


def test_length_mismatch_does_not_name_a_reference() -> None:
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


def test_all_zero_shifts_does_not_fabricate_a_reference() -> None:
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


def test_the_stack_summary_records_the_camera_profile_of_its_frames() -> None:
    """A listed camera is recorded plainly; an unlisted one flags."""
    paths = ["/lib/a.fits", "/lib/b.fits"]
    registration_frames = [_registration_fact(dx=0.0, dy=0.0), _registration_fact(dx=1.0, dy=1.0)]

    listed = [FrameRecord(path=p, role="LIGHT", camera="ZWO ASI 533MM Pro", exposure="1.0") for p in paths]
    listed_summary = _build_summary(listed, registration_frames, paths)
    assert listed_summary.camera_profile is not None
    assert listed_summary.camera_profile.profile_name == "ZWO ASI533MM Pro"
    assert listed_summary.camera_profile.is_generic_fallback is False

    unlisted = [FrameRecord(path=p, role="LIGHT", camera="Acme Imager 9000", exposure="1.0") for p in paths]
    unlisted_summary = _build_summary(unlisted, registration_frames, paths)
    assert unlisted_summary.camera_profile is not None
    assert unlisted_summary.camera_profile.is_generic_fallback is True
    assert unlisted_summary.flagged is True
    assert any("Acme Imager 9000" in reason for reason in unlisted_summary.flag_reasons)


def test_registration_facts_fill_the_honestly_named_fields_only() -> None:
    """Siril's values fill the new fields; the mislabeled ones stay empty."""
    frame = FrameRecord(path="/lib/a.fits", role="LIGHT", camera="c", exposure="1.0")
    other = FrameRecord(path="/lib/b.fits", role="LIGHT", camera="c", exposure="1.0")
    registration_fact = _registration_fact(
        dx=0.0, dy=0.0, nb_stars=1163, fwhm_px=2.83064, weighted_fwhm_px=3.27475, background_level=0.0238392
    )
    registration_fact["rotation_deg"] = 0.001

    _build_summary(
        [frame, other],
        [registration_fact, _registration_fact(dx=1.0, dy=1.0)],
        ["/lib/a.fits", "/lib/b.fits"],
    )

    measurements = frame.measurements
    assert measurements.registration_fwhm_x_px == pytest.approx(2.83064)
    assert measurements.registration_weighted_fwhm_px == pytest.approx(3.27475)
    assert measurements.registration_background_level == pytest.approx(0.0238392)
    assert measurements.registration_quality == pytest.approx(0.0)
    assert measurements.registration_rotation_deg == pytest.approx(0.001)
    assert measurements.registration_star_count == 1163
    assert measurements.registration_fwhm_y_px is None
    assert measurements.registration_rmse is None
