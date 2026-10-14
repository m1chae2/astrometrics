"""Purpose: Unit tests for post-stack image quality measurements.

Description: Verifies measure_saturated_pixel_fraction's saturated-pixel
count. measure_image_fwhm now lives in pipelines/astrometry/fwhm.py
and is tested there, and measure_rejected_fraction reads Siril's own
rejection map, so it lives in drivers/siril_output_parsing.py and is
tested in drivers/test/test_siril_rejection_map.py.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.quality.quality_metrics import measure_saturated_pixel_fraction


def test_measure_saturated_pixel_fraction_finds_plateau_in_normalised_stack(tmp_path: Path) -> None:
    """Verifies 100 pixels piled at 0.8 in a 0-1 stack count as saturated."""
    data = np.random.default_rng(1).uniform(0.03, 0.07, (100, 100)).astype(np.float32)
    data[:1, :100] = 0.8  # 100 of 10000 pixels on the plateau
    path = tmp_path / "saturated_stack.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_saturated_pixel_fraction(str(path)) == pytest.approx(0.01)


def test_measure_saturated_pixel_fraction_ignores_unsaturated_stack(tmp_path: Path) -> None:
    """Verifies a stack whose brightest pixel is a lone peak reports zero."""
    data = np.random.default_rng(1).uniform(0.03, 0.07, (100, 100)).astype(np.float32)
    data[50, 50] = 1.0
    path = tmp_path / "unsaturated_stack.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_saturated_pixel_fraction(str(path)) == pytest.approx(0.0)
