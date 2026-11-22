"""How every pipeline decides which stars are worth keeping, and saves them.

Astrometry, spectroscopy, and photometry each find their own stars, but
once a star is found, saving it works the same way for all three:

1. Throw away any star we could never name at all (`_drop_unresolved_stars`).
2. Check whether it is actually a star we already know about, just with
   a position that shifted slightly since last time
   (`_reconcile_position_only_star_ids`), or just under a different
   catalog's name for it (`_reconcile_identified_star_ids`).
3. Merge it into the catalog rather than overwriting it, since a star can
   carry data from more than one target and more than one pipeline
   (`merge_astrometry_stellar_object` and its two siblings).

`record_pipeline_stars` does all three in order. Astrometry is the one
exception: it needs the drop step's counts *before* it can finish
building its quality summary, and that summary-building work sits between
the drop and the save, so it calls the drop step itself, earlier, and
passes `already_dropped=True` here to skip repeating it.
"""

import logging
import math
import re
import sqlite3
from collections.abc import Callable
from typing import Any, NamedTuple

from astrometricslib._internal.drivers.catalog_access import POSITION_ONLY_STAR_ID_PREFIX
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.pipelines.shared.catalog_star_identity import (
    CATALOG_MATCH_RADIUS_ARCSEC,
    POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC,
    SAME_STAR_POSITION_TOLERANCE_ARCSEC,
    catalog_family,
    display_name_rank,
    identity_rank,
    merge_duplicate_into_survivor,
    order_names_by_familiarity,
)

logger = logging.getLogger(__name__)

# Matches the synthetic placeholder id assigned by
# `StarIdentifier._build_stellar_objects_from_sources` and
# `VariabilityAnalyzer.process`'s blind-detection path (optionally
# prefixed, e.g. "sess_20260101:Star_3") to a star that was never
# resolved to a real catalog id (SIMBAD/Gaia) or a position-derived
# one (FIELD_J...). A real catalog or position-derived id never
# matches this pattern.
_UNRESOLVED_STAR_ID_PATTERN = re.compile(r"^(?:.*:)?Star_\d+$")

# Prefix minted by star_identifier.identify_stars_with_wcs's Step 3 for a
# star with a solved sky position but no SIMBAD/Gaia match. Taken from
# the data access layer, which is where the catalog's own definition of
# a position-only star now lives, rather than from star_identifier --
# that would be a dependency in the wrong direction.
_POSITION_ONLY_STAR_ID_PREFIX = POSITION_ONLY_STAR_ID_PREFIX


class StarIdentificationBreakdown(NamedTuple):
    """How a batch of stars resolved; see `_drop_unresolved_stars`."""

    catalog_matched: int
    position_only: int
    unresolved: int


def is_unresolved_star(stellar_object: Any) -> bool:
    """Tell whether a star has no identity that could be saved.

    A star is unresolved when it still carries the placeholder id given to a
    detection that was never matched to a catalog star or given a
    position-derived name. `record_pipeline_stars` drops such a star right
    before saving, so a pipeline can use this to skip work on a star whose
    result would be thrown away.

    Parameters
    ----------
    stellar_object : `Any`
        A star with an ``id`` text.

    Returns
    -------
    is_unresolved : `bool`
        `True` when the id is a placeholder, so the star would be dropped at
        save.
    """
    return _UNRESOLVED_STAR_ID_PATTERN.match(stellar_object.id) is not None


