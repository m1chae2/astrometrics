"""Purpose: Test negative fluxes and the signal-to-noise comparison pool.

Description: A faint star in a noisy frame can measure a little below zero.
The old code clamped that flux to zero, so the star dropped out of the
comparison pool (which needed a positive flux in every frame) and biased
its own mean upward. These tests check that:

* the aperture measurement keeps a negative net flux and its error;
* the flux table keeps a negative flux that has an error, and marks a star
  that was not measured (no error, as at the frame edge) as missing;
* the comparison pool takes a faint star with good signal-to-noise even when
  one frame measured it below zero, and turns away a noisy star;
* a star with no errors is still held to the old rule (positive in every
  frame);
* a negative point stays in a star's mean, scatter and indices, while the
  magnitude is left out for a point that is not above zero.
"""

import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import measure_aperture_photometry
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    MINIMUM_COMPARISON_SIGNAL_TO_NOISE,
    build_flux_table,
    comparison_pool_rules,
    describe_comparison_pool,
    describe_missing_comparison_stars,
    median_signal_to_noise,
    select_comparison_set,
)
from astrometricslib.pipelines.photometry.processing.target_amplitude import magnitudes_from_flux
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    flux_mean_and_coefficient_of_variation,
)
from astrometricslib.pipelines.photometry.processing.variability_indices import extract_series

FRAME_COUNT = 30
START = datetime(2026, 5, 24, 3, 0, 0)
ERROR = 10.0


def _star(
    name: str,
    signal_to_noise: float,
    *,
    errors: bool = True,
    edits: dict[int, float] | None = None,
    seed: int = 0,
) -> StellarObject:
    """Build a star with a flat light curve, noise and a flux error.

    Parameters
    ----------
    name : `str`
        The star's id.
    signal_to_noise : `float`
        The true flux divided by the error ``ERROR``.
    errors : `bool`, optional
        Whether the light curve carries errors.
    edits : `dict` [`int`, `float`], optional
        Frame index to a flux that replaces the noisy value.
    seed : `int`, optional
        Seed of the noise.

    Returns
    -------
    star : `StellarObject`
        The star, never saturated.
    """
    rng = np.random.default_rng(seed)
    flux = signal_to_noise * ERROR + rng.normal(0.0, ERROR, FRAME_COUNT)
    for frame, value in (edits or {}).items():
        flux[frame] = value
    star = StellarObject(id=name)
    star.photometry = PhotometryResult(
        timestamps=[START + timedelta(minutes=10 * k) for k in range(FRAME_COUNT)],
        fluxes=flux.tolist(),
        flux_errors=[ERROR] * FRAME_COUNT if errors else [],
        is_saturated=[False] * FRAME_COUNT,
        airmasses=[1.0] * FRAME_COUNT,
    )
    return star


def test_the_aperture_keeps_a_negative_net_flux_with_its_error() -> None:
    """A star in a hole of the sky measures below zero, with an error."""
    data = np.full((80, 80), 100.0)
    data[28:33, 38:43] = 80.0
    measurement = measure_aperture_photometry(data, 40.0, 30.0, saturation_threshold_adu=60000.0)

    assert measurement.net_flux_adu < 0
    assert measurement.flux_error_adu > 0


def test_the_flux_table_keeps_a_negative_flux_and_marks_a_missing_one() -> None:
    """A negative flux with an error stays; a flux with no error is missing."""
    steady = [_star(f"S{index}", 200.0, seed=index) for index in range(12)]
    faint = _star("faint", 60.0, edits={7: -4.0})
    edge = _star("edge", 200.0, seed=9)
    edge.photometry.fluxes[3] = 0.0
    edge.photometry.flux_errors[3] = 0.0

    table = build_flux_table([*steady, faint, edge])

    assert table is not None
    row = table.star_ids.index("faint")
    assert table.fluxes[row, 7] == pytest.approx(-4.0)
    assert table.errors[row, 7] == pytest.approx(ERROR)
    edge_row = table.star_ids.index("edge")
    assert math.isnan(table.fluxes[edge_row, 3])
    assert np.isfinite(table.fluxes[edge_row]).sum() == FRAME_COUNT - 1


def test_a_star_with_no_errors_still_needs_a_positive_flux_in_every_frame() -> None:
    """Without errors there is no signal-to-noise, so the old rule holds."""
    steady = [_star(f"S{index}", 200.0, seed=index) for index in range(12)]
    unmeasured = _star("no_errors", 200.0, errors=False, seed=5, edits={7: -4.0})
    clean = _star("no_errors_positive", 200.0, errors=False, seed=6)

    table = build_flux_table([*steady, unmeasured, clean])

    assert table is not None
    assert math.isnan(table.fluxes[table.star_ids.index("no_errors"), 7])
    measured = comparison_pool_rules(table)[0][1]
    assert not measured[table.star_ids.index("no_errors")]
    assert measured[table.star_ids.index("no_errors_positive")]


