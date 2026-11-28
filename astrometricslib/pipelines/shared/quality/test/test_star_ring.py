"""Tests of the ring analysis around a star, on synthetic stars.

Each test draws a Moffat star (and sometimes a ray, an elongation or a
neighbor) on a flat noisy sky, writes a FITS file, and checks what
`measure_star_profile_in_file` reports in its ``ring_*`` fields. The star
finder for the neighbor mask is the astrometry run's, as in the app.
"""

import math
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.star_profile import RingHarmonic, StarProfileMeasurement
from astrometricslib.pipelines.astrometry.processing.detections import find_star_centers
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    APERTURE_RADIUS_PX,
    CROWDING_MARGIN_PX,
    CROWDING_NEIGHBOR_PEAK_FRACTION,
    SKY_ANNULUS_INNER_RADIUS_PX,
    SKY_ANNULUS_OUTER_RADIUS_PX,
)
from astrometricslib.pipelines.shared.quality.star_profile import PhotometryGeometry
from astrometricslib.pipelines.shared.quality.star_profile import (
    measure_star_profile_in_file as _measure_profile,
)

SIZE = 201


def measure_star_profile_in_file(*args: Any, **kwargs: Any) -> StarProfileMeasurement:
    """Measure a star, passing the photometry geometry the API passes.

    Returns
    -------
    measurement : `StarProfileMeasurement`
        What `measure_star_profile_in_file` returns.
    """
    geometry = PhotometryGeometry(
        aperture_radius_px=float(resolve(APERTURE_RADIUS_PX).value),
        sky_inner_px=float(resolve(SKY_ANNULUS_INNER_RADIUS_PX).value),
        sky_outer_px=float(resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value),
        crowding_margin_px=float(resolve(CROWDING_MARGIN_PX).value),
        crowding_neighbor_peak_fraction=float(resolve(CROWDING_NEIGHBOR_PEAK_FRACTION).value),
    )
    return _measure_profile(*args, photometry_geometry=geometry, **kwargs)


SKY = 0.007
NOISE = 5e-5
CENTER = 100.0
BETA = 2.5
FWHM = 2.25


def _moffat(
    columns: np.ndarray, rows: np.ndarray, center: tuple[float, float], peak: float, squeeze_x: float = 1.0
) -> np.ndarray:
    """Draw a Moffat star, optionally squeezed along x.

    Parameters
    ----------
    columns, rows : `numpy.ndarray`
        The 0-based pixel grids.
    center : `tuple` [`float`, `float`]
        The star's 0-based column and row.
    peak : `float`
        The value at the center.
    squeeze_x : `float`, optional
        Distances along x are multiplied by this, so a value above 1 makes
        the star narrower along x, which elongates it along y.

    Returns
    -------
    image : `numpy.ndarray`
        The star.
    """
    alpha = FWHM / (2.0 * math.sqrt(2.0 ** (1.0 / BETA) - 1.0))
    square = ((columns - center[0]) * squeeze_x) ** 2 + (rows - center[1]) ** 2
    return peak * (1.0 + square / alpha**2) ** (-BETA)


def _ray(columns: np.ndarray, rows: np.ndarray, angle_deg: float, amplitude: float) -> np.ndarray:
    """Draw a one-sided ray from the star toward an angle.

    The angle runs from +y toward +x. The ray is 3 px wide (sigma 1.5) and
    runs from 7 to 30 px from the star.

    Returns
    -------
    image : `numpy.ndarray`
        The ray.
    """
    angle = math.radians(angle_deg)
    delta_x, delta_y = columns - CENTER, rows - CENTER
    along = delta_x * math.sin(angle) + delta_y * math.cos(angle)
    across = delta_x * math.cos(angle) - delta_y * math.sin(angle)
    return amplitude * np.exp(-(across**2) / (2.0 * 1.5**2)) * ((along > 7.0) & (along < 30.0))


