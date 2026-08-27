"""Unit tests for saturation flagging in forced-aperture photometry.

Verifies _measure_flux_numpy and the per-frame worker's saturation
detection, which flags individual flux measurements as untrustworthy
when the star's own aperture (not the surrounding
background/annulus) is significantly saturated.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.variability_analyzer import (
    VariabilityAnalyzer,
    _calculate_frame_offset,
    _measure_aperture_flux,
    locate_star_centroid,
)


def test_measure_flux_numpy_unsaturated_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a normal, unsaturated star aperture is not flagged."""
    data = np.full((100, 100), 500.0)
    data[45:55, 45:55] += 3000.0  # bright but unsaturated star

    analyzer = VariabilityAnalyzer()
    flux, is_saturated = analyzer._measure_flux_numpy(data, 50, 50)
    assert flux > 0
    assert is_saturated is False


def test_measure_flux_numpy_saturated_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a star aperture mostly at the saturation ADU is flagged."""
    data = np.full((100, 100), 500.0)
    data[46:54, 46:54] = 65535.0  # saturated core within the 4px-radius aperture

    analyzer = VariabilityAnalyzer()
    _flux, is_saturated = analyzer._measure_flux_numpy(data, 50, 50)
    assert is_saturated is True


def test_measure_flux_numpy_out_of_bounds_returns_unsaturated_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an out-of-bounds star returns (0.0, False), not raises."""
    data = np.full((20, 20), 500.0)
    analyzer = VariabilityAnalyzer()
    flux, is_saturated = analyzer._measure_flux_numpy(data, 1, 1)
    assert flux == pytest.approx(0.0)
    assert is_saturated is False


def test_measure_aperture_flux_pins_sum_method_exact():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the exact flux value produced by sum_method="exact".

    photutils.aperture supports several ways to decide how much of a
    boundary pixel counts as "inside" a circular aperture ("center",
    "exact", "subpixel"), and they give slightly different answers right
    at the edge of the circle. This test uses a hard-edged square star
    (not a soft, star-like glow) so that edge-pixel handling actually
    matters, and pins the exact number photutils gives us today for
    "exact" (its own default method). If this test ever fails after an
    unrelated change, the most likely cause is someone changed which
    method is used -- that is a real, if small, change to every star's
    measured brightness and should be a deliberate decision, not an
    accident.
    """
    data = np.full((100, 100), 500.0)
    data[46:54, 46:54] = 1000.0  # hard-edged square, not a soft star glow

    flux, is_saturated = _measure_aperture_flux(data, 50, 50)
    assert flux == pytest.approx(23824.693920034817)
    assert is_saturated is False


def test_measure_aperture_flux_empty_annulus_uses_local_median():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the local-cutout-median fallback when the annulus is empty.

    A star near the edge of a small picture might not have any
    background ring (annulus) pixels to measure at all. When that
    happens, this function should fall back to the median brightness of
    the whole local cutout around the star.
    """
    data = np.full((9, 9), 500.0)
    data[2:7, 2:7] = 1000.0  # star block big enough that the local median is still background (500)

    flux, is_saturated = _measure_aperture_flux(data, 4, 4, cutout_radius=4)
    assert flux == pytest.approx(12500.0)
    assert is_saturated is False


def test_measure_aperture_flux_empty_annulus_honors_explicit_fallback():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an explicit fallback_background overrides the local median.

    This is the background the per-frame worker passes in (a frame-wide
    sampled median), which differs from the local-cutout-median fallback
    that the reference-frame flux measurements use when no
    fallback_background is given.
    """
    data = np.full((9, 9), 500.0)
    data[2:7, 2:7] = 1000.0

    flux, is_saturated = _measure_aperture_flux(data, 4, 4, cutout_radius=4, fallback_background=100.0)
    assert flux == pytest.approx(32606.192982974677)
    assert is_saturated is False


def test_locate_star_centroid_finds_shifted_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a star shifted from its expected position is re-located."""
    data = np.full((200, 200), 500.0)
    data[63:68, 73:78] += 4000.0  # true centroid near (75, 65)

    located = locate_star_centroid(data, expected_x=70.0, expected_y=60.0)
    assert located is not None
    located_x, located_y = located
    assert abs(located_x - 75.0) < 1.0
    assert abs(located_y - 65.0) < 1.0


def test_locate_star_centroid_returns_none_outside_frame():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a search window falling off the frame edge returns None."""
    data = np.full((50, 50), 500.0)
    assert locate_star_centroid(data, expected_x=5.0, expected_y=5.0) is None


def test_locate_star_centroid_returns_none_with_no_signal():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a flat, background-only window returns None."""
    data = np.full((200, 200), 500.0)
    assert locate_star_centroid(data, expected_x=100.0, expected_y=100.0) is None


def test_calculate_frame_offset_recovers_uniform_shift():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the median offset matches a known shift across many stars.

    Uses a known uniform frame-to-frame shift applied to every star.
    """
    data = np.full((600, 600), 500.0)
    true_shift_x, true_shift_y = 3.0, -2.0
    # Well-separated (>100px apart) so each star's search window
    # (default half-width 40) never overlaps another's injected signal.
    reference_positions = [
        (100.0, 100.0, 0.0),
        (300.0, 100.0, 0.0),
        (500.0, 100.0, 0.0),
        (100.0, 300.0, 0.0),
        (300.0, 300.0, 0.0),
        (500.0, 500.0, 0.0),
    ]
    for reference_x, reference_y, _ in reference_positions:
        x_center = round(reference_x + true_shift_x)
        y_center = round(reference_y + true_shift_y)
        data[y_center - 2 : y_center + 3, x_center - 2 : x_center + 3] += 4000.0

    delta_x, delta_y = _calculate_frame_offset(data, reference_positions)
    assert abs(delta_x - true_shift_x) < 0.5
    assert abs(delta_y - true_shift_y) < 0.5


def test_calculate_frame_offset_returns_zero_when_too_few_stars_located():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the offset defaults to (0, 0) when too few stars are located.

    Fewer than 5 reference stars re-located (e.g. all fell on empty
    background) should not produce a spurious shift estimate.
    """
    data = np.full((100, 100), 500.0)
    reference_positions = [(20.0, 20.0, 0.0), (40.0, 40.0, 0.0)]
    assert _calculate_frame_offset(data, reference_positions) == (0.0, 0.0)
