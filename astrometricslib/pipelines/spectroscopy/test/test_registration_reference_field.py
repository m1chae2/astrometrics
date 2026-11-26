"""Purpose: Unit tests for the stars a spectral frame is registered against.

Description: Registration hands a spectral star the name of whichever reference
star lines up with it by pixel geometry, so the reference stars have to come
from the frame's own part of the sky. These tests check that stars far from the
frame centre are left out, from both the target's own stars and the fallback
set, and that nothing is ruled out when the frame centre is unknown.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astropy.coordinates import SkyCoord

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess, StarPosition, StarSummary
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


def _unflagged_star(star_id: str, ra: float | None, dec: float | None) -> StellarObject:
    """Build a star with a sky position whose identified flag is off.

    Returns
    -------
    star : `StellarObject`
        The star, attached to the Albireo target.
    """
    star = _star(star_id, ra, dec, ["Albireo"])
    star.is_catalog_identified = False
    return star


def test_a_named_star_with_a_position_is_a_reference_even_when_not_flagged() -> None:
    """Regression: `* alf Per` and `* gam Cas` were stored unflagged."""
    named = _unflagged_star("* alf Per", 292.7, 28.0)

    candidates = _registration_reference_candidates(
        SimpleNamespace(id="Albireo"), _Catalog([named]), FIELD_CENTER
    )

    assert [star.id for star in candidates] == ["* alf Per"]


def test_unflagged_anonymous_detections_and_unplaced_stars_are_not_references() -> None:
    """Only a real name with a sky position rescues an unflagged star."""
    anonymous = [
        _unflagged_star("Star_3", 292.7, 28.0),
        _unflagged_star("FIELD_J292.7_28.0", 292.7, 28.0),
        _unflagged_star("* bet Cyg", None, None),
    ]

    candidates = _registration_reference_candidates(SimpleNamespace(id="Albireo"), _Catalog(anonymous), None)

    assert candidates == []


def test_a_solved_position_is_preferred_over_the_header(tmp_path: Path) -> None:
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


class _CountingCatalog(_Catalog):
    """A stand-in that remembers every id whose full record was read."""

    def __init__(self, stars: list[StellarObject]) -> None:
        """Keep the stars and start with no ids read."""
        super().__init__(stars)
        self.read_ids: list[str] = []

    def get_by_ids(self, dataset_type: str, ids: list[str]) -> list[StellarObject]:
        """Note the ids asked for, then return those stars.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The stars found.
        """
        self.read_ids.extend(ids)
        return super().get_by_ids(dataset_type, ids)


def test_position_only_rows_are_never_read_in_full() -> None:
    """A FIELD_J row can never be a reference, so its record is not loaded."""
    named = _star("Gaia DR3 1", 292.7, 28.0, ["Albireo"])
    position_only = StellarObject(id="FIELD_J292.7000+28.0000", name="FIELD_J292.7000+28.0000")
    position_only.right_ascension = 292.7
    position_only.declination = 28.0
    position_only.target_ids = ["Albireo"]

    for field_center in (FIELD_CENTER, None):
        catalog = _CountingCatalog([named, position_only])
        candidates = _registration_reference_candidates(SimpleNamespace(id="Albireo"), catalog, field_center)
        assert [star.id for star in candidates] == ["Gaia DR3 1"]
        assert position_only.id not in catalog.read_ids


def _write_stack(path: Path) -> str:
    """Write a small stack that says it holds five frames.

    Returns
    -------
    stack_path : `str`
        The stack's path.
    """
    import numpy as np
    from astropy.io import fits

    header = fits.Header()
    header["STACKCNT"] = 5
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32), header=header).writeto(path)
    return str(path)


def test_the_stars_astrometry_named_on_the_stack_are_references(tmp_path: Path) -> None:
    """Verify the stack's solved star list joins the saved stars of the target.

    Astrometry no longer saves star records, so the named stars of the
    target's imaging stack come from the list kept next to the stack.
    """
    from astrometricslib.pipelines.shared.solved_star_list import read_solved_stars, save_solved_stars

    stack_path = _write_stack(tmp_path / "Albireo_Stacked.fits")
    named = _star("HD 183912", FIELD_CENTER[0], FIELD_CENTER[1] + 0.01, ["Albireo"])
    far = _star("HD 1", FIELD_CENTER[0] + 5.0, FIELD_CENTER[1], ["Albireo"])
    assert save_solved_stars(stack_path, [named, far])
    assert [star.id for star in read_solved_stars(stack_path)] == ["HD 183912", "HD 1"]
    saved = _star("Gaia DR3 7", FIELD_CENTER[0], FIELD_CENTER[1] - 0.01, ["Albireo"])
    target = SimpleNamespace(
        id="Albireo", stacking=SimpleNamespace(stacked_image=stack_path, stacks_by_configuration={})
    )

    candidates = _registration_reference_candidates(target, _Catalog([saved]), FIELD_CENTER)

    assert sorted(star.id for star in candidates) == ["Gaia DR3 7", "HD 183912"]


def test_a_single_frame_gets_no_solved_star_list(tmp_path: Path) -> None:
    """Verify nothing is written next to an image that is not a stack."""
    import numpy as np
    from astropy.io import fits

    from astrometricslib.pipelines.shared.solved_star_list import read_solved_stars, save_solved_stars

    frame_path = tmp_path / "frame.fits"
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(frame_path)

    assert not save_solved_stars(str(frame_path), [_star("HD 1", 1.0, 1.0, ["Albireo"])])
    assert read_solved_stars(str(frame_path)) == []


def test_a_restacked_stack_drops_its_old_solved_star_list(tmp_path: Path) -> None:
    """Verify a list written before the stack changed is not read.

    A restack writes a new stack; until astrometry solves it again, the stars
    named on the old stack must not stand for the new one.
    """
    import os

    from astrometricslib.pipelines.shared.solved_star_list import read_solved_stars, save_solved_stars

    stack_path = _write_stack(tmp_path / "Albireo_Stacked.fits")
    assert save_solved_stars(stack_path, [_star("HD 183912", FIELD_CENTER[0], FIELD_CENTER[1], ["Albireo"])])
    assert [star.id for star in read_solved_stars(stack_path)] == ["HD 183912"]

    status = os.stat(stack_path)
    os.utime(stack_path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))

    assert read_solved_stars(stack_path) == []


def test_a_list_saved_before_stamps_is_read_while_the_stack_is_not_newer(tmp_path: Path) -> None:
    """Verify a list with no stamp is read unless the stack changed after it.

    Lists written before the stack's stamp was added have none. Such a list
    is kept while its file is not older than the stack, and dropped once the
    stack is newer (a restack that astrometry has not solved yet).
    """
    import json
    import os

    from astrometricslib.pipelines.shared.solved_star_list import read_solved_stars, save_solved_stars
    from astrometricslib.pipelines.shared.stack_sidecar import sidecar_path

    stack_path = _write_stack(tmp_path / "Albireo_Stacked.fits")
    assert save_solved_stars(stack_path, [_star("HD 183912", FIELD_CENTER[0], FIELD_CENTER[1], ["Albireo"])])
    saved_path = sidecar_path(stack_path, "_solved_stars.json")
    with open(saved_path, encoding="utf-8") as handle:
        record = json.load(handle)
    del record["stack_stamp"]
    with open(saved_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    stack_time = os.stat(stack_path).st_mtime_ns
    os.utime(saved_path, ns=(stack_time, stack_time + 1_000_000_000))

    assert [star.id for star in read_solved_stars(stack_path)] == ["HD 183912"]

    os.utime(stack_path, ns=(stack_time, stack_time + 2_000_000_000))

    assert read_solved_stars(stack_path) == []
