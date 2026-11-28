"""Purpose: Tests for the linked spectrum read.

Description: Covers `StellarCatalog.get_spectrum`. A star's spectrum is read
from the star itself, and an older ``::spectroscopy`` copy is only a
fallback.
"""

from unittest.mock import MagicMock

from astrometricslib.api.stars import StellarCatalog
from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject


def _star(star_id: str, with_spectrum: bool) -> StellarObject:
    """Build a star with or without a spectrum.

    Parameters
    ----------
    star_id : `str`
        The star's id.
    with_spectrum : `bool`
        Whether to give the star a two-point spectrum.

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    star = StellarObject(id=star_id)
    if with_spectrum:
        star.spectroscopy = SpectroscopyResult(wavelengths_angstrom=[4000.0, 5000.0], intensities=[1.0, 2.0])
    return star


def _stars_with(*stars: StellarObject) -> StellarCatalog:
    """Build a catalog whose storage holds exactly these stars.

    Returns
    -------
    catalog : `StellarCatalog`
        The catalog.
    """
    by_id = {star.id: star for star in stars}
    storage = MagicMock()
    storage.get_by_ids.side_effect = lambda _kind, ids: [by_id[i] for i in ids if i in by_id]
    storage.get_stars_without_light_curves.side_effect = lambda ids: [by_id[i] for i in ids if i in by_id]
    storage.list_star_ids.return_value = list(by_id)
    return StellarCatalog(MagicMock(), storage)


class TestGetSpectrum:
    """Unit test suite for StellarCatalog.get_spectrum."""

    def test_reads_the_spectrum_from_the_star_itself(self) -> None:
        """The star's own record answers; no copy is needed."""
        star = _star("HD 1", True)
        assert _stars_with(star).get_spectrum(star) is star.spectroscopy

    def test_falls_back_to_an_older_spectroscopy_copy(self) -> None:
        """A star with no spectrum of its own uses its older copy."""
        copy = _star("HD 1::spectroscopy", True)
        star = _star("HD 1", False)
        catalog = _stars_with(star, copy)
        assert catalog.get_spectrum(star) is copy.spectroscopy

    def test_accepts_the_old_copy_itself(self) -> None:
        """The old copy, given as the star, gives the base star's spectrum."""
        star = _star("HD 1", True)
        assert _stars_with(star).get_spectrum(_star("HD 1::spectroscopy", False)) is star.spectroscopy

    def test_returns_none_without_a_spectrum(self) -> None:
        """A star with no spectrum and no copy gives `None`."""
        star = _star("HD 1", False)
        catalog = _stars_with(star)
        assert catalog.get_spectrum(star) is None
        assert catalog.get_spectrum(_star("HD 2", False)) is None
