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
    """A stand-in for the library's star catalog that fails on a full read.

    Not a `Mock`: `StellarService` treats a `Mock` as "running in a test"
    and takes a shortcut, which would hide the behaviour under test.
    """

    def __init__(self, by_target: dict[str, list[StellarObject]] | None = None) -> None:
        """Set which stars each target owns and start recording calls."""
        self.by_target = by_target or {}
        self.calls: list[tuple] = []
        self.existing = set()
        self.spectrum_ids: list[str] = []
        self.found_or_created = StellarObject(id="Created")

    def list_objects(self) -> list[StellarObject]:
        """Fail, because reading every star is what must not happen.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("the whole star catalog was read")

    def save_all(self, objects: list[StellarObject], allow_empty: bool = False) -> str:
        """Fail, because replacing the whole catalog must not happen.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("the whole star catalog was rewritten")

    def list_objects_for_target(self, target_id: str) -> list[StellarObject]:
        """Return the stars of one target and record the request.

        Returns
        -------
        stars : `list` [`StellarObject`]
            That target's stars.
        """
        self.calls.append(("list_objects_for_target", target_id))
        return self.by_target.get(target_id, [])

    def existing_ids(self, ids: list[str]) -> set[str]:
        """Return which ids exist and record which ids were asked about.

        Returns
        -------
        found : `set` [`str`]
            The ids that exist.
        """
        self.calls.append(("existing_ids", sorted(ids)))
        return self.existing & set(ids)

    def list_spectrum_object_ids(self) -> list[str]:
        """Return the ids of stars with spectra.

        Returns
        -------
        ids : `list` [`str`]
            The ids set by the test.
        """
        return self.spectrum_ids

    def find_or_create_by_position(self, ra: float, dec: float, **details: object) -> StellarObject:
        """Record the request and return the prepared star.

        Returns
        -------
        star : `StellarObject`
            The star set by the test.
        """
        self.calls.append(("find_or_create_by_position", ra, dec, details))
        return self.found_or_created


def _make_service(stars: _Stars, planning_sources: list | None = None) -> StellarService:
    """Build a service over a library stand-in and a mock wayfinder.

    Returns
    -------
    service : `StellarService`
        A service whose star catalog is `stars`.
    """
    astrometrics = SimpleNamespace(stars=stars, targets=SimpleNamespace(list=lambda: []))
    wayfinder = MagicMock()
    wayfinder.planning.get_sources.return_value = planning_sources or []
    return StellarService(config=MagicMock(), astrometrics=astrometrics, wayfinder=wayfinder)


def test_stars_of_one_target_are_read_from_that_target_only() -> None:
    """Asking for a target's stars reads just that target's stars."""
    stars = _Stars({"M 13": [StellarObject(id="A"), StellarObject(id="B")]})

    found = _make_service(stars).get_stellar_objects("M 13")

    assert [star.id for star in found] == ["A", "B"]
    assert stars.calls == [("list_objects_for_target", "M 13")]


def test_the_displayable_listing_for_a_target_hides_detection_stubs() -> None:
    """The user-facing listing reads one target and drops per-frame stubs."""
    stars = _Stars({"M 81": [StellarObject(id="Polaris"), StellarObject(id="M 81:2026-01-14:0:0:Star_60")]})

    listed = _make_service(stars).get_displayable_stellar_objects("M 81")

    assert [star.id for star in listed] == ["Polaris"]


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


def test_find_or_create_by_position_is_handed_to_the_library() -> None:
    """The service does not search the catalog itself any more."""
    stars = _Stars()

    result = _make_service(stars).find_or_create_by_position(
        279.2, 38.8, name="Vega", spectral_type="A0V", magnitude=0.03, target_id="Vega", tolerance_arcsec=3.0
    )

    assert result is stars.found_or_created
    assert stars.calls == [
        (
            "find_or_create_by_position",
            279.2,
            38.8,
            {
                "name": "Vega",
                "spectral_type": "A0V",
                "magnitude": 0.03,
                "target_id": "Vega",
                "tolerance_arcsec": 3.0,
            },
        )
    ]


def test_the_list_of_stars_with_spectra_comes_from_the_library() -> None:
    """The spectra list is answered from the library's index."""
    stars = _Stars()
    stars.spectrum_ids = ["Vega", "Deneb"]

    assert _make_service(stars).get_spectroscopy_list() == ["Vega", "Deneb"]


def test_get_sources_with_the_global_catalog_checks_only_the_returned_ids() -> None:
    """Telling local stars from SIMBAD's asks about just the ids returned."""
    local_star = StellarObject(id="Local", ra=10.0, dec=20.0, magnitude=5.0)
    global_star = StellarObject(id="Global", ra=10.1, dec=20.1, magnitude=6.0)
    stars = _Stars()
    stars.existing = {"Local", "Something Else In The Library"}

    service = _make_service(stars, planning_sources=[local_star, global_star])
    sources = service.get_sources(ra=10.0, dec=20.0, radius=1.0, include_catalog=True)

    flags = {source["id"]: source["global"] for source in sources}
    assert flags == {"Local": False, "Global": True}
    assert stars.calls == [("existing_ids", ["Global", "Local"])]
