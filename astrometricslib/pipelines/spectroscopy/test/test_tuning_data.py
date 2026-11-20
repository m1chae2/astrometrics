"""Purpose: Unit tests for the stored-data readers of the spectroscopy tuners.

Description: Checks that `stored_spectra` sorts and cleans stored spectra,
skips stars without one and lists a star once, and that `quantile_of` takes
a quantile.
"""

import numpy as np
import pytest

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.spectroscopy.tuning_data import (
    SpectralTargetStars,
    quantile_of,
    stored_spectra,
)


def _target(stars: list[StellarObject]) -> SpectralTargetStars:
    """Build a target entry around some stars.

    Returns
    -------
    target : `SpectralTargetStars`
        A target named ``T`` with a small image.
    """
    return SpectralTargetStars("T", "T.fits", (100, 100), stars, None)


def test_stored_spectra_sorts_cleans_and_skips() -> None:
    """Stored spectra sorts cleans and skips.

    A spectrum is sorted by wavelength, its NaN samples dropped, and a star
    with none skipped.
    """
    with_spectrum = StellarObject(id="A", name="A")
    with_spectrum.spectroscopy = SpectroscopyResult(
        wavelengths_angstrom=[5000.0, 4000.0, 4500.0, 6000.0], intensities=[3.0, 1.0, np.nan, 4.0]
    )
    without_spectrum = StellarObject(id="B", name="B")
    spectra = stored_spectra([_target([with_spectrum, without_spectrum]), _target([with_spectrum])])
    assert [spectrum.star_id for spectrum in spectra] == ["A"]
    assert spectra[0].wavelength_angstrom.tolist() == [4000.0, 5000.0, 6000.0]
    assert spectra[0].intensity.tolist() == [1.0, 3.0, 4.0]
    assert spectra[0].sample_spacing_angstrom == pytest.approx(1000.0)


def test_quantile_of_interpolates() -> None:
    """The median of 1, 2, 3, 4 is 2.5."""
    assert quantile_of([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