def _drop_unresolved_stars(
    stellar_objects: list, *, target_id: str, pipeline_name: str
) -> tuple[list, StarIdentificationBreakdown]:
    """Filter out stars that were never resolved to a real identity.

    A star that can't be matched to SIMBAD/Gaia and can't even be
    given a stable position-derived id (its sky position couldn't be
    determined) is worthless as a persistent catalog entry -- its
    placeholder id is arbitrary and not reproducible across runs, so
    saving it would only pollute `stellar_catalog` with rows that can
    never be merged back into the real star they came from. Dropping
    it here, right before recording, keeps this rule in one place
    regardless of which pipeline (astrometry, spectroscopy,
    photometry) produced the star.

    Also logs and returns a breakdown of every star's outcome
    (catalog-matched / position-only / unresolved-and-dropped), so a
    caller worried about spurious detections has a concrete per-run
    number to look at instead of only transient DEBUG-level logging
    from the identification step itself.

    Parameters
    ----------
    stellar_objects : `list`
        Candidate stars to filter.
    target_id : `str`
        The target this batch of stars belongs to, for the log line.
    pipeline_name : `str`
        Which pipeline produced `stellar_objects` ("astrometry",
        "spectroscopy", or "photometry"), for the log line.

    Returns
    -------
    resolved : `list`
        The subset of `stellar_objects` with a real or position-derived
        identity.
    breakdown : `StarIdentificationBreakdown`
        Counts of every star's outcome, computed before filtering.
    """
    resolved = []
    catalog_matched = 0
    position_only = 0
    unresolved = 0
    for stellar_object in stellar_objects:
        if is_unresolved_star(stellar_object):
            unresolved += 1
            continue
        if stellar_object.is_catalog_identified:
            catalog_matched += 1
        else:
            position_only += 1
        resolved.append(stellar_object)

    logger.info(
        "[%s] %s star identification: %s catalog-matched, %s position-only (no catalog match), %s "
        "dropped (no sky position at all)",
        target_id,
        pipeline_name,
        catalog_matched,
        position_only,
        unresolved,
    )
    return resolved, StarIdentificationBreakdown(catalog_matched, position_only, unresolved)


def _reconcile_position_only_star_ids(
    stellar_objects: list,
    *,
    catalog_access: Any,
    target_id: str,
) -> list:
    """Reconcile and merge catalog IDs based on star positions.

    Standard naming based on position assumes we can measure star locations
    perfectly every time. In reality, tests show star positions can shift
    by 1-6 arcseconds between different runs of the same field. If we don't
    merge them, this shift causes the same physical star to be saved multiple
    times under slightly different names.

    This function checks new stars before saving them, comparing them to
    existing stars in the catalog. If they are close enough (within
    `CATALOG_MATCH_RADIUS_ARCSEC`), it merges them. This ensures we update
    the existing star instead of creating a duplicate.

    Note: This handles active pipeline outputs; legacy catalog
    deduplication is addressed separately via
    `scripts/reconcile_position_only_star_catalog.py`.

    Parameters
    ----------
    stellar_objects : `list`
        Candidate stars about to be recorded, mutated in place (each
        reassigned star's `id`/`name` are overwritten with the id of
        the existing catalog row it matched).
    catalog_access : `Any`
        Provides `list_position_only_stars` for reading the target's
        existing position-only stars.
    target_id : `str`
        The target these stars belong to. Scoped to one target both to
        keep the candidate set small and because that is where this
        catalog's own measured duplication was concentrated; a
        position-only star shared between two overlapping targets'
        fields is not reconciled by this pass.

    Returns
    -------
    stellar_objects : `list`
        The same list, for chaining alongside `_drop_unresolved_stars`.
    """
    from astropy import units as u
    from astropy.coordinates import SkyCoord

    # Every real `FIELD_J...` star has both set to real floats at the
    # same place its id is minted (star_identifier.py's Step 3); this
    # only excludes a malformed star that should never reach recording
    # in the first place.
    position_only_stars = [
        stellar_object
        for stellar_object in stellar_objects
        if stellar_object.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        and stellar_object.right_ascension is not None
        and stellar_object.declination is not None
    ]
    if not position_only_stars:
        return stellar_objects

    try:
        existing_position_only = catalog_access.list_position_only_stars(target_id=target_id)
    except (AstrometricsError, sqlite3.Error) as lookup_error:
        # Reconciliation is an optimization over an already-correct (if
        # duplicative) storage path; a lookup failure must not block
        # a run's own stars from being saved.
        logger.debug(
            "[%s] Could not read existing catalog for id reconciliation: %s", target_id, lookup_error
        )
        return stellar_objects

    if not existing_position_only:
        return stellar_objects

    existing_coords = SkyCoord(
        ra=[star.right_ascension for star in existing_position_only] * u.deg,
        dec=[star.declination for star in existing_position_only] * u.deg,
    )

    reused_ids: set[str] = set()
    reused_count = 0
    match_radius_arcsec = resolve(CATALOG_MATCH_RADIUS_ARCSEC).value
    for stellar_object in position_only_stars:
        star_coord = SkyCoord(
            ra=stellar_object.right_ascension * u.deg, dec=stellar_object.declination * u.deg
        )
        idx, d2d, _ = star_coord.match_to_catalog_sky(existing_coords)
        if d2d >= match_radius_arcsec * u.arcsec:
            continue

        existing_id = existing_position_only[idx].id
        if existing_id in reused_ids:
            # Already claimed by another star from this same run -- two
            # distinct stars should never collapse onto one row. Leave
            # this one with its own freshly minted id rather than
            # colliding; if it's a genuine duplicate of the star that
            # already claimed the match, that will still be caught the
            # next time this reconciliation runs.
            continue
        if existing_id == stellar_object.id:
            continue

        stellar_object.id = existing_id
        stellar_object.name = existing_id
        reused_ids.add(existing_id)
        reused_count += 1

    if reused_count:
        logger.info(
            "[%s] Reconciled %s position-only star id(s) onto existing catalog rows within %g "
            "arcsec, instead of minting new ones.",
            target_id,
            reused_count,
            resolve(CATALOG_MATCH_RADIUS_ARCSEC).value,
        )
    return stellar_objects


