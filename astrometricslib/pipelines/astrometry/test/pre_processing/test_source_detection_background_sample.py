"""Tests that the source detector's background subsample is reproducible.

On a large image, when the 2-D background map is not available, the
detector estimates the background from a random 1/16 of the pixels. These
tests check that the sample is the same on every call (so two runs on one
frame give one star list) and that it is still a spread-out random subset,
not just the first pixels of the image.
"""

import numpy as np
from pytest_mock import MockerFixture

from astrometricslib.pipelines.astrometry.pre_processing import source_detection
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import (
    BACKGROUND_SAMPLE_SEED,
    SourceDetector,
    background_sample_indices,
)

_PIXEL_COUNT = 600 * 600


def _star_summary(star: dict) -> tuple[float, float, float]:
    """Reduce one detected star to its position and brightness.

    Parameters
    ----------
    star : `dict`
        One entry of the detector's result.

    Returns
    -------
    summary : `tuple` [`float`, `float`, `float`]
        The x position, y position, and flux, as plain floats.
    """
    x_key = "xcentroid" if "xcentroid" in star else "x_centroid"
    y_key = "ycentroid" if "ycentroid" in star else "y_centroid"
    return float(star[x_key]), float(star[y_key]), float(star["flux"])


def test_background_sample_is_identical_between_calls() -> None:
    """Two calls with the default seed pick exactly the same pixels."""
    first = background_sample_indices(_PIXEL_COUNT)
    second = background_sample_indices(_PIXEL_COUNT)

    assert np.array_equal(first, second)


def test_background_sample_changes_with_the_seed() -> None:
    """A different seed picks different pixels, so the seed really is used."""
    default = background_sample_indices(_PIXEL_COUNT)
    other = background_sample_indices(_PIXEL_COUNT, seed=BACKGROUND_SAMPLE_SEED + 1)

    assert not np.array_equal(default, other)


def test_background_sample_is_a_random_looking_subset() -> None:
    """The sample is distinct pixels spread over the image, not the first N."""
    indices = background_sample_indices(_PIXEL_COUNT)

    assert indices.size == _PIXEL_COUNT // 16
    assert np.unique(indices).size == indices.size
    assert indices.min() >= 0
    assert indices.max() < _PIXEL_COUNT
    assert not np.array_equal(np.sort(indices), np.arange(indices.size))
    # Each fifth of the image holds roughly a fifth of the sample.
    fifth_counts = np.histogram(indices, bins=5, range=(0, _PIXEL_COUNT))[0]
    assert np.all(np.abs(fifth_counts / indices.size - 0.2) < 0.02)


def test_detect_gives_identical_star_lists_on_the_global_background_path(mocker: MockerFixture) -> None:
    """Two detections of one large frame give the same stars on the fallback.

    The 2-D background step is made to fail, which forces the detector
    onto the random-subsample fallback. The star lists must match exactly.
    """
    mocker.patch.object(source_detection, "Background2D", side_effect=ValueError("forced fallback"))
    rng = np.random.default_rng(3)
    image = rng.normal(100.0, 5.0, (600, 600))
    y_grid, x_grid = np.mgrid[0:600, 0:600]
    for x_center, y_center in [(100, 120), (300, 310), (450, 200), (520, 530)]:
        image += 800.0 * np.exp(-((x_grid - x_center) ** 2 + (y_grid - y_center) ** 2) / (2 * 2.0**2))
    detector = SourceDetector(fwhm=4.0, threshold_sigma=5.0)

    first = detector.detect(image)
    second = detector.detect(image)

    assert len(first) >= 4
    assert [_star_summary(star) for star in first] == [_star_summary(star) for star in second]
