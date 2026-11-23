"""Tests for the checks that keep instrument effects from being flagged.

The light curves are built to look like the M 52 run of 2026-10-03: 42 frames
whose shift against the first frame jumps at frames 8, 18, 28 and 38, with
four stars that dim by several percent exactly across the jump at frame 28.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.processing.instrumental_artifacts import (
    coverage_reason,
    edge_reason,
    field_step_summary,
    registration_step_threshold_px,
    step_artifact_reason,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    VariabilityAnalyzer,
    flag_variable_stars,
)

FRAME_COUNT = 42
STEP_FRAMES = (8, 18, 28, 38)


def stepping_shifts(seed: int = 1) -> list[tuple[float, float]]:
    """Build the frame shifts of a night with four jumps of the field.

    Parameters
    ----------
    seed : `int`, optional
        Seed of the frame-to-frame jitter (0.2 px).

    Returns
    -------
    shifts : `list` [`tuple` [`float`, `float`]]
        One ``(x, y)`` shift per frame, in pixels.
    """
    rng = np.random.default_rng(seed)
    level = np.zeros(FRAME_COUNT)
    for frame in STEP_FRAMES:
        level[frame:] += 3.0
    jitter = rng.normal(0.0, 0.2, (FRAME_COUNT, 2))
    return [(float(level[i] + jitter[i, 0]), float(jitter[i, 1])) for i in range(FRAME_COUNT)]


def noisy_curve(seed: int, sigma: float = 0.007) -> np.ndarray:
    """Build a constant light curve with white noise.

    Parameters
    ----------
    seed : `int`
        Seed of the noise.
    sigma : `float`, optional
        The noise as a fraction of the level.

    Returns
    -------
    values : `numpy.ndarray`
        The light curve, level 1.
    """
    return 1.0 + np.random.default_rng(seed).normal(0.0, sigma, FRAME_COUNT)


def test_the_step_threshold_follows_the_jitter_and_has_a_floor() -> None:
    """A steady mount gives the floor; a restless one a higher limit."""
    steady = [(0.0, 0.0)] * 30
    assert registration_step_threshold_px(steady) == pytest.approx(1.0)
    shifts = stepping_shifts()
    assert 1.0 <= registration_step_threshold_px(shifts) < 2.0
    assert registration_step_threshold_px(shifts[:2]) == pytest.approx(1.0)


def test_a_dip_that_starts_and_ends_at_registration_steps_is_an_artifact() -> None:
    """A star 7 percent faint between two steps is put down to the steps."""
    shifts = stepping_shifts()
    values = noisy_curve(2)
    values[28:38] -= 0.07
    reason = step_artifact_reason(values, shifts, registration_step_threshold_px(shifts))
    assert reason is not None
    assert "registration steps" in reason


def test_an_eclipse_between_steps_is_not_an_artifact() -> None:
    """A deep dip inside one group of frames has no jump at a step."""
    shifts = stepping_shifts()
    values = noisy_curve(3)
    values[12:15] -= 0.10
    assert step_artifact_reason(values, shifts, registration_step_threshold_px(shifts)) is None


def test_a_smooth_variable_is_not_an_artifact() -> None:
    """A sinusoid changes as much inside a group as across a step."""
    shifts = stepping_shifts()
    values = 1.0 + 0.04 * np.sin(2.0 * np.pi * np.arange(FRAME_COUNT) / 17.0) + noisy_curve(4, 0.004) - 1.0
    assert step_artifact_reason(values, shifts, registration_step_threshold_px(shifts)) is None


def test_a_constant_star_and_a_curve_without_steps_pass() -> None:
    """Noise gives no verdict, and neither does a night with no steps."""
    shifts = stepping_shifts()
    threshold = registration_step_threshold_px(shifts)
    assert step_artifact_reason(noisy_curve(5), shifts, threshold) is None
    dipping = noisy_curve(6)
    dipping[28:38] -= 0.07
    assert step_artifact_reason(dipping, [(0.1 * i % 0.4, 0.0) for i in range(FRAME_COUNT)], 1.5) is None


def test_short_or_mismatched_curves_give_no_verdict() -> None:
    """The check does not guess from fewer points than two groups need."""
    shifts = stepping_shifts()
    assert step_artifact_reason([1.0] * 5, shifts[:5], 1.5) is None
    assert step_artifact_reason([1.0] * 10, shifts, 1.5) is None


@pytest.mark.parametrize(
    ("fraction", "expected_reason"),
    [(1.0, False), (0.9, False), (0.83, True), (0.48, True), (None, False)],
)
def test_coverage_requires_nine_in_ten_unsaturated_frames(
    fraction: float | None, expected_reason: bool
) -> None:
    """A star saturated in over a tenth of its frames is not judged."""
    reason = coverage_reason(fraction)
    assert (reason is not None) is expected_reason
    if reason:
        assert "saturated" in reason


def test_edge_distance_is_the_sky_ring_plus_the_largest_shift() -> None:
    """A star 15 px from an edge fails when the field moved 7 px."""
    shape = (3008, 3008)
    assert edge_reason(1500.0, 1500.0, shape, 7.0) is None
    assert edge_reason(40.0, 1500.0, shape, 7.0) is None
    reason = edge_reason(15.0, 1500.0, shape, 7.0)
    assert reason is not None
    assert "15 px from the frame edge" in reason
    assert edge_reason(None, 1500.0, shape, 7.0) is None
    assert edge_reason(1500.0, 3000.0, shape, 7.0) is not None


def make_analyzer_star(
    star_id: str, values: np.ndarray, timestamps: list[datetime], saturated_removed: int = 0
) -> StellarObject:
    """Build a star with a finished light curve for the analyzer.

    Parameters
    ----------
    star_id : `str`
        The star's id.
    values : `numpy.ndarray`
        The detrended brightness at each timestamp.
    timestamps : `list` [`datetime.datetime`]
        The frame times.
    saturated_removed : `int`, optional
        How many saturated points were removed from the star.

    Returns
    -------
    star : `StellarObject`
        The star, in the middle of a 3008 by 3008 frame.
    """
    star = StellarObject(id=star_id)
    star.star_data = {"x_centroid": 1500.0, "y_centroid": 1500.0}
    star.photometry = PhotometryResult(
        timestamps=list(timestamps),
        fluxes=[1000.0] * len(timestamps),
        fluxes_normalized=list(values),
        fluxes_detrended=list(values),
        is_saturated=[False] * len(timestamps),
        saturated_points_removed=saturated_removed,
    )
    return star


def test_the_analyzer_marks_the_stars_it_cannot_judge_and_keeps_their_curves() -> None:
    """Each failing star gets a reason, the clean one none; no curve is cut."""
    start = datetime(2026, 10, 3, 4, 20, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=130 * i) for i in range(FRAME_COUNT)]
    analyzer = VariabilityAnalyzer()
    analyzer.reference_frame_shape = (3008, 3008)
    analyzer.frame_registration_drift = dict(zip(timestamps, stepping_shifts(), strict=True))
    stepped = noisy_curve(7)
    stepped[28:38] -= 0.07
    clean = make_analyzer_star("clean", noisy_curve(8), timestamps)
    stepping = make_analyzer_star("stepping", stepped, timestamps)
    saturating = make_analyzer_star("saturating", noisy_curve(9), timestamps, saturated_removed=20)
    edge = make_analyzer_star("edge", noisy_curve(10), timestamps)
    edge.star_data = {"x_centroid": 12.0, "y_centroid": 1500.0}
    analyzer.stellar_objects = [clean, stepping, saturating, edge]

    analyzer._judge_light_curves()

    assert clean.photometry.not_judged_reason is None
    assert "registration steps" in (stepping.photometry.not_judged_reason or "")
    assert "saturated" in (saturating.photometry.not_judged_reason or "")
    assert "frame edge" in (edge.photometry.not_judged_reason or "")
    assert all(len(star.photometry.fluxes_detrended) == FRAME_COUNT for star in analyzer.stellar_objects)


def test_a_star_that_is_not_judged_is_never_a_candidate() -> None:
    """On a small field, the CV rule skips a not-judged star."""
    start = datetime(2026, 10, 3, 4, 20, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=130 * i) for i in range(FRAME_COUNT)]
    stars = [make_analyzer_star(f"constant-{i}", noisy_curve(20 + i, 0.004), timestamps) for i in range(8)]
    varying = make_analyzer_star("varying", 1.0 + 0.2 * np.sin(np.arange(FRAME_COUNT) / 3.0), timestamps)
    for star in [*stars, varying]:
        values = np.asarray(star.photometry.fluxes_detrended)
        star.photometry.coefficient_of_variation = float(values.std() / values.mean())
    flagged, _ = flag_variable_stars([*stars, varying], 5.0)
    assert varying in flagged
    varying.photometry.not_judged_reason = "Not judged for variability: test"
    flagged, _ = flag_variable_stars([*stars, varying], 5.0)
    assert varying not in flagged


def field_with_common_step(deviant_dip: float = 0.0, star_count: int = 60) -> tuple[list, list, list, dict]:
    """Build a field whose every star drops 2 percent at the frame-28 step.

    Parameters
    ----------
    deviant_dip : `float`, optional
        Extra dip of star 0 between frames 28 and 38, as a fraction.
    star_count : `int`, optional
        Number of stars.

    Returns
    -------
    curves, times, shifts, field_steps : `tuple`
        The light curves, the frame times (frame numbers), the shifts and the
        field's summary of each step.
    """
    shifts = stepping_shifts()
    times = list(range(FRAME_COUNT))
    curves = []
    for seed in range(star_count):
        values = noisy_curve(100 + seed, 0.002)
        values[28:] -= 0.02
        if seed == 0:
            values[28:38] -= deviant_dip
        curves.append(values)
    threshold = registration_step_threshold_px(shifts)
    summary = field_step_summary([(c, times, shifts) for c in curves], threshold)
    return curves, times, shifts, summary


def test_a_field_wide_step_does_not_veto_the_stars_that_follow_it() -> None:
    """Every precise star drops 2 percent at the step, so nobody is vetoed."""
    curves, times, shifts, summary = field_with_common_step()
    threshold = registration_step_threshold_px(shifts)
    assert summary[28][0] == pytest.approx(-0.02, abs=0.003)
    own_error_only = [step_artifact_reason(c, shifts, threshold) for c in curves]
    assert sum(reason is not None for reason in own_error_only) > 30
    relative = [step_artifact_reason(c, shifts, threshold, times, summary) for c in curves]
    assert all(reason is None for reason in relative)


def test_one_star_that_differs_from_the_field_at_the_step_is_vetoed() -> None:
    """A star with an extra 6 percent dip across the steps still fails."""
    curves, times, shifts, summary = field_with_common_step(deviant_dip=0.06)
    threshold = registration_step_threshold_px(shifts)
    assert step_artifact_reason(curves[0], shifts, threshold, times, summary) is not None
    assert all(step_artifact_reason(c, shifts, threshold, times, summary) is None for c in curves[1:])


def test_a_small_field_is_judged_on_the_star_alone() -> None:
    """Fewer than ten stars give no field summary; the star stands alone."""
    _, _, _, summary = field_with_common_step(star_count=5)
    assert summary == {}