def _move_catalog_row_to_new_id(
    catalog_access: Any,
    *,
    old_id: str,
    new_id: str,
    new_name: str,
) -> bool:
    """Rename a saved star's row, keeping everything stored on it.

    The row is written under its new id first and only then is the old
    one deleted, so a failure half way leaves a duplicate row (which
    `scripts/merge_duplicate_catalog_stars.py` can clean up) rather than
    losing the star's spectra and light curve.

    Parameters
    ----------
    catalog_access : `Any`
        Provides `get_by_ids`, `merge_and_record` and `delete_by_ids`.
    old_id : `str`
        The id the row is saved under now.
    new_id : `str`
        The id to save it under.
    new_name : `str`
        The name to save it with.

    Returns
    -------
    moved : `bool`
        `True` when the row now exists under `new_id`. `False` when it
        could not be moved (no row under `old_id`, a row already under
        `new_id`, or a storage error); nothing is changed then.
    """
    try:
        if catalog_access.get_by_ids("stellar_catalog", [new_id]):
            return False
        saved_rows = catalog_access.get_by_ids("stellar_catalog", [old_id])
        if not saved_rows:
            return False
        renamed_row = saved_rows[0]
        renamed_row.id = new_id
        renamed_row.name = new_name or new_id
        catalog_access.merge_and_record(
            "stellar_catalog", [renamed_row], lambda _existing_row, updated_row: updated_row
        )
    except (AstrometricsError, sqlite3.Error, ValueError) as storage_error:
        logger.warning("Could not rename catalog row %s to %s: %s", old_id, new_id, storage_error)
        return False

    try:
        catalog_access.delete_by_ids("stellar_catalog", [old_id])
    except (AstrometricsError, sqlite3.Error) as storage_error:
        logger.warning(
            "Renamed catalog row %s to %s but could not delete the old row: %s", old_id, new_id, storage_error
        )
    return True


