"""Tests for the stacking values sized to the target (#175, Phase E).

The flat-match disc set aside for the target and the smoothing width used to
find an extended target are each the larger of a smallest value and a share
of the target's catalog size (the run's target-size basis). A target with no
catalog size keeps the value chosen on the reference setup.
"""

from collections.abc import Callable

import pytest

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES
from astrometricslib.models.tuned_parameter import RunBasis
from astrometricslib.pipelines.stacking.post_processing.flat_match import target_mask_radius_cells
from astrometricslib.pipelines.stacking.processing.group_alignment import (
    extended_target_smoothing_sigma_pixels,
)

ARCSEC_PER_ARCMIN = 60.0


def _with_target_size(size_arcmin: float | None, function: Callable[[], float]) -> float:
    """Run a sizing function in a run that knows only the target's size.

    Parameters
    ----------
    size_arcmin : `float` or `None`
        The catalog size, or `None` for a target with none.
    function : `Callable` [[], `float`]
        The sizing function.

    Returns
    -------
    value : `float`
        What the function gives.
    """
    with parameter_context():
        if size_arcmin is not None:
            record_basis(RunBasis.TARGET_SIZE_ARCSEC, size_arcmin * ARCSEC_PER_ARCMIN)
        return function()


def test_disc_with_no_target_size_is_the_smallest_disc() -> None:
    """A star (no catalog size) keeps the 25-cell disc chosen on M 57."""
    assert _with_target_size(None, target_mask_radius_cells) == pytest.approx(25.0)


def test_disc_for_a_small_target_is_the_smallest_disc() -> None:
    """M 57 (1.15 arcmin) is well inside the smallest disc, which stands."""
    assert _with_target_size(1.153, target_mask_radius_cells) == pytest.approx(25.0)


def test_disc_grows_with_a_large_target() -> None:
    """M 13 (33 arcmin) gets a disc of 1.25 times its radius, in cells."""
    scale = REFERENCE_SETUP_BASES[RunBasis.PLATE_SCALE_ARCSEC_PER_PX]
    expected = 1.25 * 33.0 * ARCSEC_PER_ARCMIN / 2.0 / scale / 8.0
    assert _with_target_size(33.0, target_mask_radius_cells) == pytest.approx(expected)


def test_disc_for_a_target_larger_than_the_frame_is_held_to_half_the_field() -> None:
    """M 31 (200 arcmin) is held to half of half the image's short side."""
    expected = 0.5 * REFERENCE_SETUP_BASES[RunBasis.IMAGE_SHORT_SIDE_PX] / 2.0 / 8.0
    assert _with_target_size(199.53, target_mask_radius_cells) == pytest.approx(expected)


def test_smoothing_with_no_target_size_is_the_m13_width() -> None:
    """A target with no catalog size keeps the 25 px chosen on M 13."""
    assert _with_target_size(None, extended_target_smoothing_sigma_pixels) == pytest.approx(25.0)


def test_smoothing_for_m13_gives_back_the_width_it_was_chosen_with() -> None:
    """M 13's catalog size on the reference setup gives 25 px again."""
    assert _with_target_size(33.0, extended_target_smoothing_sigma_pixels) == pytest.approx(25.0, rel=1e-3)


def test_smoothing_for_a_small_target_stops_at_the_floor() -> None:
    """M 57 would give under 1 px; the floor of 4 star widths stands."""
    floor = 4.0 * REFERENCE_SETUP_BASES[RunBasis.STAR_WIDTH_PX]
    assert _with_target_size(1.153, extended_target_smoothing_sigma_pixels) == pytest.approx(floor)


def test_smoothing_floor_follows_the_star_width() -> None:
    """With stars twice as wide, the floor doubles."""
    star_width = REFERENCE_SETUP_BASES[RunBasis.STAR_WIDTH_PX]
    with parameter_context():
        record_basis(RunBasis.TARGET_SIZE_ARCSEC, 1.153 * ARCSEC_PER_ARCMIN)
        record_basis(RunBasis.STAR_WIDTH_PX, 2.0 * star_width)
        sigma = extended_target_smoothing_sigma_pixels()
    assert sigma == pytest.approx(8.0 * star_width)
