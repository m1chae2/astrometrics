"""Purpose: Tests of the shared neighbor-masking helpers.

Description: Checks the clipped median, the nearest-neighbor search and the
neighbor mask on small arrays.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.shared.neighbor_masking import (
    NeighborField,
    clipped_median_and_sigma,
    nearest_neighbors,
    neighbor_mask,
)

_CLIP = {"minimum_count": 10, "maximum_passes": 5, "clip_sigma": 3.0}


def test_clipping_drops_bright_outliers_from_the_median() -> None:
    """Bright outlier pixels do not move the clipped median or its noise."""
    rng = np.random.default_rng(1)
    values = np.concatenate([rng.normal(100.0, 2.0, 400), np.full(20, 5000.0)])
    median, sigma, kept = clipped_median_and_sigma(values, **_CLIP)  # type: ignore[misc]
    assert median == pytest.approx(100.0, abs=0.5)
    assert sigma == pytest.approx(2.0, abs=0.3)
    assert kept < values.size


def test_clipping_needs_the_minimum_count() -> None:
    """Too few finite pixels give no result."""
    assert clipped_median_and_sigma(np.array([1.0, np.nan, 2.0]), **_CLIP) is None


def test_nearest_neighbors_reports_distance_ratio_and_count() -> None:
    """Each star learns its nearest neighbor, its flux ratio and the count."""
    positions = np.array([[0.0, 0.0], [3.0, 4.0], [100.0, 100.0]])
    fluxes = np.array([10.0, 5.0, 1.0])
    result = nearest_neighbors(positions, fluxes, 10.0)
    assert result.distance_px[0] == pytest.approx(5.0)
    assert result.flux_ratio[0] == pytest.approx(0.5)
    assert result.count.tolist() == [1, 1, 0]
    assert np.isnan(result.distance_px[2])


def test_field_around_excludes_the_star_itself() -> None:
    """The star at the point is left out, and the rest come nearest first."""
    field = NeighborField(np.array([[10.0, 10.0], [14.0, 10.0], [12.0, 10.0]]), np.ones(3))
    assert field.around(10.0, 10.0, 5.0, exclude_radius=1.0).tolist() == [2, 1]


def test_mask_covers_a_bright_neighbor_but_not_a_faint_one() -> None:
    """A neighbor well above the glow is masked; one under 5 sigma is not."""
    kwargs = {
        "star_width_px": 2.33,
        "target_peak": 2000.0,
        "sky_noise": 2.0,
        "minimum_signal_to_noise": 5.0,
        "mask_noise_multiple": 1.0,
        "beta": 3.0,
        "target_match_radius_px": 3.0,
    }
    mask, count = neighbor_mask((41, 41), (20.0, 20.0), [(30.0, 20.0, 3000.0)], **kwargs)
    assert count == 1 and mask[20, 30] and not mask[20, 20]
    mask, count = neighbor_mask((41, 41), (20.0, 20.0), [(30.0, 20.0, 5.0)], **kwargs)
    assert count == 0 and not mask.any()