def _reconcile_identified_star_ids(
    stellar_objects: list,
    *,
    catalog_access: Any,
    target_id: str,
) -> list:
    """Save a star under one name even when two catalogs name it differently.

    A star identified against the Henry Draper catalog is ``HD 151086``;
    identified against Gaia it is ``Gaia DR3 1328...``. Rows are keyed by
    name, so if one run finds the first and a later run finds the second,
    the same star would be saved twice, splitting its spectra and light
    curve between two rows.

    This checks each new star's position against the catalog before
    saving. When exactly one saved row sits within
    `SAME_STAR_POSITION_TOLERANCE_ARCSEC` and that row's name comes from a
    different catalog, the two are one star and are saved as one row:

    * If the saved row's id is at least as good an identity (a Gaia DR3
      number, then HD/BD/CD/CPD, then anything else; see `identity_rank`),
      the new star takes the saved row's id.
    * If the new star's id is better, the saved row is renamed to it, so
      the row that survives carries the better id and everything already
      stored on it.

    Either way the row keeps the more familiar of the two names as its
    display name (see `display_name_rank`) and the other as an alias.

    Two names from the same catalog are different stars however close
    they are (a close double star has two Gaia ids), and a star with
    more than one saved row nearby is ambiguous, so neither is touched.

    Position-only ids are handled by `_reconcile_position_only_star_ids`
    and are not looked at here.

    Parameters
    ----------
    stellar_objects : `list`
        Candidate stars about to be recorded, mutated in place (a matched
        star's `id`/`name` are overwritten with those of the saved row).
    catalog_access : `Any`
        Provides `list_stars_in_region` to find saved rows near the
        stars, and the read, write and delete calls used to rename a row.
    target_id : `str`
        The target these stars belong to, for the log line only. Unlike
        the position-only step this is not limited to the target's own
        stars: a star imaged for two targets is one star.

    Returns
    -------
    stellar_objects : `list`
        The same list, for chaining alongside the other reconcile step.
    """
    from astropy import units as u
    from astropy.coordinates import SkyCoord, search_around_sky

    named_stars = [stellar_object for stellar_object in stellar_objects if _is_named_star(stellar_object)]
    if not named_stars:
        return stellar_objects

    star_coordinates = SkyCoord(
        ra=[star.right_ascension for star in named_stars] * u.deg,
        dec=[star.declination for star in named_stars] * u.deg,
    )
    center_right_ascension, center_declination, search_radius_degrees = _search_circle(
        star_coordinates, resolve(SAME_STAR_POSITION_TOLERANCE_ARCSEC).value
    )

    try:
        nearby_rows = catalog_access.list_stars_in_region(
            center_right_ascension, center_declination, search_radius_degrees
        )
        nearby_rows = [row for row in nearby_rows if not row.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX)]
    except (AstrometricsError, sqlite3.Error) as lookup_error:
        # Same reasoning as the position-only step: a failed lookup must
        # not stop a run's own stars being saved.
        logger.debug(
            "[%s] Could not read existing catalog for name reconciliation: %s", target_id, lookup_error
        )
        return stellar_objects
    if not nearby_rows:
        return stellar_objects

    row_coordinates = SkyCoord(
        ra=[row.right_ascension for row in nearby_rows] * u.deg,
        dec=[row.declination for row in nearby_rows] * u.deg,
    )
    star_indices, row_indices, _, _ = search_around_sky(
        star_coordinates, row_coordinates, resolve(SAME_STAR_POSITION_TOLERANCE_ARCSEC).value * u.arcsec
    )
    rows_near_star: dict[int, list] = {}
    for star_index, row_index in zip(star_indices, row_indices, strict=True):
        rows_near_star.setdefault(int(star_index), []).append(nearby_rows[int(row_index)])

    claimed_row_ids: set[str] = set()
    reused_count = renamed_count = 0
    for star_index, stellar_object in enumerate(named_stars):
        rows_here = rows_near_star.get(star_index, [])
        if not rows_here or any(row.id == stellar_object.id for row in rows_here):
            # Nothing saved nearby, or the star's own row is already there.
            continue
        if len(rows_here) > 1:
            logger.debug(
                "[%s] %s has %d saved rows within %g arcsec; leaving it for the cleanup script.",
                target_id,
                stellar_object.id,
                len(rows_here),
                resolve(SAME_STAR_POSITION_TOLERANCE_ARCSEC).value,
            )
            continue
        saved_row = rows_here[0]
        if catalog_family(saved_row.id) == catalog_family(stellar_object.id):
            continue
        if saved_row.id in claimed_row_ids:
            # Two new stars must never collapse onto one row.
            continue

        saved_name = saved_row.name or saved_row.id
        names = order_names_by_familiarity([
            stellar_object.name,
            saved_name,
            stellar_object.id,
            saved_row.id,
            *stellar_object.aliases,
        ])
        if identity_rank(stellar_object.id) < identity_rank(saved_row.id):
            kept_id = stellar_object.id
            if not _move_catalog_row_to_new_id(
                catalog_access,
                old_id=saved_row.id,
                new_id=kept_id,
                new_name=names[0],
            ):
                continue
            renamed_count += 1
        else:
            kept_id = saved_row.id
            stellar_object.id = kept_id
            reused_count += 1
        stellar_object.name = names[0]
        stellar_object.aliases = [name for name in names if name not in (kept_id, names[0])]
        claimed_row_ids.add(saved_row.id)

    if reused_count or renamed_count:
        logger.info(
            "[%s] Matched %s star(s) to a saved row under another catalog's name within %g arcsec "
            "(%s reused the saved name, %s renamed the saved row), instead of saving a second row.",
            target_id,
            reused_count + renamed_count,
            resolve(SAME_STAR_POSITION_TOLERANCE_ARCSEC).value,
            reused_count,
            renamed_count,
        )
    return stellar_objects


