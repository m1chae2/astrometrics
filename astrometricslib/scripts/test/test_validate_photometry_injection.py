"""Tests for the photometry injection-recovery script.

The signal builders are checked against values worked out by hand. The whole
procedure is checked on a synthetic field of constant stars with Gaussian
noise, where a deep signal must be found, a signal far below the noise must
not, and untouched stars must almost never be flagged.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.scripts import validate_photometry_injection as injection

START = datetime(2026, 1, 1, tzinfo=UTC)
POINTS = 40


def make_field(star_count: int = 120, seed: int = 4) -> list[StellarObject]:
    """Build constant stars with 1 percent noise and a range of brightness.

    Parameters
    ----------
    star_count : `int`, optional
        How many stars.
    seed : `int`, optional
        Seed of the noise.

    Returns
    -------
    stars : `list` [`StellarObject`]
        Stars with timestamps, fluxes, normalized fluxes and errors, as the
        photometry pipeline leaves them.
    """
    generator = np.random.default_rng(seed)
    stars = []
    for index in range(star_count):
        normalized = (1.0 + generator.normal(0.0, 0.01, POINTS)).tolist()
        flux = 500.0 * 2.0 ** (index % 8)
        star = StellarObject(id=f"S{index}")
        star.photometry = PhotometryResult(
            timestamps=[START + timedelta(minutes=5 * step) for step in range(POINTS)],
            fluxes=[flux * value for value in normalized],
            fluxes_normalized=normalized,
            fluxes_detrended=normalized,
            fluxes_normalized_errors=[0.01] * POINTS,
            fluxes_detrended_errors=[0.01] * POINTS,
            airmasses=[1.1] * POINTS,
            is_saturated=[False] * POINTS,
        )
        stars.append(star)
    return stars


def test_a_transit_dims_only_the_points_inside_it_by_its_depth() -> None:
    """A 0.1 mag transit gives a flux factor of 0.9120 over 20% of the span."""
    times = np.linspace(0.0, 1000.0, 101)

    factor = injection.signal_factor("transit", 0.1, times)

    inside = np.abs(times / 1000.0 - 0.5) <= injection.TRANSIT_DURATION_FRACTION / 2.0
    assert factor[inside] == pytest.approx(10.0 ** (-0.04))
    assert factor[~inside] == pytest.approx(1.0)
    assert 10.0 ** (-0.04) == pytest.approx(0.9120, abs=1e-4)


def test_a_sinusoid_swings_by_its_peak_to_peak_depth() -> None:
    """A 0.2 mag sinusoid has extremes 0.2 mag apart."""
    times = np.linspace(0.0, 1000.0, 4001)

    factor = injection.signal_factor("sinusoid", 0.2, times)

    swing_mag = -2.5 * np.log10(factor.max() / factor.min())
    assert abs(swing_mag) == pytest.approx(0.2, abs=1e-3)


def test_an_unknown_shape_is_refused() -> None:
    """Only the two shapes exist."""
    with pytest.raises(ValueError, match="signal shape"):
        injection.signal_factor("sawtooth", 0.1, np.linspace(0.0, 1.0, 5))


def test_injecting_scales_the_fluxes_and_their_errors_together() -> None:
    """Every flux series and error series is multiplied by the same factor."""
    star = make_field(1)[0]
    before = np.array(star.photometry.fluxes_normalized)
    errors_before = np.array(star.photometry.fluxes_normalized_errors)

    injection.inject_signal(star, "transit", 0.5)

    after = np.array(star.photometry.fluxes_normalized)
    ratio = after / before
    assert ratio.min() == pytest.approx(10.0 ** (-0.2))
    assert np.array(star.photometry.fluxes_normalized_errors) / errors_before == pytest.approx(ratio)
    assert np.array(star.photometry.fluxes_detrended) == pytest.approx(after)


def test_the_wilson_interval_brackets_the_proportion_and_stays_inside_zero_and_one() -> None:
    """8 of 10 at one sigma, worked by hand, and the ends at 0 and 1.

    The centre is (0.8 + 0.05) / 1.1 = 0.7727 and the half-width is
    sqrt(0.016 + 0.0025) / 1.1 = 0.1237.
    """
    low, high = injection.wilson_interval(8, 10)

    assert low < 0.8 < high
    assert low == pytest.approx(0.7727 - 0.1237, abs=1e-3)
    assert high == pytest.approx(0.7727 + 0.1237, abs=1e-3)
    assert injection.wilson_interval(0, 10)[0] == pytest.approx(0.0)
    assert injection.wilson_interval(10, 10)[1] == pytest.approx(1.0)


def test_a_deep_signal_is_found_and_a_buried_one_and_untouched_stars_are_not() -> None:
    """On constant stars with 1% noise, the completeness follows the depth.

    A 0.16 mag signal is 15 times the noise and must be found nearly always. A
    0.005 mag signal is half the noise and must be found rarely. Untouched
    stars must almost never be flagged.
    """
    result = injection.run_injection(make_field(), set(), realizations=40, seed=3)

    deep = [cell for key, cell in result.cells.items() if key[1] == pytest.approx(0.16)]
    shallow = [cell for key, cell in result.cells.items() if key[1] == pytest.approx(0.005)]
    deep_share = sum(c.flagged for c in deep) / sum(c.injected for c in deep)
    shallow_share = sum(c.flagged for c in shallow) / sum(c.injected for c in shallow)
    untouched_share = result.untouched.flagged / result.untouched.injected

    assert deep_share > 0.9
    assert shallow_share < 0.2
    assert untouched_share < 0.02
    assert result.hosts > 100


def test_the_report_has_a_table_for_each_shape_and_the_untouched_rate() -> None:
    """The text names both shapes, every depth and the untouched count."""
    result = injection.run_injection(make_field(), set(), realizations=10, seed=5)

    text = injection.format_report(result, "Synthetic")

    assert "sinusoid:" in text and "transit:" in text
    assert "0.160" in text and "0.005" in text
    assert "untouched stars flagged" in text
