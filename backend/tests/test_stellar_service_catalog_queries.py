"""Purpose: Unit tests for how StellarService reaches the star catalog.

Description: The star catalog is owned by astrometricslib and lives in its
database. Loading every star into new objects costs about 2.8 GB and 12
seconds on a real 274,000-star library, and the memory is not handed back,
which got the backend killed for running out of memory. These tests check
that the service asks the library for only the stars it needs, and never
reads or rewrites the whole catalog for an everyday request.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from astrometricslib import StellarObject
from backend.services.data.stellar_service import StellarService


class _Stars:
    """A stand-in for the library's star catalog that fails on a full read."""

    def __init__(self) -> None:
        """Start recording calls."""
        self.calls: list[tuple] = []
        self.spectrum_ids: list[str] = []

    def query(self, has_spectra: bool | None = None, **arguments: object) -> list[StellarObject]:
        """Answer the stars-with-spectra query, and refuse any other.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The stars with spectra.

        Raises
        ------
        AssertionError
            If the query would read every star in the catalog.
        """
        self.calls.append(("query", has_spectra, arguments))
        if has_spectra and not arguments:
            return [StellarObject(id=star_id) for star_id in self.spectrum_ids]
        raise AssertionError("the whole star catalog was read")


def _make_service(stars: _Stars) -> StellarService:
    """Build a service over a library stand-in and a mock wayfinder.

    Returns
    -------
    service : `StellarService`
        A service whose star catalog is `stars`.
    """
    astrometrics = SimpleNamespace(stars=stars)
    return StellarService(config=MagicMock(), astrometrics=astrometrics, wayfinder=MagicMock())


def test_saving_does_not_read_or_rewrite_the_catalog() -> None:
    """save_objects() has nothing to write, and touches nothing.

    Every star change is already saved as it is made. This used to load all
    stars and write them all back, replacing the whole table, which could
    delete a star another process had just added.
    """
    stars = _Stars()

    result = _make_service(stars).save_objects()

    assert result == "stellar catalog saved"
    assert stars.calls == []


def test_the_list_of_stars_with_spectra_comes_from_the_library() -> None:
    """The spectra list is answered from one filtered library query."""
    stars = _Stars()
    stars.spectrum_ids = ["Vega", "Deneb"]

    assert _make_service(stars).get_spectroscopy_list() == ["Vega", "Deneb"]