def _search_circle(star_coordinates: Any, margin_arcsec: float) -> tuple[float, float, float]:
    """Find one sky circle that holds every star of a run, plus a margin.

    One search covering every new star costs one lookup rather than one per
    star. The circle is centered on the average direction of the stars
    (averaging unit vectors, not right ascensions, so a field across 0/360
    degrees is centered correctly).

    Parameters
    ----------
    star_coordinates : `astropy.coordinates.SkyCoord`
        The stars' positions.
    margin_arcsec : `float`
        How far past the outermost star the circle reaches, in arcseconds.

    Returns
    -------
    center_right_ascension, center_declination, radius : `float`
        The circle's center and radius, in degrees.
    """
    from astropy import units as u
    from astropy.coordinates import SkyCoord

    average_x, average_y, average_z = star_coordinates.cartesian.xyz.value.mean(axis=1)
    center_right_ascension = math.degrees(math.atan2(average_y, average_x)) % 360.0
    center_declination = math.degrees(math.atan2(average_z, math.hypot(average_x, average_y)))
    center = SkyCoord(ra=center_right_ascension * u.deg, dec=center_declination * u.deg)
    radius_degrees = float(star_coordinates.separation(center).deg.max()) + margin_arcsec / 3600.0
    return center_right_ascension, center_declination, radius_degrees


def _is_named_star(stellar_object: Any) -> bool:
    """Tell whether a star has a catalog name and a sky position.

    Returns
    -------
    is_named : `bool`
        `True` for a star that is neither position-only nor an unresolved
        detection and that has both coordinates.
    """
    return bool(
        stellar_object.id
        and not stellar_object.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        and not _UNRESOLVED_STAR_ID_PATTERN.match(stellar_object.id)
        and stellar_object.right_ascension is not None
        and stellar_object.declination is not None
    )