def test_the_signal_to_noise_pool_takes_a_faint_good_star_and_turns_away_a_noisy_one() -> None:
    """A star at S/N 60 with a frame below zero passes; one at 10 fails."""
    steady = [_star(f"S{index}", 300.0, seed=index) for index in range(10)]
    faint_good = _star("faint_good", 60.0, edits={7: -3.0}, seed=21)
    noisy = _star("noisy", 10.0, seed=22)

    table = build_flux_table([*steady, faint_good, noisy])

    assert table is not None
    rules = dict(comparison_pool_rules(table))
    good_row, noisy_row = table.star_ids.index("faint_good"), table.star_ids.index("noisy")
    assert rules["measured"][noisy_row]
    assert rules["measured"][good_row]
    assert rules["signal_to_noise"][good_row]
    assert not rules["signal_to_noise"][noisy_row]
    assert median_signal_to_noise(table)[good_row] >= MINIMUM_COMPARISON_SIGNAL_TO_NOISE.default
    selection = select_comparison_set(table)
    assert noisy_row not in selection.member_rows
    text = describe_comparison_pool(table, selection)
    assert "median signal-to-noise of at least 50" in text


def test_a_star_missing_from_a_few_frames_is_a_candidate_but_not_from_many() -> None:
    """Missing from 1 of 30 frames is allowed; missing from 5 of 30 is not."""
    steady = [_star(f"S{index}", 300.0, seed=index) for index in range(12)]
    few = _star("few_missing", 300.0, seed=31)
    many = _star("many_missing", 300.0, seed=32)
    for star, frames in ((few, [4]), (many, [1, 2, 3, 4, 5])):
        for frame in frames:
            star.photometry.fluxes[frame] = 0.0
            star.photometry.flux_errors[frame] = 0.0

    table = build_flux_table([*steady, few, many])

    assert table is not None
    measured = comparison_pool_rules(table)[0][1]
    assert measured[table.star_ids.index("few_missing")]
    assert not measured[table.star_ids.index("many_missing")]


def test_the_mean_and_scatter_keep_a_negative_point() -> None:
    """Dropping a point at or below zero would raise a faint star's mean."""
    values = [0.2, -0.1, 0.1, 0.3, -0.2, 0.2, 0.0, 0.4]
    photometry = PhotometryResult(fluxes_normalized=values)

    mean, coefficient = flux_mean_and_coefficient_of_variation(photometry) or (math.nan, math.nan)

    assert mean == pytest.approx(np.mean(values))
    assert coefficient > 1.0


def test_the_series_keeps_a_negative_point_and_a_nonpositive_mean_gives_none() -> None:
    """A series keeps every finite point; a mean of zero or less gives none."""
    positive_mean = StellarObject(id="faint")
    positive_mean.photometry = PhotometryResult(
        fluxes=[5.0, -1.0, 4.0, 6.0, 3.0, 5.0, 4.0, 5.0, 6.0, 4.0, 5.0, 4.0],
        fluxes_normalized=[0.5, -0.1, 0.4, 0.6, 0.3, 0.5, 0.4, 0.5, 0.6, 0.4, 0.5, 0.4],
        fluxes_normalized_errors=[0.1] * 12,
    )
    series = extract_series(positive_mean)

    assert series is not None
    assert series.values.size == 12
    assert series.values.min() < 0
    assert series.instrumental_magnitude is not None
    assert series.error_mag is not None

    negative_mean = StellarObject(id="empty")
    negative_mean.photometry = PhotometryResult(
        fluxes=[-1.0] * 12, fluxes_normalized=[-0.1, 0.0] * 6, fluxes_normalized_errors=[0.1] * 12
    )
    assert extract_series(negative_mean) is None


def test_the_magnitude_conversion_marks_a_flux_that_is_not_above_zero() -> None:
    """Magnitudes are only for positive flux; the rest come back as NaN."""
    magnitudes = magnitudes_from_flux(np.array([2.0, 1.0, 0.0, -0.5]))

    assert np.isfinite(magnitudes[:2]).all()
    assert np.isnan(magnitudes[2:]).all()


def test_a_session_where_no_star_is_measured_often_enough_says_why() -> None:
    """Every star misses 2 of 30 frames, so none has 95% and we say why."""
    stars = [_star(f"S{index}", 300.0, seed=index) for index in range(40)]
    for index, star in enumerate(stars):
        for frame in range(index % 28, index % 28 + 2):
            star.photometry.fluxes[frame] = 0.0
            star.photometry.flux_errors[frame] = 0.0

    table = build_flux_table(stars)

    assert table is not None
    selection = select_comparison_set(table)
    assert selection.member_rows == ()
    reason = describe_missing_comparison_stars(table, selection)
    assert reason.startswith("0 of 40 stars have a flux in at least 95%")
    assert "lack a flux" in reason
