"""Purpose: Unit tests for removing saturated points from a light curve.

Description: Verifies that `normalize_light_curves` takes the points where a
star was saturated out of that star's light curve. The tests check that the
points are gone from every per-frame array and the arrays stay aligned, that
the statistics are computed without them, that each star and the run count
what was removed, that a star saturated in every frame has no light curve but
says why and is still in the run's warnings, and that a run with no
saturation is unchanged.
"""

import logging
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_SATURATED_POINTS_REMOVED_COUNT,
    PHOTOMETRY_STARS_WITH_SATURATED_POINTS_COUNT,
)
from astrometricslib.pipelines.photometry.post_processing.record_quality import (
    PhotometryRunInputs,
    photometry_run_measurements,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    VariabilityAnalyzer,
    median_light_curve_scatter_mag,
)
from astrometricslib.pipelines.photometry.run_warnings import collect_logged_warnings
from astrometricslib.pipelines.photometry.test.conformance_fixture import (
    photometry_fixture_gates,
    photometry_fixture_inputs,
    photometry_fixture_metrics,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext

_STAR_COUNT = 40
_FRAME_COUNT = 30
_TARGET_INDEX = 20
_SATURATED_FRAMES = (3, 4, 5, 17)

# What a saturated frame reads for the target star: far above its true level,
# so a statistic that kept the point would show it.
_SATURATED_FLUX = 5000.0
_TRUE_FLUX = 2000.0


def _timestamps() -> list[datetime]:
    """Make the frame times.

    Returns
    -------
    timestamps : `list` [`datetime.datetime`]
        One exposure start every 5 minutes.
    """
    first = datetime(2026, 7, 20, 22, 0, tzinfo=UTC)
    return [first + timedelta(minutes=5 * index) for index in range(_FRAME_COUNT)]


def _build_stars(
    saturated_frames: tuple[int, ...], target_always_saturated: bool = False
) -> list[StellarObject]:
    """Build a field of steady stars; the target star saturates in some frames.

    Parameters
    ----------
    saturated_frames : `tuple` [`int`]
        The frames in which the target star is flagged saturated, with an
        inflated flux there.
    target_always_saturated : `bool`, optional
        Flag the target star in every frame.

    Returns
    -------
    stars : `list` [`StellarObject`]
        ``Star_0`` is the brightest. ``Star_20`` is the target star. Every
        star has errors, airmasses and BJD times, one per frame.
    """
    timestamps = _timestamps()
    stars = []
    for index in range(_STAR_COUNT):
        star = StellarObject(id=f"Star_{index}")
        flux = _TRUE_FLUX if index == _TARGET_INDEX else float(_STAR_COUNT - index) * 100.0 + 5000.0
        # A small repeating wiggle so the statistics are not degenerate.
        fluxes = [flux * (1.0 + 0.001 * ((frame % 5) - 2)) for frame in range(_FRAME_COUNT)]
        flags = [False] * _FRAME_COUNT
        if index == _TARGET_INDEX:
            for frame in range(_FRAME_COUNT) if target_always_saturated else saturated_frames:
                flags[frame] = True
                fluxes[frame] = _SATURATED_FLUX
        star.photometry = PhotometryResult(
            timestamps=list(timestamps),
            fluxes=fluxes,
            is_saturated=flags,
            airmasses=[1.0 + 0.01 * frame for frame in range(_FRAME_COUNT)],
            flux_errors=[1.0] * _FRAME_COUNT,
            time_bjd_tdb=[2461000.0 + 0.003 * frame for frame in range(_FRAME_COUNT)],
            time_basis="BJD_TDB",
        )
        stars.append(star)
    return stars


def _run(stars: list[StellarObject]) -> VariabilityAnalyzer:
    """Normalize the stars, detrend them and flag variables.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer after the three steps.
    """
    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {
        stamp: f"frame_{index:03d}.fits" for index, stamp in enumerate(_timestamps())
    }
    analyzer.total_frames = _FRAME_COUNT
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()
    analyzer.identify_variable_stars()
    return analyzer


def test_saturated_points_leave_every_per_frame_array_and_the_arrays_stay_aligned() -> None:
    """Verify the saturated frames leave all the target star's arrays."""
    stars = _build_stars(_SATURATED_FRAMES)
    original_timestamps = list(stars[_TARGET_INDEX].photometry.timestamps)

    _run(stars)

    light_curve = stars[_TARGET_INDEX].photometry
    kept = _FRAME_COUNT - len(_SATURATED_FRAMES)
    removed_times = {original_timestamps[frame] for frame in _SATURATED_FRAMES}
    assert not removed_times & set(light_curve.timestamps)
    for array in (
        light_curve.timestamps,
        light_curve.fluxes,
        light_curve.fluxes_normalized,
        light_curve.fluxes_detrended,
        light_curve.is_saturated,
        light_curve.airmasses,
        light_curve.flux_errors,
        light_curve.fluxes_normalized_errors,
        light_curve.fluxes_detrended_errors,
        light_curve.time_bjd_tdb,
    ):
        assert len(array) == kept
    assert not any(light_curve.is_saturated)
    expected_airmasses = [
        1.0 + 0.01 * frame for frame in range(_FRAME_COUNT) if frame not in _SATURATED_FRAMES
    ]
    assert light_curve.airmasses == pytest.approx(expected_airmasses)
    assert light_curve.time_basis == "BJD_TDB"


def test_statistics_are_computed_without_the_saturated_points() -> None:
    """Verify the star's statistics ignore the removed points."""
    saturated_field = _build_stars(_SATURATED_FRAMES)
    clean_field = _build_stars(())

    _run(saturated_field)
    _run(clean_field)
    clean_target = clean_field[_TARGET_INDEX]

    target = saturated_field[_TARGET_INDEX].photometry
    # The saturated flux (2.5 times the true level) would lift the scatter far
    # above the wiggle of 0.1 percent if it were kept.
    assert target.mean_flux == pytest.approx(clean_target.photometry.mean_flux, rel=0.01)
    assert target.coefficient_of_variation is not None
    assert target.coefficient_of_variation < 0.01
    assert target.coefficient_of_variation == pytest.approx(
        clean_target.photometry.coefficient_of_variation, abs=0.002
    )
    assert max(target.fluxes) < _SATURATED_FLUX
    assert median_light_curve_scatter_mag(saturated_field) < 0.01


def test_each_star_records_how_many_points_were_removed() -> None:
    """Verify the per-star count and the input-quality fraction."""
    stars = _build_stars(_SATURATED_FRAMES)

    _run(stars)

    target = stars[_TARGET_INDEX].photometry
    assert target.saturated_points_removed == len(_SATURATED_FRAMES)
    assert target.light_curve_withheld_reason is None
    assert target.input_quality.saturated_fraction == pytest.approx(len(_SATURATED_FRAMES) / _FRAME_COUNT)
    assert all(
        star.photometry.saturated_points_removed == 0 for star in stars if star is not stars[_TARGET_INDEX]
    )


def test_the_run_counts_are_emitted_as_measurements() -> None:
    """Verify the run-level counts reach the stored measurements."""
    inputs = photometry_fixture_inputs()
    context = PipelineRunContext(
        "M 99", "photometry", "1.3.0", "job-1", "job-1", datetime(2026, 10, 22, tzinfo=UTC)
    )

    measurements = photometry_run_measurements(
        photometry_fixture_metrics(), photometry_fixture_gates(), inputs, context
    )

    values = {measurement.metric: measurement.value for measurement in measurements}
    assert values[PHOTOMETRY_SATURATED_POINTS_REMOVED_COUNT] == pytest.approx(12.0)
    assert values[PHOTOMETRY_STARS_WITH_SATURATED_POINTS_COUNT] == pytest.approx(3.0)
    assert PhotometryRunInputs().saturated_points_removed is None


def test_a_star_saturated_in_every_frame_has_no_light_curve_but_says_why() -> None:
    """Verify an all-saturated star stays listed with a reason."""
    stars = _build_stars((), target_always_saturated=True)

    _run(stars)

    assert stars[_TARGET_INDEX] in stars
    target = stars[_TARGET_INDEX].photometry
    assert target.timestamps == []
    assert target.fluxes == []
    assert target.fluxes_normalized == []
    assert target.saturated_points_removed == _FRAME_COUNT
    assert "saturated in every frame" in target.light_curve_withheld_reason
    assert target.mean_flux is None
    assert target.coefficient_of_variation is None
    assert target.input_quality.saturated_fraction == pytest.approx(1.0)
    assert target.input_quality.frames_measured == 0


def test_a_star_left_with_too_few_points_gets_no_statistics_or_search() -> None:
    """Verify a star left with two points has no scatter and no search."""
    stars = _build_stars(tuple(range(_FRAME_COUNT - 2)))

    _run(stars)

    target = stars[_TARGET_INDEX].photometry
    assert len(target.timestamps) == 2
    assert target.saturated_points_removed == _FRAME_COUNT - 2
    assert target.coefficient_of_variation is None
    assert not stars[_TARGET_INDEX].can_run_period_search


def test_the_removal_is_logged_as_a_warning_the_job_can_list(caplog: pytest.LogCaptureFixture) -> None:
    """Verify the run's warning collector receives the removal."""
    stars = _build_stars(_SATURATED_FRAMES)

    with collect_logged_warnings() as collector, caplog.at_level(logging.WARNING):
        _run(stars)

    texts = [warning.message for warning in collector.warnings()]
    assert any("removed 4 saturated point(s) from the light curves of 1 star(s)" in text for text in texts)


def test_a_run_with_no_saturation_is_unchanged() -> None:
    """Verify nothing is removed, counted or logged when no star saturates."""
    stars = _build_stars(())

    with collect_logged_warnings() as collector:
        _run(stars)

    for star in stars:
        light_curve = star.photometry
        assert light_curve.saturated_points_removed == 0
        assert light_curve.light_curve_withheld_reason is None
        assert len(light_curve.timestamps) == _FRAME_COUNT
        assert np.all(np.isfinite(light_curve.fluxes_normalized))
    assert not any("Saturation" in warning.message for warning in collector.warnings())
