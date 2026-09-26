"""Purpose: Unit tests for the stars a spectral frame is registered against.

Description: Registration hands a spectral star the name of whichever reference
star lines up with it by pixel geometry, so the reference stars have to come
from the frame's own part of the sky. These tests check that stars far from the
frame centre are left out, from both the target's own stars and the fallback
set, and that nothing is ruled out when the frame centre is unknown.
"""

from types import SimpleNamespace
from typing import Any

from astropy.coordinates import SkyCoord

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess, StarPosition, StarSummary
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy.runner import (
    _field_center_for_registration,
    _registration_reference_candidates,
)

FIELD_CENTER = (292.68, 27.96)


def _star(star_id: str, ra: float | None, dec: float | None, targets: list[str]) -> StellarObject:
    """Build a catalog-identified star at a sky position.

    Returns
    -------
    star : `StellarObject`
        The star, tagged with the given target names.
    """
    star = StellarObject(id=star_id, name=star_id)
    star.right_ascension = ra
    star.declination = dec
    star.is_catalog_identified = True
    star.target_ids = targets
    return star


class _Catalog(AbstractCatalogAccess):
    """A stand-in for the database that the candidate search queries.

    Answers the same queries as `CatalogAccess`, from a list of stars, and
    refuses to hand back the whole catalog: the search must ask only for the
    stars it needs.
    """

    def __init__(self, stars: list[StellarObject]) -> None:
        """Keep the stars to hand back."""
        self._stars = stars

    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Refuse to return the whole catalog.

        Raises
        ------
        AssertionError
            Always: reading every star is what this search must not do.
        """
        raise AssertionError("the candidate search read the whole catalog")

    def put(self, obj: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Do nothing; nothing here is saved."""

    def exists(self, dataset_type: str, selector: dict[str, Any]) -> bool:
        """Report that nothing exists.

        Returns
        -------
        found : `bool`
            Always `False`.
        """
        return False

    def get_local_path(self, dataset_type: str, selector: dict[str, Any]) -> str:
        """Return a placeholder path.

        Returns
        -------
        path : `str`
            An unused placeholder.
        """
        return ""

    def _summary(self, star: StellarObject) -> StarSummary:
        return StarSummary(
            id=star.id,
            name=star.name,
            right_ascension=star.right_ascension,
            declination=star.declination,
            target_ids=list(star.target_ids),
        )

    def list_star_summaries(
        self, target_id: str | None = None, limit: int | None = None
    ) -> list[StarSummary]:
        """List every star, or the stars of one target, in short form.

        Returns
        -------
        summaries : `list` [`StarSummary`]
            One summary per matching star.
        """
        return [self._summary(s) for s in self._stars if not target_id or target_id in s.target_ids]

    def list_stars_in_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
        magnitude_range: tuple[float, float] | None = None,
    ) -> list[StarSummary]:
        """List the stars within a circle of sky, by great-circle distance.

        Returns
        -------
        summaries : `list` [`StarSummary`]
            One summary per star with a position inside the circle.
        """
        center = SkyCoord(ra_degrees, dec_degrees, unit="deg")
        return [
            self._summary(s)
            for s in self._stars
            if s.right_ascension is not None
            and s.declination is not None
            and center.separation(SkyCoord(s.right_ascension, s.declination, unit="deg")).deg
            <= radius_degrees
        ]

    def list_position_only_stars(self, target_id: str | None = None) -> list[StarPosition]:
        """Return no position-only stars.

        Returns
        -------
        positions : `list` [`StarPosition`]
            Always empty.
        """
        return []

    def get_by_ids(self, dataset_type: str, ids: list[str]) -> list[StellarObject]:
        """Return the stars with the given ids.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The stars found.
        """
        return [s for s in self._stars if s.id in set(ids)]


def test_own_target_stars_far_from_the_field_are_left_out() -> None:
    """A star an earlier bad run attached to the target is not trusted."""
    near = _star("near", 292.7, 28.0, ["Albireo"])
    far = _star("far", 98.07, 4.57, ["Albireo"])

    candidates = _registration_reference_candidates(
        SimpleNamespace(id="Albireo"), _Catalog([near, far]), FIELD_CENTER
    )

    assert [star.id for star in candidates] == ["near"]


def test_the_fallback_set_is_limited_to_the_field() -> None:
    """With no stars of its own, only stars in the field are candidates."""
    near = _star("near", 292.5, 27.5, ["Vega"])
    far = _star("far", 283.4, 33.0, ["M 57"])

    candidates = _registration_reference_candidates(
        SimpleNamespace(id="Albireo"), _Catalog([near, far]), FIELD_CENTER
    )

    assert [star.id for star in candidates] == ["near"]


def test_a_star_without_a_sky_position_is_left_out_when_the_field_is_known() -> None:
    """A position cannot be checked, so the star cannot be trusted."""
    unplaced = _star("unplaced", None, None, ["Albireo"])

    assert (
        _registration_reference_candidates(SimpleNamespace(id="Albireo"), _Catalog([unplaced]), FIELD_CENTER)
        == []
    )


def test_nothing_is_ruled_out_when_the_field_is_unknown() -> None:
    """Without a frame centre the old behaviour is kept."""
    far = _star("far", 98.07, 4.57, ["Albireo"])

    candidates = _registration_reference_candidates(SimpleNamespace(id="Albireo"), _Catalog([far]), None)

    assert candidates == [far]


def test_a_solved_position_is_preferred_over_the_header(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """The hint from a plate-solved stack wins; the header is the fallback."""
    from astropy.io import fits

    stack = tmp_path / "stack.fits"
    header = fits.Header()
    header["RA"] = 10.0
    header["DEC"] = 20.0
    fits.PrimaryHDU(data=[[0.0]], header=header).writeto(stack)

    assert _field_center_for_registration(str(stack), 1.5, 2.5) == (1.5, 2.5)
    assert _field_center_for_registration(str(stack), None, None) == (10.0, 20.0)
    assert _field_center_for_registration(str(tmp_path / "missing.fits"), None, None) is None
