"""Purpose: Tests of neighbor masking and crowding in aperture photometry.

Description: Builds a synthetic field with one star and neighbors at chosen
places. A neighbor in the sky ring lifts the sky and makes the star read too
faint until the ring leaves it out. A neighbor inside the circle makes the
star crowded. A crowded star is never chosen as a comparison star.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest
from photutils.aperture import CircularAnnulus, CircularAperture

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    ApertureMeasurement,
    detect_neighbor_field,
    measure_aperture_photometry,
)
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    build_flux_table,
    select_comparison_set,
)
from astrometricslib.pipelines.shared.neighbor_masking import NeighborField
from astrometricslib.pipelines.shared.quality.star_profile_fitting import detect_stars_without_shape_cut

SATURATION_ADU = 65000.0
SIZE = 121
CENTER = 60.0
SKY_ADU = 100.0
NOISE_ADU = 2.0
# The reference setup's star width, which is also the default basis.
SIGMA_PX = 2.33 / 2.3548
STAR_PEAK_ADU = 2000.0


def _star(rows: np.ndarray, columns: np.ndarray, x: float, y: float, peak: float) -> np.ndarray:
    """Give a Gaussian star of the reference width.

    Returns
    -------
    image : `numpy.ndarray`
        The star's light, in ADU above the sky.
    """
    return peak * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2.0 * SIGMA_PX**2))


def _field(neighbors: list[tuple[float, float, float]]) -> tuple[np.ndarray, NeighborField]:
    """Build a frame with the star at the center and the given neighbors.

    Returns
    -------
    data, field : `numpy.ndarray`, `NeighborField`
        The frame and the detection list (the star first, then the neighbors).
    """
    rows, columns = np.indices((SIZE, SIZE), dtype=float)
    data = SKY_ADU + np.random.default_rng(7).normal(0.0, NOISE_ADU, (SIZE, SIZE))
    data += _star(rows, columns, CENTER, CENTER, STAR_PEAK_ADU)
    detections = [(CENTER, CENTER, STAR_PEAK_ADU), *neighbors]
    for x, y, peak in neighbors:
        data += _star(rows, columns, x, y, peak)
    positions = np.array([[x, y] for x, y, _ in detections])
    peaks = np.array([peak for _, _, peak in detections])
    return data, NeighborField(positions, peaks)


def _measure(data: np.ndarray, field: NeighborField | None) -> ApertureMeasurement:
    """Measure the central star.

    Returns
    -------
    measurement : `ApertureMeasurement`
        The measurement, with the field's crowding when one is given.
    """
    return measure_aperture_photometry(
        data, CENTER, CENTER, saturation_threshold_adu=SATURATION_ADU, neighbors=field
    )


def _plain_median_flux(data: np.ndarray) -> float:
    """Measure the central star the old way: the plain median of the ring.

    Returns
    -------
    flux : `float`
        The net flux, in ADU, with the sky taken as the median of every ring
        pixel.
    """
    aperture = CircularAperture((CENTER, CENTER), r=4.0)
    annulus = CircularAnnulus((CENTER, CENTER), r_in=7.0, r_out=12.0)
    sky = float(np.median(annulus.to_mask(method="center").get_values(data)))
    return float(aperture.to_mask(method="exact").get_values(data).sum() - aperture.area * sky)


def test_masking_removes_the_bias_that_neighbors_in_the_ring_cause() -> None:
    """Five bright ring neighbors lift a plain median; masking does not."""
    angles = np.arange(5) * 1.25
    ring_neighbors = [(CENTER + 9.0 * np.cos(a), CENTER + 9.0 * np.sin(a), 5000.0) for a in angles]
    alone, _ = _field([])
    reference = _measure(alone, None)
    data, field = _field(ring_neighbors)
    masked = _measure(data, field)
    assert reference.net_flux_adu - _plain_median_flux(data) > 5.0 * reference.flux_error_adu
    assert abs(masked.net_flux_adu - reference.net_flux_adu) < 2.0 * reference.flux_error_adu
    assert masked.masked_sky_fraction > 0.2


def test_a_neighbor_inside_the_circle_makes_the_star_crowded() -> None:
    """A bright neighbor 4.5 px away is flagged; a far or faint one is not."""
    close, field = _field([(CENTER + 4.5, CENTER, 1000.0)])
    measurement = _measure(close, field)
    assert measurement.crowded
    assert measurement.nearest_neighbor_distance_px == pytest.approx(4.5)
    assert measurement.nearest_neighbor_flux_ratio == pytest.approx(0.5)

    far, far_field = _field([(CENTER + 20.0, CENTER, 1000.0)])
    assert not _measure(far, far_field).crowded
    faint, faint_field = _field([(CENTER + 4.0, CENTER, 50.0)])
    assert not _measure(faint, faint_field).crowded


def test_a_field_without_neighbors_keeps_the_defaults() -> None:
    """With no detections given, the crowding fields stay empty."""
    data, _ = _field([])
    measurement = _measure(data, None)
    assert not measurement.crowded
    assert np.isnan(measurement.nearest_neighbor_distance_px)
    assert measurement.masked_sky_fraction == pytest.approx(0.0)


def test_comparison_selection_never_chooses_a_crowded_star() -> None:
    """The brightest-band star flagged crowded is left out of the set."""
    count, frames = 60, 12
    stamps = [datetime(2026, 1, 1) + timedelta(minutes=5 * index) for index in range(frames)]
    stars = []
    for index in range(count):
        star = StellarObject(id=f"Star_{index}")
        star.photometry = PhotometryResult(
            timestamps=list(stamps),
            fluxes=[(count - index) * 100.0] * frames,
            flux_errors=[1.0] * frames,
            is_saturated=[False] * frames,
            crowded=index == 10,
        )
        stars.append(star)
    table = build_flux_table(stars)
    assert table is not None
    chosen = {table.star_ids[row] for row in select_comparison_set(table).member_rows}
    assert "Star_10" not in chosen
    assert chosen
    for star in stars:
        star.photometry.crowded = False
    table = build_flux_table(stars)
    assert table is not None
    assert "Star_10" in {table.star_ids[row] for row in select_comparison_set(table).member_rows}


def test_the_neighbor_search_lists_a_close_pair_that_the_default_finder_merges() -> None:
    """A pair 5 px apart merges by default and splits with less separation."""
    rows, columns = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    data = SKY_ADU + np.random.default_rng(3).normal(0.0, NOISE_ADU, (SIZE, SIZE))
    data += _star(rows, columns, CENTER, CENTER, STAR_PEAK_ADU)
    data += _star(rows, columns, CENTER + 5.0, CENTER, 0.5 * STAR_PEAK_ADU)
    assert len(detect_stars_without_shape_cut(data, expected_fwhm_px=2.33)) == 1
    field = detect_neighbor_field(data)
    assert field is not None
    assert len(field.positions) == 2
    assert sorted(np.round(field.positions[:, 0])) == [CENTER, CENTER + 5.0]
