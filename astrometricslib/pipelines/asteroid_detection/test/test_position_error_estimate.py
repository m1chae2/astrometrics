"""Tests for the per-frame position error that the straight-line test uses.

The pointing correction pairs each detection with a reference star and finds
one shared shift. What is left after the shift is the error of one position
in that frame. The straight-line test needs that number, so these tests
check that it is estimated, that it follows the real scatter, and that it is
left out (so the assumed value is used) when it cannot be measured. They
also check that the exposure time reaches the detections.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from pytest_mock import MockerFixture

from astrometricslib.models.moving_object import CascadeStage
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.pipeline import (
    AsteroidDetectionPipeline,
    _build_reference_star_tree,
    _estimate_bulk_pointing_correction_deg,
    _read_exposure_seconds,
)
from astrometricslib.pipelines.asteroid_detection.test.test_moving_object_pipeline import (
    _build_moving_target,
    _light_frames,
)

_DECLINATION_DEG = 20.0
_COS_DECLINATION = math.cos(math.radians(_DECLINATION_DEG))


def _spread_out_reference_stars(count: int, seed: int) -> list[tuple[float, float]]:
    """Scatter reference stars about 90 arcsec apart so wrong pairs are rare.

    Parameters
    ----------
    count : `int`
        How many stars.
    seed : `int`
        Seed for the random generator.

    Returns
    -------
    positions_deg : `list` [`tuple` [`float`, `float`]]
        `(ra_deg, dec_deg)` for each star.
    """
    rng = np.random.default_rng(seed)
    columns = math.ceil(math.sqrt(count))
    spacing_deg = 90.0 / 3600.0
    positions = []
    for index in range(count):
        row, column = divmod(index, columns)
        positions.append((
            150.0 + (column * spacing_deg + rng.uniform(-0.1, 0.1) * spacing_deg) / _COS_DECLINATION,
            _DECLINATION_DEG + row * spacing_deg + rng.uniform(-0.1, 0.1) * spacing_deg,
        ))
    return positions


@pytest.mark.parametrize("noise_arcsec", [0.5, 2.0, 4.0])
def test_the_position_error_follows_the_scatter_of_the_detections(noise_arcsec: float) -> None:
    """Shifted, noisy detections report an error near the noise."""
    reference = _spread_out_reference_stars(100, seed=3)
    rng = np.random.default_rng(11)
    shift_ra_arcsec, shift_dec_arcsec = 12.0, -9.0
    detections = [
        (
            ra - (shift_ra_arcsec + rng.normal(0.0, noise_arcsec)) / 3600.0 / _COS_DECLINATION,
            dec - (shift_dec_arcsec + rng.normal(0.0, noise_arcsec)) / 3600.0,
        )
        for ra, dec in reference
    ]
    tree, reference_ra, reference_dec, cos_dec = _build_reference_star_tree(reference)

    ra_offset_deg, dec_offset_deg, position_error = _estimate_bulk_pointing_correction_deg(
        detections, tree, reference_ra, reference_dec, cos_dec
    )

    assert ra_offset_deg * 3600.0 * _COS_DECLINATION == pytest.approx(shift_ra_arcsec, abs=1.0)
    assert dec_offset_deg * 3600.0 == pytest.approx(shift_dec_arcsec, abs=1.0)
    assert position_error is not None
    assert position_error == pytest.approx(noise_arcsec, rel=0.35)


def test_the_position_error_is_none_when_no_correction_could_be_measured() -> None:
    """With no detections there is no vote, so no error is reported."""
    reference = _spread_out_reference_stars(25, seed=5)
    tree, reference_ra, reference_dec, cos_dec = _build_reference_star_tree(reference)

    assert _estimate_bulk_pointing_correction_deg([], tree, reference_ra, reference_dec, cos_dec) == (
        0.0,
        0.0,
        None,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(30, 30.0), ("45.5", 45.5), (None, None), ("n/a", None), (0, None), (-5, None), ("nan", None)],
)
def test_exposure_time_is_read_only_when_it_is_a_positive_number(
    value: object, expected: float | None
) -> None:
    """EXPTIME that is missing, not a number or not positive gives `None`."""
    header = fits.Header()
    if value is not None:
        header["EXPTIME"] = value

    assert _read_exposure_seconds(header) == expected


def test_the_pipeline_carries_exposure_time_and_an_assumed_error_onto_the_track(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """EXPTIME reaches every detection; with no stars the error is assumed."""
    mocker.patch("astroquery.imcce.Skybot.cone_search", return_value=None)
    target = _build_moving_target(tmp_path)
    for frame in target.frames:
        fits.setval(frame.path, "EXPTIME", value=30.0)

    pipeline = AsteroidDetectionPipeline(MovingObjectConfig())
    candidates = pipeline.process(target.id, target.stacking.stacked_image, _light_frames(target))

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    assert {detection.exposure_seconds for detection in candidates[0].frame_detections} == {30.0}
    assert {detection.astrometric_error_arcsec for detection in candidates[0].frame_detections} == {None}
    track = candidates[0].track
    assert track is not None
    assert track.astrometric_error_assumed is True
    assert track.astrometric_error_arcsec == pytest.approx(
        MovingObjectConfig().astrometric_error_default_arcsec
    )
    assert pipeline.last_run_metrics["residual_chains_tested"] == 1
    assert pipeline.last_run_metrics["residual_assumed_error_detections"] == 4