def _absorb_position_only_rows(
    stellar_objects: list,
    *,
    catalog_access: Any,
    target_id: str,
) -> list[str]:
    """Fold saved position-only rows into the named stars that replace them.

    A star that an earlier run could not name (its Gaia query failed, or
    the field was too large to ask for) was saved as ``FIELD_J...``. When a
    later run names the star, the old row would stay beside the named one,
    holding part of the star's light curve. Each named star of this run
    that has a saved position-only row within
    `POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC` takes that row's data: the
    nearest such row, and each row goes to one star only. Data is only
    added (see `merge_duplicate_into_survivor`): into the named star's
    saved row when it has one, otherwise into the new star itself. The
    caller deletes the position-only rows once the run's stars are saved.

    Parameters
    ----------
    stellar_objects : `list`
        The run's stars, with their final ids. A named star with no saved
        row is changed in place.
    catalog_access : `Any`
        Provides `list_stars_in_region`, `get_by_ids` and
        `merge_and_record`.
    target_id : `str`
        The target, for the log line only.

    Returns
    -------
    absorbed_ids : `list` [`str`]
        The position-only rows whose data was taken; the caller deletes
        them after saving.
    """
    from astropy import units as u
    from astropy.coordinates import SkyCoord, search_around_sky

    named_stars = [stellar_object for stellar_object in stellar_objects if _is_named_star(stellar_object)]
    if not named_stars:
        return []
    radius_arcsec = resolve(POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC).value
    star_coordinates = SkyCoord(
        ra=[star.right_ascension for star in named_stars] * u.deg,
        dec=[star.declination for star in named_stars] * u.deg,
    )
    center_right_ascension, center_declination, search_radius_degrees = _search_circle(
        star_coordinates, radius_arcsec
    )
    try:
        position_only_rows = [
            row
            for row in catalog_access.list_stars_in_region(
                center_right_ascension, center_declination, search_radius_degrees
            )
            if row.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        ]
    except (AstrometricsError, sqlite3.Error) as lookup_error:
        logger.debug("[%s] Could not read position-only rows to fold in: %s", target_id, lookup_error)
        return []
    if not position_only_rows:
        return []

    row_coordinates = SkyCoord(
        ra=[row.right_ascension for row in position_only_rows] * u.deg,
        dec=[row.declination for row in position_only_rows] * u.deg,
    )
    star_indices, row_indices, separations, _ = search_around_sky(
        star_coordinates, row_coordinates, radius_arcsec * u.arcsec
    )
    # Closest pairs first, so each row goes to the star nearest it and each
    # star takes its nearest row.
    pairs = sorted(
        zip(separations.arcsec, star_indices, row_indices, strict=True), key=lambda pair: float(pair[0])
    )
    chosen: dict[int, str] = {}
    claimed_rows: set[str] = set()
    for _, star_index, row_index in pairs:
        row_id = position_only_rows[int(row_index)].id
        if int(star_index) in chosen or row_id in claimed_rows:
            continue
        chosen[int(star_index)] = row_id
        claimed_rows.add(row_id)
    if not chosen:
        return []

    try:
        position_only_records = {
            record.id: record for record in catalog_access.get_by_ids("stellar_catalog", sorted(claimed_rows))
        }
        named_ids = [named_stars[star_index].id for star_index in chosen]
        saved_named = {
            record.id: record for record in catalog_access.get_by_ids("stellar_catalog", named_ids)
        }
        absorbed_ids: list[str] = []
        updated_saved_rows = []
        for star_index, row_id in chosen.items():
            position_only_record = position_only_records.get(row_id)
            if position_only_record is None:
                continue
            star = named_stars[star_index]
            saved_row = saved_named.get(star.id)
            merge_duplicate_into_survivor(saved_row if saved_row is not None else star, position_only_record)
            if saved_row is not None:
                updated_saved_rows.append(saved_row)
            absorbed_ids.append(row_id)
        if updated_saved_rows:
            catalog_access.merge_and_record(
                "stellar_catalog", updated_saved_rows, lambda _existing_row, updated_row: updated_row
            )
    except (AstrometricsError, sqlite3.Error, ValueError) as storage_error:
        # Folding in is a tidy-up on top of an already correct save.
        logger.warning(
            "[%s] Could not fold position-only rows into named stars: %s", target_id, storage_error
        )
        return []

    if absorbed_ids:
        logger.info(
            "[%s] Folded %s saved position-only row(s) into the named star(s) within %g arcsec of them.",
            target_id,
            len(absorbed_ids),
            radius_arcsec,
        )
    return absorbed_ids