def _write(
    stars: list[tuple[tuple[float, float], float]],
    seed: int = 1,
    clip: float | None = None,
    squeeze_x: float = 1.0,
    extra: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
) -> str:
    """Write a sky with Moffat stars to a FITS file.

    Parameters
    ----------
    stars : `list` of `tuple`
        Each star as its 0-based (column, row) and peak. Only the first is
        squeezed.
    seed : `int`, optional
        The noise seed.
    clip : `float`, optional
        A ceiling the pixel values are cut at.
    squeeze_x : `float`, optional
        See `_moffat`, for the first star.
    extra : callable, optional
        Adds more light, given the column and row grids.

    Returns
    -------
    path : `str`
        The file.
    """
    rng = np.random.default_rng(seed)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = SKY + rng.normal(0.0, NOISE, (SIZE, SIZE))
    for index, (center, peak) in enumerate(stars):
        image += _moffat(columns, rows, center, peak, squeeze_x if index == 0 else 1.0)
    if extra is not None:
        image += extra(columns, rows)
    if clip is not None:
        image = np.minimum(image, clip)
    path = Path(tempfile.mkdtemp()) / "ring.fits"
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)
    return str(path)


def _measure(path: str, use_finder: bool = True) -> StarProfileMeasurement:
    """Measure the star at the center of the synthetic image.

    Returns
    -------
    measurement : `StarProfileMeasurement`
        The measurement, with the neighbor mask when ``use_finder`` is set.
    """
    return measure_star_profile_in_file(
        path, CENTER + 1.0, CENTER + 1.0, find_neighbors=find_star_centers if use_finder else None
    )


def _term(measurement: StarProfileMeasurement, order: int) -> RingHarmonic:
    """Pick one Fourier term of a measurement.

    Returns
    -------
    term : `RingHarmonic`
        The term of the given order.
    """
    return next(term for term in measurement.ring_harmonics if term.order == order)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_round_star_has_no_significant_term(seed: int) -> None:
    """A round Moffat star on noise shows nothing at 3 sigma in any order."""
    result = _measure(_write([((CENTER, CENTER), 1.0)], seed=seed))
    assert result.ring_note is None
    assert [term.order for term in result.ring_harmonics] == list(range(1, 9))
    assert not any(term.significant for term in result.ring_harmonics)
    assert result.ring_masked_neighbor_count == 0


def test_ray_gives_a_significant_one_sided_term_at_its_angle() -> None:
    """A ray toward 150 degrees makes order 1 significant, pointing there."""
    path = _write([((CENTER, CENTER), 1.0)], extra=lambda columns, rows: _ray(columns, rows, 150.0, 3e-4))
    result = _measure(path)
    one_sided = _term(result, 1)
    assert one_sided.significant
    assert one_sided.significance > 5.0
    difference = (one_sided.direction_deg - 150.0 + 180.0) % 360.0 - 180.0
    assert abs(difference) < 15.0
    assert one_sided.amplitude_debiased <= one_sided.amplitude


def test_angle_runs_from_y_toward_x() -> None:
    """A ray along +x (to the right in the array) is at 90 degrees."""
    path = _write([((CENTER, CENTER), 1.0)], extra=lambda columns, rows: _ray(columns, rows, 90.0, 3e-4))
    difference = (_term(_measure(path), 1).direction_deg - 90.0 + 180.0) % 360.0 - 180.0
    assert abs(difference) < 15.0


def test_elongated_star_gives_an_oval_term() -> None:
    """A star stretched along y makes order 2 significant, along y."""
    result = _measure(_write([((CENTER, CENTER), 1.0)], squeeze_x=1.25))
    oval = _term(result, 2)
    assert oval.significant
    assert min(oval.direction_deg, 180.0 - oval.direction_deg) < 10.0
    assert oval.amplitude > 0.03


