"""Purpose: Unit tests for gathering sky-analysis measurements.

Description: Verifies how frames and guiding runs become positioned samples:
which frames count, how missing positions are reported, which nights' guiding
is left out as unreliable, and how the exposure lengths in use are chosen.
"""

from typing import Any

import pytest

from wayfindinglib.models.session.session_quality import (
    GuidingInputQuality,
    GuidingPerformance,
    GuidingRunPerformance,
    GuidingSessionAnalysis,
)
from wayfindinglib.tasks.control_tasks.capture_analysis_tasks import exposure_lengths_in_use
from wayfindinglib.tasks.control_tasks.sky_analysis_tasks import (
    sky_samples_from_frames,
    sky_samples_from_guiding,
)

_NIGHT = 1790217108.0
"""2026-09-23 20:31:48 MDT, as seconds since the epoch."""


@pytest.fixture
def make_frame() -> Any:
    """Return a function that builds a light frame.

    Returns
    -------
    make : `Callable`
        Builds a 60 second imaging frame with a measured star; fields can be
        overridden.
    """
    from wayfindinglib.models.session.capture_frame import CaptureFrame

    def make(**overrides: Any) -> Any:
        """Build the frame.

        Returns
        -------
        frame : `CaptureFrame`
            The frame.
        """
        fields: dict[str, Any] = {
            "path": "/f.fits",
            "target_id": "T",
            "timestamp": _NIGHT,
            "exposure_seconds": 60.0,
            "filter_name": "Luminance",
            "is_spectral": False,
            "star_width_arcsec": 5.0,
            "roundness": 0.9,
            "altitude_degrees": 50.0,
            "azimuth_degrees": 180.0,
            "pier_side": "WEST",
        }
        fields.update(overrides)
        return CaptureFrame(**fields)

    return make


def test_each_qualifying_frame_gives_a_width_and_a_roundness_sample(make_frame: Any) -> None:
    """Verify one frame becomes two samples with the same position."""
    samples, without_position = sky_samples_from_frames([make_frame()], 9.6)

    assert [s.metric for s in samples] == ["star_width", "star_roundness"]
    assert {s.altitude_degrees for s in samples} == {50.0}
    assert samples[0].night == "2026-09-23"
    assert without_position == 0


def test_short_spectral_and_unmeasured_frames_give_no_sample(make_frame: Any) -> None:
    """Verify only long imaging frames with a star measurement count."""
    frames = [
        make_frame(exposure_seconds=2.0),
        make_frame(is_spectral=True),
        make_frame(star_width_arcsec=None, roundness=None),
    ]

    assert sky_samples_from_frames(frames, 9.6) == ([], 0)


def test_a_frame_with_no_position_is_counted_not_used(make_frame: Any) -> None:
    """Verify an unpositioned frame is reported as left out."""
    frame = make_frame(altitude_degrees=None, azimuth_degrees=None, pier_side=None)

    assert sky_samples_from_frames([frame], 9.6) == ([], 1)


def test_equipment_that_has_not_guided_has_no_star_samples(make_frame: Any) -> None:
    """Verify without a guide cycle there is no exposure cut, so no samples."""
    assert sky_samples_from_frames([make_frame()], None) == ([], 0)


def _analysis(night: str, match: str = "exact", **quality: Any) -> GuidingSessionAnalysis:
    """Build a night's guiding analysis with two runs.

    Returns
    -------
    analysis : `GuidingSessionAnalysis`
        A night with one positioned run, one run with no position and one
        run with no error.
    """
    runs = [
        GuidingRunPerformance(
            run_id="a", started_at=0.0, rms_per_axis_arcsec=1.0, altitude_degrees=40.0, pierSide="West"
        ),
        GuidingRunPerformance(run_id="b", started_at=1.0, rms_per_axis_arcsec=1.2),
        GuidingRunPerformance(run_id="c", started_at=2.0, altitude_degrees=40.0),
    ]
    return GuidingSessionAnalysis(
        session_id=night,
        equipment_fingerprint="f",
        input_quality=GuidingInputQuality(limits_equipment_match=match, **quality),
        performance=GuidingPerformance(runs=runs),
    )


def test_a_trustworthy_nights_runs_become_guiding_error_samples() -> None:
    """Verify runs with an error and a position are used, others counted."""
    samples, without_position, excluded = sky_samples_from_guiding([_analysis("2026-01-01")])

    assert [(s.metric, s.value, s.pier_side) for s in samples] == [("guiding_error", 1.0, "West")]
    assert (without_position, excluded) == (1, 0)


def test_nights_with_bad_guiding_data_are_left_out() -> None:
    """Verify an impossible calibration or a weak star excludes the night."""
    analyses = [
        _analysis("2026-01-01", calibration_problems=["x"]),
        _analysis("2026-01-02", has_low_signal=True),
        _analysis("2026-01-03"),
    ]

    samples, _, excluded = sky_samples_from_guiding(analyses)

    assert excluded == 2
    assert {s.night for s in samples} == {"2026-01-03"}


def test_a_night_on_other_equipment_is_skipped_quietly() -> None:
    """Verify other equipment is neither used nor counted as excluded."""
    assert sky_samples_from_guiding([_analysis("2026-01-01", match="none")]) == ([], 0, 0)


def test_exposure_lengths_in_use_need_enough_frames_and_enough_length(make_frame: Any) -> None:
    """Verify tests and too-short exposures are not lengths in use."""
    frames = [make_frame(exposure_seconds=300.0) for _ in range(12)]
    frames += [make_frame(exposure_seconds=60.0) for _ in range(15)]
    frames += [make_frame(exposure_seconds=120.0) for _ in range(3)]
    frames += [make_frame(exposure_seconds=2.0) for _ in range(40)]

    assert exposure_lengths_in_use(frames, 9.6) == [60.0, 300.0]


def test_only_the_most_used_lengths_are_kept(make_frame: Any) -> None:
    """Verify at most six lengths are reported, the most used ones."""
    frames = []
    for index, length in enumerate([20, 30, 40, 50, 60, 70, 80, 90]):
        frames += [make_frame(exposure_seconds=float(length)) for _ in range(10 + index)]

    lengths = exposure_lengths_in_use(frames, 9.6)

    assert lengths == [40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
