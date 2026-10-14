"""Purpose: Unit tests for reading Siril's rejection map.

Description: Verifies measure_rejected_fraction takes the mean of the
``_RejMap.fits`` file Siril writes beside a stack, and returns None when
there is no such file.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.drivers.siril_output_parsing import measure_rejected_fraction


def test_measure_rejected_fraction_computes_mean_of_rejmap(tmp_path: Path) -> None:
    """Verify the rejected fraction is the rejmap mean, not a nonzero count.

    This is the exact bug found and fixed during development: a rejmap
    pixel holds (frames rejected at that pixel / frames stacked at
    that pixel), so a nonzero-count approach overstates rejection by
    treating "any rejection at all" as "fully rejected".
    """
    stacked_path = tmp_path / "Target_Stacked.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(stacked_path)

    rejmap_path = tmp_path / "Target_Stacked_RejMap.fits"
    rejmap_data = np.zeros((10, 10), dtype=np.float32)
    rejmap_data[0, :] = 0.5  # 10 of 100 pixels at a 50% rejected-frame fraction
    fits.PrimaryHDU(rejmap_data).writeto(rejmap_path)

    result = measure_rejected_fraction(str(stacked_path))
    assert result == pytest.approx(0.05)  # mean, not count-based


def test_measure_rejected_fraction_returns_none_when_rejmap_missing(tmp_path: Path) -> None:
    """Verify a stacked file with no sibling rejmap returns None, not raise."""
    stacked_path = tmp_path / "NoRejmap_Stacked.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(stacked_path)
    assert measure_rejected_fraction(str(stacked_path)) is None