def test_neighbor_is_masked_and_its_one_sided_term_goes_away() -> None:
    """A neighbor in the ring gives order 1 until it is masked."""
    stars = [((CENTER, CENTER), 1.0), ((CENTER + 14.0, CENTER + 6.0), 0.03)]
    path = _write(stars)
    unmasked = _measure(path, use_finder=False)
    masked = _measure(path)
    assert unmasked.ring_masked_neighbor_count == 0
    assert _term(unmasked, 1).significant
    assert masked.ring_masked_neighbor_count == 1
    assert masked.ring_masked_fraction > 0.0
    neighbor = masked.ring_neighbors[0]
    assert neighbor.x == pytest.approx(CENTER + 15.0, abs=0.5)
    assert neighbor.y == pytest.approx(CENTER + 7.0, abs=0.5)
    assert neighbor.mask_radius_px > 2.0
    assert not _term(masked, 1).significant
    assert _term(masked, 1).amplitude < 0.2 * _term(unmasked, 1).amplitude


def test_brighter_neighbor_gets_a_bigger_mask() -> None:
    """The mask radius grows with the neighbor's brightness."""
    radii = []
    for peak in (0.01, 0.1):
        path = _write([((CENTER, CENTER), 1.0), ((CENTER + 14.0, CENTER + 6.0), peak)])
        radii.append(_measure(path).ring_neighbors[0].mask_radius_px)
    assert radii[1] > radii[0]


def test_clipped_star_ring_reaches_its_real_wings() -> None:
    """A clipped star's ring comes from the pixels, not the narrow fit."""
    result = _measure(_write([((CENTER, CENTER), 3.0)], clip=1.0))
    assert result.clipped
    fit = result.moffat
    assert fit.fitted
    alpha = fit.fwhm_px / (2.0 * math.sqrt(2.0 ** (1.0 / fit.beta) - 1.0))
    # Where the fitted Moffat falls to 2 sky noise, times the old margin.
    fit_radius = alpha * math.sqrt((fit.peak / (2.0 * result.sky_noise)) ** (1.0 / fit.beta) - 1.0)
    assert result.ring_outer_px > 1.5 * fit_radius
    assert result.ring_inner_px >= 4.0
    assert result.ring_outer_px - result.ring_inner_px >= 4.0


def test_bins_use_the_unmasked_pixels() -> None:
    """The bins hold the ring pixels the fit used, in 5 degree steps."""
    stars = [((CENTER, CENTER), 1.0), ((CENTER + 14.0, CENTER + 6.0), 0.03)]
    masked = _measure(_write(stars))
    unmasked = _measure(_write(stars), use_finder=False)
    assert len(masked.ring_bin_angle_deg) == len(masked.ring_bin_values) == len(masked.ring_bin_counts)
    assert masked.ring_bin_angle_deg[0] == pytest.approx(2.5)
    assert all(0.0 < angle < 360.0 for angle in masked.ring_bin_angle_deg)
    assert sum(masked.ring_bin_counts) < sum(unmasked.ring_bin_counts)
    removed = sum(neighbor.pixels_masked for neighbor in masked.ring_neighbors)
    assert sum(unmasked.ring_bin_counts) - sum(masked.ring_bin_counts) == removed
    assert masked.ring_masked_fraction == pytest.approx(removed / sum(unmasked.ring_bin_counts))


def test_noise_has_a_note_and_no_terms() -> None:
    """With no star, the ring has bins and a note but no terms."""
    result = measure_star_profile_in_file(_write([]), CENTER + 1.0, CENTER + 1.0)
    assert not result.star_found
    assert result.ring_harmonics == []
    assert result.ring_note is not None
    assert 0.0 < result.ring_inner_px < result.ring_outer_px
    assert len(result.ring_bin_values) > 0


def test_ring_stays_inside_the_image_edge() -> None:
    """A star near the edge gets a ring that stops at the edge."""
    result = measure_star_profile_in_file(
        _write([((20.0, CENTER), 1.0)]), 21.0, CENTER + 1.0, find_neighbors=find_star_centers
    )
    assert result.ring_outer_px <= 20.0
