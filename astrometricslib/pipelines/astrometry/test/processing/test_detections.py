"""Tests of the viewer's detection list.

They check that `measure_detections_in_file` reports the stars of a synthetic
image in 1-based pixels, that it gives the same list as the function the
astrometry run calls (`find_unique_stars`), and that a star reported twice is
marked as merged.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import Astrometrics
from astrometricslib.exceptions import NotFoundError
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.astrometry.processing.detections import measure_detections_in_file
from astrometricslib.pipelines.astrometry.processing.star_identifier import find_unique_stars

STAR_POSITIONS = [(40.0, 50.0), (120.0, 60.0), (80.0, 140.0), (150.0, 150.0)]
CLOSE_PAIR = [(60.0, 100.0), (66.0, 100.0)]


def _write_image(path: Path, positions: list[tuple[float, float]]) -> np.ndarray:
    """Write a noisy image with Gaussian stars at the given 0-based positions.

    Returns
    -------
    image : `numpy.ndarray`
        The pixels that were written.
    """
    rows, columns = np.mgrid[0:200, 0:200]
    image = np.random.default_rng(3).normal(100.0, 2.0, size=(200, 200))
    for x, y in positions:
        image += 3000.0 * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * 1.7**2))
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)
    return image


def test_stars_are_found_in_one_based_pixels(tmp_path: Path) -> None:
    """Each known star has a detection within a pixel of its position."""
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    result = measure_detections_in_file(str(path))
    assert result.detection_count == len(result.detections)
    assert result.width == 200 and result.height == 200
    for x, y in STAR_POSITIONS:
        nearest = min(result.detections, key=lambda d: (d.x - (x + 1)) ** 2 + (d.y - (y + 1)) ** 2)
        assert abs(nearest.x - (x + 1)) < 1.0
        assert abs(nearest.y - (y + 1)) < 1.0
    assert type(result.detections[0].x) is float
    assert result.kernel_fwhm_px > 0


def test_same_list_as_the_run_function(tmp_path: Path) -> None:
    """The call gives the run function's list, shifted to 1-based pixels."""
    path = tmp_path / "pair.fits"
    image = _write_image(path, STAR_POSITIONS + CLOSE_PAIR)
    result = measure_detections_in_file(str(path))
    _, unique = find_unique_stars(SourceDetector(), np.asarray(image, dtype=np.float32).astype(float))
    assert len(unique) == result.detection_count
    for source, detection in zip(unique, result.detections, strict=True):
        assert detection.x == pytest.approx(float(source["x_centroid"]) + 1.0)
        assert detection.y == pytest.approx(float(source["y_centroid"]) + 1.0)
        assert detection.flux == pytest.approx(float(source["flux"]))


def test_a_star_reported_twice_is_marked_merged() -> None:
    """Two close reports become one marked detection."""
    detector = SourceDetector(fwhm=4.0)
    sources = [
        {"x_centroid": 10.0, "y_centroid": 10.0, "flux": 5.0},
        {"x_centroid": 11.0, "y_centroid": 10.0, "flux": 2.0},
        {"x_centroid": 50.0, "y_centroid": 50.0, "flux": 3.0},
    ]
    unique = detector.deduplicate(sources)
    assert len(unique) == 2
    merged = unique[0]
    assert merged["merged_detection_count"] == 2
    assert merged["x_centroid"] == pytest.approx(10.0)
    assert merged["flux"] == pytest.approx(7.0)
    assert "merged_detection_count" not in unique[1]


def test_public_call_matches_and_missing_file_is_refused(tmp_path: Path) -> None:
    """The public call gives the same count; a missing file is refused."""
    path = tmp_path / "stars.fits"
    _write_image(path, STAR_POSITIONS)
    through_api = Astrometrics().quality.measure_detections(str(path))
    assert through_api.detection_count == measure_detections_in_file(str(path)).detection_count
    with pytest.raises(NotFoundError):
        measure_detections_in_file(str(tmp_path / "missing.fits"))
