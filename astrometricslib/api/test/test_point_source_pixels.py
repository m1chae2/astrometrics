"""Purpose: Check that `detect_point_sources` gives 1-based pixel positions.

Description: Every public position counts pixels from 1, as FITS and DS9 do.
A star drawn at array index (row 30, column 20) must come back near x 21,
y 31.
"""

from unittest.mock import MagicMock

import numpy as np

from astrometricslib import AppConfiguration
from astrometricslib._internal.processing_scripts import detect_point_sources


def test_point_sources_are_in_one_based_pixels() -> None:
    """A star at 0-based column 20, row 30 is reported at x 21, y 31."""
    rng = np.random.default_rng(1)
    rows, columns = np.mgrid[0:64, 0:64]
    image = rng.normal(100.0, 2.0, (64, 64))
    image += 5000.0 * np.exp(-((columns - 20.0) ** 2 + (rows - 30.0) ** 2) / (2 * 1.7**2))

    pipelines = MagicMock()
    pipelines._config = AppConfiguration()
    stars = detect_point_sources(pipelines, image).stars

    assert stars
    assert abs(stars[0].x - 21.0) < 0.2
    assert abs(stars[0].y - 31.0) < 0.2
