"""Purpose: Turn the name of a sky object into where it is.

Description: `resolve_name` is the owner of `TargetCatalog.resolve`. It looks
the name up in three places, in order, and stops at the first that knows it:

1. the library's targets, by id or common name (ignoring case), when the
   target has a position;
2. the library's stars, by id or name, when the star has a position;
3. the SIMBAD catalog, which gives an ICRS position at epoch J2000.

The answer is one `Resolution` that says which place knew the name.
"""

import logging
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.resolution import Resolution
from astrometricslib.pipelines.shared import target_records

logger = logging.getLogger(__name__)

__all__ = ["resolve_name"]

_EMPTY_POSITIONS = frozenset({"0h 0m 0s", "0° 0′ 0′′", "0d 0m 0s", "0° 0′ 0″", ""})
"""The position texts of a target whose position was never set."""


def _target_resolution(catalog: Any, name: str) -> Resolution | None:
    """Look a name up among the library's targets.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    name : `str`
        The name.

    Returns
    -------
    resolution : `Resolution` or `None`
        The target with that id or common name that has a position, or
        `None`.
    """
    wanted = name.strip().casefold()
    for target in target_records.list_targets(catalog):
        if target.id.casefold() != wanted and (target.common_name or "").casefold() != wanted:
            continue
        if target.ra.strip() in _EMPTY_POSITIONS and target.dec.strip() in _EMPTY_POSITIONS:
            continue
        try:
            return Resolution(
                name=target.id, ra_deg=target.ra_deg, dec_deg=target.dec_deg, source="target", target=target
            )
        except InvalidArgumentError:
            logger.warning(
                "Target %r has a position that cannot be read: %s %s", target.id, target.ra, target.dec
            )
    return None


def _star_resolution(storage: Any, name: str) -> Resolution | None:
    """Look a name up among the library's stars.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    name : `str`
        The name.

    Returns
    -------
    resolution : `Resolution` or `None`
        The first star with that id or name that has a position, or `None`.
    """
    matching_ids = list(storage.find_star_ids_by_name(name.strip()))
    if name in matching_ids:
        matching_ids.remove(name)
        matching_ids.insert(0, name)
    by_id = {star.id: star for star in storage.get_by_ids("stellar_catalog", matching_ids)}
    for star_id in matching_ids:
        star = by_id.get(star_id)
        if star is None or star.right_ascension is None or star.declination is None:
            continue
        ra_deg, dec_deg = float(star.right_ascension), float(star.declination)
        if ra_deg == 0.0 and dec_deg == 0.0:  # ruff: ignore[float-equality-comparison] -- 0, 0 means unset
            continue
        return Resolution(name=star.name or star.id, ra_deg=ra_deg, dec_deg=dec_deg, source="star", star=star)
    return None


def resolve_name(catalog: Any, storage: Any, name: str, simbad: Any) -> Resolution | None:
    """Find where a named object is: a target, a star, or SIMBAD.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    storage : `AbstractCatalogAccess`
        The catalog storage, for the stars.
    name : `str`
        A target id or common name, a star name, or any name SIMBAD knows.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask when the library does not know the name.

    Returns
    -------
    resolution : `Resolution` or `None`
        Where the object is and who knew the name, or `None` when neither the
        library nor SIMBAD knows it (or SIMBAD cannot be reached, which logs a
        warning).

    Raises
    ------
    InvalidArgumentError
        If ``name`` is empty.
    """
    from astrometricslib.pipelines.shared.target_position import resolve_catalog_position

    if not isinstance(name, str) or not name.strip():
        raise InvalidArgumentError("resolve needs a non-empty name.")
    found = _target_resolution(catalog, name) or _star_resolution(storage, name)
    if found is not None:
        return found
    position = resolve_catalog_position(name, simbad)
    if position is None:
        return None
    return Resolution(
        name=name.replace("_", " ").strip(),
        ra_deg=float(position.coordinate.ra.deg),
        dec_deg=float(position.coordinate.dec.deg),
        source="simbad",
        object_type=position.object_type,
    )
