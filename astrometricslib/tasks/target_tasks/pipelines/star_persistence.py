"""How every pipeline decides which stars are worth keeping, and saves them.

Astrometry, spectroscopy, and photometry each find their own stars, but
once a star is found, saving it works the same way for all three:

1. Throw away any star we could never name at all (`_drop_unresolved_stars`).
2. Check whether it is actually a star we already know about, just with
   a position that shifted slightly since last time
   (`_reconcile_position_only_star_ids`).
3. Merge it into the catalog rather than overwriting it, since a star can
   carry data from more than one target and more than one pipeline
   (`merge_astrometry_stellar_object` and its two siblings).

`persist_pipeline_stars` does all three in order. Astrometry is the one
exception: it needs the drop step's counts *before* it can finish
building its quality summary, and that summary-building work sits between
the drop and the save, so it calls the drop step itself, earlier, and
passes `already_dropped=True` here to skip repeating it.
"""

import logging
import re
from typing import NamedTuple

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
# star with a solved sky position but no SIMBAD/Gaia match. Duplicated
# here rather than imported, matching _UNRESOLVED_STAR_ID_PATTERN's own
# precedent of matching the format by convention instead of taking a
# dependency in the other direction.
_POSITION_ONLY_STAR_ID_PREFIX = "FIELD_J"


class StarIdentificationBreakdown(NamedTuple):
    """How a batch of stars resolved; see `_drop_unresolved_stars`."""

    catalog_matched: int
    position_only: int
    unresolved: int


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
    it here, right before persistence, keeps this rule in one place
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
        if _UNRESOLVED_STAR_ID_PATTERN.match(stellar_object.id):
            unresolved += 1
            continue
        if stellar_object.is_catalog_identified:
            catalog_matched += 1
        else:
            position_only += 1
        resolved.append(stellar_object)

    logger.info(
        f"[{target_id}] {pipeline_name} star identification: {catalog_matched} catalog-matched, "
        f"{position_only} position-only (no catalog match), {unresolved} dropped (no sky position at all)"
    )
    return resolved, StarIdentificationBreakdown(catalog_matched, position_only, unresolved)


def _reconcile_position_only_star_ids(
    stellar_objects: list,
    *,
    butler,  # ruff: ignore[missing-type-function-argument]
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
        Candidate stars about to be persisted, mutated in place (each
        reassigned star's `id`/`name` are overwritten with the id of
        the existing catalog row it matched).
    butler : `Any`
        Provides `list_projected` for reading the target's existing
        position-only rows.
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

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
        CATALOG_MATCH_RADIUS_ARCSEC,
    )

    # `StellarObject.right_ascension`/`.declination` are typed `Any` and
    # default to `""`, not `None` -- an `is not None` check alone would
    # let that default through and crash the `SkyCoord` arithmetic
    # below. Every real `FIELD_J...` star has both set to real floats at
    # the same place its id is minted (star_identifier.py's Step 3), so
    # this only excludes a malformed star that should never reach
    # persistence in the first place.
    position_only_stars = [
        stellar_object
        for stellar_object in stellar_objects
        if stellar_object.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        and isinstance(stellar_object.right_ascension, int | float)
        and isinstance(stellar_object.declination, int | float)
    ]
    if not position_only_stars:
        return stellar_objects

    try:
        existing_rows = butler.list_projected("stellar_catalog", ["id", "ra", "dec", "target_id"])
    except Exception as lookup_error:
        # Reconciliation is an optimization over an already-correct (if
        # duplicative) persistence path; a lookup failure must not block
        # a run's own stars from being saved.
        logger.debug(
            "[%s] Could not read existing catalog for id reconciliation: %s", target_id, lookup_error
        )
        return stellar_objects

    # target_id is a comma-joined string (a star can belong to more than
    # one target), so membership is checked in Python -- same reasoning
    # as StellarCatalog.list_object_summaries's identical filter.
    existing_position_only = [
        row
        for row in existing_rows
        if row["id"].startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        and row["ra"] is not None
        and row["dec"] is not None
        and target_id in (row["target_id"] or "").split(",")
    ]
    if not existing_position_only:
        return stellar_objects

    existing_coords = SkyCoord(
        ra=[row["ra"] for row in existing_position_only] * u.deg,
        dec=[row["dec"] for row in existing_position_only] * u.deg,
    )

    reused_ids: set[str] = set()
    reused_count = 0
    for stellar_object in position_only_stars:
        star_coord = SkyCoord(
            ra=stellar_object.right_ascension * u.deg, dec=stellar_object.declination * u.deg
        )
        idx, d2d, _ = star_coord.match_to_catalog_sky(existing_coords)
        if d2d >= CATALOG_MATCH_RADIUS_ARCSEC * u.arcsec:
            continue

        existing_id = existing_position_only[idx]["id"]
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
            f"[{target_id}] Reconciled {reused_count} position-only star id(s) onto existing "
            f"catalog rows within {CATALOG_MATCH_RADIUS_ARCSEC:g} arcsec, instead of minting new ones."
        )
    return stellar_objects


