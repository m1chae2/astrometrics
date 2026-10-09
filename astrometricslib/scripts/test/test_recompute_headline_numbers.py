"""Tests for the independent recomputation of saved headline numbers.

An independent check must itself be checked, or its disagreements mean
nothing. The star-width method is tried on artificial stars of known width
with random sub-pixel positions and noise, to show its bias is small; the
other recomputations are tried on small known cases.
"""

import numpy as np
import pytest

from astrometricslib.scripts import recompute_headline_numbers as recompute


def star_field(fwhm: float, seed: int, moffat: bool = False, stars: int = 4) -> np.ndarray:
    """Build an image of stars of a known width.

    Parameters
    ----------
    fwhm : `float`
        The stars' true full width at half maximum, in pixels.
    seed : `int`
        Seed for the star positions and the noise.
    moffat : `bool`, optional
        Use a Moffat profile (wings) instead of a Gaussian.
    stars : `int`, optional
        How many stars.

    Returns
    -------
    image : `numpy.ndarray`
        The image: sky of 100 with noise of 3, stars with peaks of 3000.
    """
    generator = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:120, 0:150]
    image = generator.normal(100.0, 3.0, (120, 150))
    for index in range(stars):
        x, y = 30 + index * 30 + generator.uniform(-0.5, 0.5), 60 + generator.uniform(-0.5, 0.5)
        distance_squared = (xx - x) ** 2 + (yy - y) ** 2
        if moffat:
            alpha = fwhm / (2 * np.sqrt(2 ** (1 / 3.0) - 1))
            image += 3000.0 * (1 + distance_squared / alpha**2) ** -3.0
        else:
            image += 3000.0 * np.exp(-distance_squared / (2 * (fwhm / 2.355) ** 2))
    return image


@pytest.mark.parametrize("fwhm", [2.5, 3.3, 4.5, 6.5])
@pytest.mark.parametrize("moffat", [False, True])
def test_the_independent_width_is_close_to_the_truth_for_known_stars(fwhm: float, moffat: bool) -> None:
    """Artificial stars are measured within 15% of their true width."""
    widths = [recompute.independent_fwhm(star_field(fwhm, seed, moffat)) for seed in range(8)]
    measured = float(np.median([width for width in widths if width]))

    assert measured == pytest.approx(fwhm, rel=0.15)


def test_a_blank_or_starless_image_has_no_width() -> None:
    """With no star above the noise there is nothing to measure."""
    flat = np.random.default_rng(0).normal(100.0, 3.0, (80, 80))

    assert recompute.independent_fwhm(flat) is None
    assert recompute.independent_fwhm(np.full((80, 80), np.nan)) is None


def test_a_star_too_close_to_the_edge_is_not_measured() -> None:
    """The profile needs room around the star."""
    image = np.zeros((80, 80))
    image[5, 5] = 100.0

    assert recompute.radial_profile_fwhm(image, 5, 5) is None


def test_the_pixel_fractions_count_zero_and_saturated_pixels() -> None:
    """A tenth zero and a twentieth saturated gives those shares."""
    data = np.ones((100, 100))
    data[:10, :] = 0.0
    data[-5:, :] = 65000.0

    zero, saturated = recompute.pixel_fractions(data, 65000.0)

    assert zero == pytest.approx(0.10)
    assert saturated == pytest.approx(0.05)
    assert recompute.pixel_fractions(data, None)[1] is None


def test_a_colour_stack_is_judged_by_its_worst_channel() -> None:
    """One dead channel shows in the zero fraction."""
    data = np.ones((3, 50, 50))
    data[0] = 0.0

    assert recompute.pixel_fractions(data, None)[0] == pytest.approx(1.0)


def test_cv_and_mean_are_recomputed_from_the_positive_values() -> None:
    """Standard deviation over the mean, ignoring non-positive values."""
    result = recompute.recompute_cv([1.0, 2.0, 3.0, 0.0, -1.0])

    assert result is not None
    cv, mean = result
    assert mean == pytest.approx(2.0)
    assert cv == pytest.approx(np.std([1.0, 2.0, 3.0]) / 2.0)
    assert recompute.recompute_cv([1.0, 2.0]) is None


def test_a_fresh_periodogram_finds_the_period_of_a_sinusoid() -> None:
    """A clean 0.5 day sinusoid is found at 0.5 days."""
    times = np.linspace(0.0, 10.0, 400)
    flux = 1.0 + 0.1 * np.sin(2 * np.pi * times / 0.5)

    assert recompute.recompute_period(times, flux, 0.1, 2.0) == pytest.approx(0.5, rel=0.01)


def test_a_comparison_counts_agreements_and_lists_disagreements() -> None:
    """Each record is counted; only those beyond tolerance are listed."""
    comparison = recompute.Comparison("example")

    comparison.record("A", 1.0, 1.0, True)
    comparison.record("B", 1.0, 2.0, False)
    text = recompute.format_report([comparison])

    assert comparison.compared == 2
    assert comparison.disagreements == ["B: stored 1, recomputed 2"]
    assert "example: 2 compared, 1 disagree" in text
    assert "B: stored 1, recomputed 2" in text


def test_the_report_truncates_a_long_list_of_disagreements() -> None:
    """Only the first few are shown, with a count of the rest."""
    comparison = recompute.Comparison("many")
    for index in range(12):
        comparison.record(f"item{index}", 1.0, 2.0, False)

    text = recompute.format_report([comparison], maximum_listed=3)

    assert "and 9 more" in text