def _combine_names(existing_stellar_object: Any, updated_stellar_object: Any) -> None:
    """Keep the more familiar of two names of one star; the rest are aliases.

    Parameters
    ----------
    existing_stellar_object : `StellarObject`
        The saved record, changed in place.
    updated_stellar_object : `StellarObject`
        The new record of the same star.
    """
    names = order_names_by_familiarity([
        existing_stellar_object.name,
        updated_stellar_object.name,
        *existing_stellar_object.aliases,
        *updated_stellar_object.aliases,
    ])
    names = [name for name in names if not name.startswith(_POSITION_ONLY_STAR_ID_PREFIX)]
    if names and display_name_rank(names[0]) < display_name_rank(
        existing_stellar_object.name or existing_stellar_object.id
    ):
        existing_stellar_object.name = names[0]
    existing_stellar_object.aliases = [
        name for name in names if name not in (existing_stellar_object.id, existing_stellar_object.name)
    ]


def record_pipeline_stars(
    stellar_objects: list,
    *,
    catalog_access: Any,
    target_id: str,
    merge_function: Callable[..., Any],
    pipeline_name: str | None = None,
    already_dropped: bool = False,
) -> tuple[list, StarIdentificationBreakdown | None]:
    """Tag, reconcile, and save a pipeline's stars into the shared catalog.

    This is the block astrometry, spectroscopy, and photometry all run
    right before returning: every star gets tagged with this target's id,
    a position-only star gets checked against the catalog in case it is
    really one we already have (`_reconcile_position_only_star_ids`), a
    named star in case we already have it under another catalog's name
    (`_reconcile_identified_star_ids`), and the result is merged into
    `stellar_catalog` rather than overwritten, since one star can carry
    data from more than one target.

    Astrometry calls `_drop_unresolved_stars` itself, earlier, because it
    needs the star-identification breakdown to build its quality summary
    before this function runs. Pass `already_dropped=True` in that case so
    the drop step does not run twice; `breakdown` in the return value is
    then `None`, since the caller already has its own copy.

    Parameters
    ----------
    stellar_objects : `list`
        The stars this pipeline found.
    catalog_access : `Any`
        Provides catalog reads and the merge/record call.
    target_id : `str`
        The target these stars belong to.
    merge_function : callable
        One of `merge_astrometry_stellar_object`,
        `merge_spectroscopy_stellar_object`, or
        `merge_photometry_stellar_object` -- decides how a newly found
        star's fields combine with an existing catalog row for the same
        star.
    pipeline_name : `str`, optional
        Which pipeline this is, for the drop step's log line. Required
        unless `already_dropped` is `True`.
    already_dropped : `bool`, optional
        Set by astrometry, which has already called
        `_drop_unresolved_stars` itself. Defaults to `False`.

    Returns
    -------
    stellar_objects : `list`
        The saved stars, tagged and reconciled.
    breakdown : `StarIdentificationBreakdown` or `None`
        The drop step's counts, or `None` when `already_dropped` was
        `True`.
    """
    breakdown = None
    if not already_dropped:
        stellar_objects, breakdown = _drop_unresolved_stars(
            stellar_objects, target_id=target_id, pipeline_name=pipeline_name
        )

    for stellar_object in stellar_objects:
        if target_id not in stellar_object.target_ids:
            stellar_object.target_ids.append(target_id)

    stellar_objects = _reconcile_position_only_star_ids(
        stellar_objects, catalog_access=catalog_access, target_id=target_id
    )
    stellar_objects = _reconcile_identified_star_ids(
        stellar_objects, catalog_access=catalog_access, target_id=target_id
    )
    absorbed_ids = _absorb_position_only_rows(
        stellar_objects, catalog_access=catalog_access, target_id=target_id
    )
    catalog_access.merge_and_record("stellar_catalog", stellar_objects, merge_function)
    if absorbed_ids:
        try:
            catalog_access.delete_by_ids("stellar_catalog", absorbed_ids)
        except (AstrometricsError, sqlite3.Error) as storage_error:
            # The data is saved on the named rows already; a row left behind
            # is only a duplicate the cleanup script can remove.
            logger.warning("[%s] Could not delete folded position-only rows: %s", target_id, storage_error)

    return stellar_objects, breakdown