def persist_pipeline_stars(
    stellar_objects: list,
    *,
    butler,  # ruff: ignore[missing-type-function-argument]
    target_id: str,
    merge_function,  # ruff: ignore[missing-type-function-argument]
    pipeline_name: str | None = None,
    already_dropped: bool = False,
) -> tuple[list, StarIdentificationBreakdown | None]:
    """Tag, reconcile, and save a pipeline's stars into the shared catalog.

    This is the block astrometry, spectroscopy, and photometry all run
    right before returning: every star gets tagged with this target's id,
    a position-only star gets checked against the catalog in case it is
    really one we already have (`_reconcile_position_only_star_ids`), and
    the result is merged into `stellar_catalog` rather than overwritten,
    since one star can carry data from more than one target.

    Astrometry calls `_drop_unresolved_stars` itself, earlier, because it
    needs the star-identification breakdown to build its quality summary
    before this function runs. Pass `already_dropped=True` in that case so
    the drop step does not run twice; `breakdown` in the return value is
    then `None`, since the caller already has its own copy.

    Parameters
    ----------
    stellar_objects : `list`
        The stars this pipeline found.
    butler : `Any`
        Provides catalog reads and the merge/persist call.
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

    stellar_objects = _reconcile_position_only_star_ids(stellar_objects, butler=butler, target_id=target_id)
    butler.merge_and_persist_records("stellar_catalog", stellar_objects, merge_function)

    return stellar_objects, breakdown


def merge_astrometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    return existing_stellar_object


def merge_spectroscopy_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Merge rule for spectroscopy updates to a star.

    Adds new target names to the list and updates the light spectrum
    data and dispersion angle, but leaves everything else alone.

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
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.is_catalog_identified = updated_stellar_object.is_catalog_identified
    existing_stellar_object.star_data = updated_stellar_object.star_data
    existing_stellar_object.detected_angle = updated_stellar_object.detected_angle
    existing_stellar_object.dispersion_angle = updated_stellar_object.dispersion_angle
    existing_stellar_object.trail_centerline_px = updated_stellar_object.trail_centerline_px
    existing_stellar_object.trail_width_px = updated_stellar_object.trail_width_px
    existing_stellar_object.rectangle = updated_stellar_object.rectangle
    existing_stellar_object.spectrum_data_processed = updated_stellar_object.spectrum_data_processed
    return existing_stellar_object


def merge_photometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
    existing_stellar_object.light_curve = updated_stellar_object.light_curve
    if getattr(updated_stellar_object, "mean_flux", None) is not None:
        existing_stellar_object.mean_flux = updated_stellar_object.mean_flux
        existing_stellar_object.coefficient_of_variation = updated_stellar_object.coefficient_of_variation
        existing_stellar_object.variability_score = updated_stellar_object.variability_score
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