def merge_astrometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-return-type-undocumented-public-function, missing-type-function-argument]
    """Merge rule for astrometry updates to a star.

    Keeps any old target names but adds new ones. It also updates
    things we just solved (like identity or position) without throwing
    away data from other targets that might be attached to this star.

    Returns
    -------
    merged_object : StellarObject
        The combined star record.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    _combine_names(existing_stellar_object, updated_stellar_object)
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.b_minus_v = updated_stellar_object.b_minus_v
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    # A run that did not reach SIMBAD (or matched through Gaia) has an empty
    # type; it must not erase one an earlier run found.
    if updated_stellar_object.simbad_object_types:
        existing_stellar_object.simbad_object_types = updated_stellar_object.simbad_object_types
    # Likewise for the Gaia DR3 source number: keep one an earlier run found.
    if updated_stellar_object.gaia_dr3_source_id is not None:
        existing_stellar_object.gaia_dr3_source_id = updated_stellar_object.gaia_dr3_source_id
    return existing_stellar_object


def merge_spectroscopy_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-return-type-undocumented-public-function, missing-type-function-argument]
    """Merge rule for spectroscopy updates to a star.

    Adds new target names to the list and updates the light spectrum
    data and dispersion angle, but leaves everything else alone.

    The star's `star_data` position is left as it was: it is a pixel
    position in the normal (astrometry) image, while the spectroscopy
    update's `star_data` is a position in the spectroscopy image, a
    different pixel grid. The spectroscopy position travels in
    `spectroscopy.star_position_px` instead.

    Returns
    -------
    merged_object : StellarObject
        The combined star record.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    existing_stellar_object.name = updated_stellar_object.name
    _combine_names(existing_stellar_object, updated_stellar_object)
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.b_minus_v = updated_stellar_object.b_minus_v
    existing_stellar_object.is_catalog_identified = updated_stellar_object.is_catalog_identified
    # Carries the trail geometry (rectangle, dispersion_angle, etc.)
    # along for free -- it lives on SpectroscopyResult now, so a full
    # replace here covers it without copying each field separately.
    existing_stellar_object.spectroscopy = updated_stellar_object.spectroscopy
    return existing_stellar_object


def merge_photometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-return-type-undocumented-public-function, missing-type-function-argument]
    """Merge rule for photometry updates to a star.

    Adds new target names, updates cross-session identity data,
    and brings in new brightness variation (variability) metrics.

    Returns
    -------
    merged_object : StellarObject
        The combined star record.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    updated_photometry = updated_stellar_object.photometry
    existing_photometry = existing_stellar_object.photometry
    # A repeat run that couldn't recompute mean_flux/coefficient_of_variation
    # this time (too few usable flux points this session) keeps the
    # star's last known values instead of wiping them to None; every
    # other photometry field still comes fully from this run.
    # A star whose saturated points were removed is the exception: the earlier
    # values came from a light curve that still held those points, so keeping
    # them would put the removed points back into the star's statistics.
    if (
        updated_photometry is not None
        and updated_photometry.mean_flux is None
        and updated_photometry.saturated_points_removed == 0
        and existing_photometry is not None
        and existing_photometry.mean_flux is not None
    ):
        updated_photometry.mean_flux = existing_photometry.mean_flux
        updated_photometry.coefficient_of_variation = existing_photometry.coefficient_of_variation
        # The variability indices go with the CV: they describe the same
        # earlier light curve.
        for index_name in (
            "instrumental_mag",
            "rms_mag",
            "excess_scatter",
            "reduced_chi_square",
            "stetson_j",
            "variability_score",
            "false_alarm_probability",
            "false_discovery_q_value",
        ):
            setattr(updated_photometry, index_name, getattr(existing_photometry, index_name))
    existing_stellar_object.photometry = updated_photometry
    # Cross-session matching (see _match_and_merge_across_sessions)
    # recomputes both fresh each run, so a full replace keeps a repeat
    # run's result authoritative rather than accumulating stale matches.
    existing_stellar_object.session_matches = updated_stellar_object.session_matches
    if updated_stellar_object.right_ascension:
        existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
        existing_stellar_object.declination = updated_stellar_object.declination
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    return existing_stellar_object
