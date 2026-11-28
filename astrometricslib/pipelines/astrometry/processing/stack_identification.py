"""Names every star a stack's own star finder detected, from Gaia and SIMBAD.

The viewer's Astrometry button on a stack asks for this. It works on the
stack only, never on a raw frame. It creates no star record; its one write
is to add missing SIMBAD names as aliases to records the library already
has (step 6). The steps are:

1. **The plate solution.** A stack whose header carries a real plate solution
   (``PLTSOLVD``) keeps it. Otherwise the stack is solved and the solution is
   written into the stack and its viewer copy (see
   `stage.ensure_stack_plate_solution`).
2. **The detections.** The stack's saved detection list is used (see
   `detections.measure_detections_in_file`), so the stars named are the
   stars the Detections overlay shows.
3. **The Gaia tiles.** Every Gaia cache tile under the stack is filled to the
   depth the stack's equipment predicts (see `gaia_field_stars`), and only
   complete tiles are read. Gaia positions are moved to the stack's date.
4. **Matching.** Each detection takes the nearest Gaia star within the
   catalog match radius. When SIMBAD can be asked, its stars in the field are
   matched the same way. The matching and naming are the ones an astrometry
   run uses (`catalog_matching`, `catalog_star_identity.name_simbad_star`),
   so a detection takes the same most familiar name, and the same library id
   (``library_id``), that the library gives the star. Offline, names are
   Gaia's only, and a note says why. A detection with no catalog star is
   ``Unnamed star`` and has no library id.
5. **Catalog stars with no detection.** Gaia stars inside the stack that are
   one magnitude brighter than the predicted detection limit (where nearly
   every isolated star is found), but have no detection within the match
   radius, are listed so the viewer can show what was missed.
6. **Aliases.** A detection matched by Gaia number to a SIMBAD star whose
   record (``Gaia DR3 <id>``) is already in the library gains the star's
   other SIMBAD names as aliases, so the star card shows the name the image
   shows. Only missing aliases are added.

The result is saved next to the stack (``<stack>_identification.json``) with
the stack's size and modification time, and `read_stack_identification`
returns it while the stack is unchanged. Star positions are 1-based pixels
(the center of the first pixel is 1, 1), as in FITS and DS9. A file saved
before stars carried a library id gets one when read, from the star's Gaia
number or its SIMBAD name. A file of the previous layout, which kept 0-based
positions, is moved to 1-based pixels when read.
"""

import logging
import math
import os
import sqlite3
from collections.abc import Callable
from typing import Any

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS

from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib.foundation.errors import AstrometricsError, InvalidArgumentError
from astrometricslib.foundation.offline import network_is_offline
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_abandonment import RunAbandonedError
from astrometricslib.models.stack_identification import IdentifiedStar, StackIdentification
from astrometricslib.pipelines.astrometry.processing.catalog_matching import (
    nearest_catalog_entries,
    pixels_to_sky,
)
from astrometricslib.pipelines.astrometry.processing.detections import measure_detections_in_file
from astrometricslib.pipelines.astrometry.processing.gaia_field_matching import (
    propagate_gaia_positions,
    read_observation_time,
)
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import (
    TileFetcher,
    load_field_gaia_stars,
    tiles_under_image,
)
from astrometricslib.pipelines.astrometry.processing.gaia_tile_queries import (
    make_gaia_tile_fetcher,
)
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.pipelines.shared.catalog_star_identity import (
    CATALOG_MATCH_RADIUS_ARCSEC,
    SimbadStarNames,
    name_simbad_star,
)
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.shared.simbad_object_types import simbad_row_identifiers, without_name_prefix
from astrometricslib.pipelines.shared.stack_sidecar import (
    read_stamped_record,
    sidecar_path,
    write_stamped_record,
)
from astrometricslib.utilities.gaia_record_ids import GAIA_DR3_PREFIX, gaia_record_id

__all__ = [
    "IDENTIFICATION_SIDECAR_SUFFIX",
    "UNNAMED_STAR",
    "identify_stack",
    "identify_stack_stars",
    "read_stack_identification",
]

logger = logging.getLogger(__name__)

# What follows the stack's name (without .fits) in the saved result's name.
IDENTIFICATION_SIDECAR_SUFFIX = "_identification.json"

# Raise this when the layout of the saved file changes. Version 4 keeps the
# star positions in 1-based pixels; version 3 kept them 0-based.
IDENTIFICATION_FILE_VERSION = 4
_ZERO_BASED_FILE_VERSION = 3

# The key that holds the stack's stamp (size and modification time) in the
# saved file.
_STAMP_KEY = "stack_stamp"

# The most catalog stars with no detection listed, brightest first, so a
# dense field does not send tens of thousands of markers to the viewer. A
# display limit only; it changes no name.
CATALOG_ONLY_LIMIT = 3000

# Catalog stars with no detection are listed only when they are this much
# brighter than the predicted limit, where the finder should find nearly
# every isolated star: on the NGC 7331 L stack (2026-11-20, AST-D21) isolated
# Gaia stars were found 92 to 100 percent of the time between G 17.1 and
# 18.4, one magnitude brighter than the 50 percent point of G 19.1. A display
# choice; it changes no name.
CATALOG_ONLY_MARGIN_MAG = 1.0

# The name of a detection no catalog star is close to. The library's one copy
# of this text; the viewer keeps its own display string.
UNNAMED_STAR = "Unnamed star"

# The share of the work each step stands for, for the progress report.
_PROGRESS_SOLVED = 0.25
_PROGRESS_DETECTED = 0.35
_PROGRESS_TILES_DONE = 0.8
_PROGRESS_SIMBAD_DONE = 0.9

ProgressReport = Callable[[float, str], None]
StopCheck = Callable[[], bool]


def read_stack_identification(stack_path: str) -> StackIdentification | None:
    """Read the saved identification of a stack, if the stack is unchanged.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    identification : `StackIdentification` or `None`
        The saved result, or `None` when there is none, it cannot be read,
        or the stack changed since.
    """
    saved = sidecar_path(stack_path, IDENTIFICATION_SIDECAR_SUFFIX)
    record = read_stamped_record(saved, stack_path, IDENTIFICATION_FILE_VERSION, _STAMP_KEY)
    shift = 0.0
    if record is None:
        # A file of the previous layout holds 0-based positions.
        record = read_stamped_record(saved, stack_path, _ZERO_BASED_FILE_VERSION, _STAMP_KEY)
        shift = 1.0
    if record is None:
        return None
    try:
        identification = StackIdentification.model_validate(record["identification"])
    except (ValueError, KeyError, TypeError) as error:
        logger.debug("Could not read the saved identification %s: %s", saved, error)
        return None
    for star in identification.stars:
        star.x += shift
        star.y += shift
    for star in identification.stars:
        if star.library_id is None and star.kind != "unmatched":
            # A file saved before stars carried a library id.
            star.library_id = _library_id(
                star.gaia_source_id, star.name if star.name_source == "simbad" else None
            )
    return identification


def _library_id(gaia_source_id: str | None, simbad_record_id: str | None) -> str | None:
    """Give the id the library saves (or will save) a named star under.

    It follows the save rule of an astrometry run: a star SIMBAD names takes
    SIMBAD's record id (see `catalog_star_identity.name_simbad_star`), and
    any other star with a Gaia number is saved as ``Gaia DR3 <number>``.

    Parameters
    ----------
    gaia_source_id : `str` or `None`
        The Gaia DR3 number of the star's Gaia match, if any.
    simbad_record_id : `str` or `None`
        The record id of the star's SIMBAD match, if any.

    Returns
    -------
    library_id : `str` or `None`
        The id, or `None` for a star no catalog names.
    """
    if simbad_record_id:
        return simbad_record_id
    return gaia_record_id(gaia_source_id) if gaia_source_id else None


def _save(stack_path: str, identification: StackIdentification) -> None:
    """Save the result next to the stack, with the stack's stamp.

    A failure is logged and ignored: the file is only a shortcut.

    Parameters
    ----------
    stack_path : `str`
        The stack.
    identification : `StackIdentification`
        The result.
    """
    write_stamped_record(
        sidecar_path(stack_path, IDENTIFICATION_SIDECAR_SUFFIX),
        stack_path,
        IDENTIFICATION_FILE_VERSION,
        _STAMP_KEY,
        {"identification": identification.model_dump()},
    )


def _check(should_stop: StopCheck | None) -> None:
    """Stop the work when the person asked for it.

    Parameters
    ----------
    should_stop : `Callable` or `None`
        Returns True when the work should stop.

    Raises
    ------
    RunAbandonedError
        When `should_stop` says so: the person stopped the identification
        before it finished.
    """
    if should_stop is not None and should_stop():
        raise RunAbandonedError("The star identification was stopped.")


def _report(progress: ProgressReport | None, fraction: float, message: str) -> None:
    """Pass one progress step to the caller, if it asked for them.

    Parameters
    ----------
    progress : `Callable` or `None`
        Takes the share done (0 to 1) and a sentence.
    fraction : `float`
        The share done.
    message : `str`
        What is being done.
    """
    if progress is not None:
        progress(fraction, message)


def _counting_fetcher(
    fetch: TileFetcher, missing: int, progress: ProgressReport | None, should_stop: StopCheck | None
) -> TileFetcher:
    """Wrap a tile fetcher so it reports progress and can be stopped.

    Parameters
    ----------
    fetch : `TileFetcher`
        The fetcher to wrap.
    missing : `int`
        How many tiles are to be fetched.
    progress : `Callable` or `None`
        The progress report.
    should_stop : `Callable` or `None`
        The stop check.

    Returns
    -------
    wrapped : `TileFetcher`
        The same fetcher, which checks for a stop before each tile.
    """
    done = {"count": 0}

    def wrapped(level: int, tile: int, have: float, want: float) -> Any:
        """Fetch one tile after a stop check, then report.

        Parameters
        ----------
        level, tile : `int`
            The tile.
        have, want : `float`
            The depths.

        Returns
        -------
        answer : `TileFetch`
            The tile's answer.
        """
        _check(should_stop)
        answer = fetch(level, tile, have, want)
        done["count"] += 1
        share = _PROGRESS_DETECTED + (_PROGRESS_TILES_DONE - _PROGRESS_DETECTED) * min(
            1.0, done["count"] / max(1, missing)
        )
        _report(progress, share, f"Downloading Gaia sky tiles ({done['count']} of about {missing})")
        return answer

    return wrapped


def _simbad_names(
    identifier: StarIdentifier, wcs: WCS, width: int, height: int
) -> tuple[SkyCoord | None, list[SimbadStarNames], str]:
    """Ask SIMBAD for the stars of the field and name each one.

    SIMBAD can return more than one row for one star. Rows with the same
    Gaia DR3 number are joined into one star that holds every identifier of
    them, and the stars are put in a fixed order (by position), so the name
    chosen for a star does not depend on the order of SIMBAD's rows. Each
    star is named by the step an astrometry run uses
    (`catalog_star_identity.name_simbad_star`).

    Parameters
    ----------
    identifier : `StarIdentifier`
        Holds the SIMBAD driver.
    wcs : `astropy.wcs.WCS`
        The stack's plate solution.
    width, height : `int`
        The stack's size.

    Returns
    -------
    coords : `SkyCoord` or `None`
        SIMBAD's star positions, or `None` when there are none.
    names : `list` [`SimbadStarNames`]
        The record id, most familiar name, Gaia number and aliases of each.
    note : `str`
        Why there are no SIMBAD names; empty when there are.
    """
    if network_is_offline():
        return None, [], "Offline: the names come from Gaia only, because SIMBAD cannot be asked."
    center = wcs.pixel_to_world(width / 2.0, height / 2.0)
    corner = wcs.pixel_to_world(0.0, 0.0)
    radius = float(center.separation(corner).deg) * 1.02
    try:
        table, coords = identifier._query_simbad_region(
            float(center.ra.deg), float(center.dec.deg), wcs, width, height, radius_deg_override=radius
        )
    except AstrometricsError as error:
        logger.info("SIMBAD could not be asked for the stack's names: %s", error)
        table, coords = None, None
    if table is None or coords is None or len(table) == 0:
        return None, [], "SIMBAD did not answer, so the names come from Gaia only."
    # One entry per star: [main ids, identifiers, position], keyed by the
    # Gaia number or (for a star with none) by the row number.
    stars: dict[str, list[Any]] = {}
    for row_number, row in enumerate(table):
        main_id, identifiers = simbad_row_identifiers(row)
        gaia = next((part for part in identifiers if part.startswith(GAIA_DR3_PREFIX)), None)
        key = f"gaia {gaia.split()[-1]}" if gaia else f"row {row_number}"
        position = (float(coords[row_number].ra.deg), float(coords[row_number].dec.deg))
        if key in stars:
            stars[key][0].append(main_id)
            stars[key][1].extend(identifiers)
            stars[key][2] = min(stars[key][2], position)
        else:
            stars[key] = [[main_id], list(identifiers), position]
    ordered = sorted(
        (
            (sorted(set(identifiers)), position, min(main_ids))
            for main_ids, identifiers, position in stars.values()
        ),
        key=lambda star: (star[1], star[0]),
    )
    names = [name_simbad_star(main_id, identifiers) for identifiers, _, main_id in ordered]
    sorted_coords = SkyCoord(
        ra=[position[0] for _, position, _ in ordered] * u.deg,
        dec=[position[1] for _, position, _ in ordered] * u.deg,
    )
    return sorted_coords, names, ""


def _record_simbad_aliases(
    config: Any, stars: list[IdentifiedStar], simbad_names: list[SimbadStarNames]
) -> int:
    """Add the SIMBAD names of the named stars to their saved star records.

    Only a star whose Gaia number SIMBAD lists, and that the library already
    has a record for (saved as ``Gaia DR3 <number>``), is touched. Only names
    the record does not already have are added, to its aliases. No record is
    created, and no id, name or other field is changed.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which say where the star catalog is.
    stars : `list` [`IdentifiedStar`]
        The identified stars, with their SIMBAD names and aliases.
    simbad_names : `list` [`SimbadStarNames`]
        SIMBAD's stars of the field. A star matched only by position, whose
        Gaia number SIMBAD does not list, is not trusted to be the same star.

    Returns
    -------
    updated : `int`
        How many records gained at least one alias; 0 when the catalog
        cannot be read or written.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess

    listed = {str(names.gaia_source_id) for names in simbad_names if names.gaia_source_id is not None}
    wanted: dict[str, list[str]] = {}
    for star in stars:
        if star.gaia_source_id in listed and star.name_source == "simbad" and star.aliases:
            wanted[gaia_record_id(star.gaia_source_id)] = [star.name, *star.aliases]
    if not wanted:
        return 0
    try:
        catalog_access = CatalogAccess(config)
        records = catalog_access.get_by_ids("stellar_catalog", sorted(wanted))
        gained: dict[str, list[str]] = {}
        for record in records:
            known = {without_name_prefix(name) for name in (record.id, record.name, *record.aliases) if name}
            missing = [name for name in wanted[record.id] if name not in known]
            if missing:
                gained[record.id] = list(dict.fromkeys(missing))
        if not gained:
            return 0

        def add_aliases(existing: Any | None, updated: Any) -> Any:
            """Add the new names to the saved record, changing nothing else.

            Parameters
            ----------
            existing : `StellarObject` or `None`
                The record as saved now.
            updated : `StellarObject`
                The record read a moment ago.

            Returns
            -------
            record : `StellarObject`
                The saved record with the new aliases.
            """
            target = existing if existing is not None else updated
            have = {without_name_prefix(name) for name in (target.id, target.name, *target.aliases) if name}
            target.aliases = [*target.aliases, *(name for name in gained[updated.id] if name not in have)]
            return target

        catalog_access.merge_and_record(
            "stellar_catalog", [record for record in records if record.id in gained], add_aliases
        )
    except (AstrometricsError, sqlite3.Error, OSError) as error:
        logger.warning("The SIMBAD names could not be added to the star records: %s", error)
        return 0
    return len(gained)


def _viewer_copy_place(stack_path: str, width: int, height: int) -> tuple[int, int, int, int]:
    """Find where the viewer's copy of the stack sits on the stack.

    Parameters
    ----------
    stack_path : `str`
        The stack.
    width, height : `int`
        The stack's size.

    Returns
    -------
    place : `tuple` [`int`, `int`, `int`, `int`]
        ``(x0, y0, width, height)`` of the copy on the stack: the whole stack
        when there is no copy, it has the same size, or its crop is unknown.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_crop import (
        CROP_HEIGHT_KEYWORD,
        CROP_WIDTH_KEYWORD,
        CROP_X0_KEYWORD,
        CROP_Y0_KEYWORD,
    )
    from astrometricslib.utilities.stack_preview_path import processed_fits_path_for

    copy_path = processed_fits_path_for(stack_path)
    if not os.path.isfile(copy_path):
        return 0, 0, width, height
    try:
        copy_header = read_header(copy_path)
    except OSError:
        return 0, 0, width, height
    size = (copy_header.get("NAXIS1"), copy_header.get("NAXIS2"))
    if size == (width, height):
        return 0, 0, width, height
    crop = [
        copy_header.get(key)
        for key in (CROP_X0_KEYWORD, CROP_Y0_KEYWORD, CROP_WIDTH_KEYWORD, CROP_HEIGHT_KEYWORD)
    ]
    if None in crop or (crop[2], crop[3]) != size:
        return 0, 0, width, height
    return int(crop[0]), int(crop[1]), int(crop[2]), int(crop[3])


def _read_solution(stack_path: str, drivers: Any) -> tuple[WCS, bool, Any]:
    """Give the stack's plate solution, solving and saving it if needed.

    Parameters
    ----------
    stack_path : `str`
        The stack.
    drivers : `Drivers` or `None`
        The plate solver to use when a solve is needed.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The solution.
    reused : `bool`
        True when the stack already carried it.
    header : `astropy.io.fits.Header`
        The stack's header after any write.

    Raises
    ------
    InvalidArgumentError
        When the stack cannot be solved.
    """
    from astrometricslib._internal.drivers.fits_access import header_is_plate_solved
    from astrometricslib.pipelines.stacking.stage import ensure_stack_plate_solution

    header = read_header(stack_path)
    reused = header_is_plate_solved(header)
    wcs, reason, _ = ensure_stack_plate_solution(stack_path, drivers)
    if wcs is None:
        raise InvalidArgumentError(f"The stack could not be plate-solved: {reason}.")
    if not reused:
        header = read_header(stack_path)
    saved_wcs = celestial_wcs(header)
    return (saved_wcs if saved_wcs is not None else wcs), reused, header


def identify_stack_stars(
    stack_path: str,
    *,
    config: Any = None,
    drivers: Any = None,
    progress: ProgressReport | None = None,
    should_stop: StopCheck | None = None,
) -> StackIdentification:
    """Name every detected star of an imaging stack (see the module text).

    Parameters
    ----------
    stack_path : `str`
        The imaging stack. A raw frame is refused.
    config : `AppConfiguration`, optional
        The settings.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.
    progress : `Callable`, optional
        Called with the share done (0 to 1) and a sentence.
    should_stop : `Callable`, optional
        Returns True to stop; the work stops before its next step or tile.

    Returns
    -------
    identification : `StackIdentification`
        The named stars. It is also saved next to the stack.

    Raises
    ------
    InvalidArgumentError
        When the file is not a stack, or it cannot be plate-solved.

    Notes
    -----
    A `RunAbandonedError` passes through when `should_stop`
    asked to stop.
    """
    if config is None:
        from astrometricslib.foundation.config import get_configuration

        config = get_configuration()
    header = read_header(stack_path)
    if int(header.get("STACKCNT", 0) or 0) <= 1:
        raise InvalidArgumentError("Star names are made for stacks only, not for single frames.")

    _report(progress, 0.05, "Checking the stack's plate solution")
    wcs, reused, header = _read_solution(stack_path, drivers)
    _check(should_stop)
    width, height = int(header["NAXIS1"]), int(header["NAXIS2"])
    _report(progress, _PROGRESS_SOLVED, "Reading the stack's detected stars")
    detections = measure_detections_in_file(stack_path).detections
    _check(should_stop)

    identifier = StarIdentifier(config, drivers=drivers)
    identifier.use_image_header(header)
    prediction = identifier.gaia_depth_prediction
    depth = identifier._gaia_query_depth()
    tiles = tiles_under_image(wcs, width, height)
    _report(progress, _PROGRESS_DETECTED, "Filling the Gaia sky tiles under the stack")
    fetch, reason = make_gaia_tile_fetcher()
    from astrometricslib._internal.drivers import catalog_store

    missing_before = len(catalog_store.missing_tiles(config, tiles, depth))
    if fetch is not None:
        fetch = _counting_fetcher(fetch, missing_before, progress, should_stop)
    field = load_field_gaia_stars(config, tiles, depth, fetch, skipped_reason=reason)
    _check(should_stop)

    notes: list[str] = []
    if field.missing_tiles:
        why = field.skipped_reason or "their download failed"
        notes.append(
            f"{len(field.missing_tiles)} of {len(field.tiles)} Gaia sky tiles are not in the cache ({why}); "
            "stars there are not named from Gaia."
        )

    # Gaia positions at the stack's date.
    gaia_coords = None
    gaia_table = field.table
    if gaia_table is not None:
        observation_time = read_observation_time(header)
        ra = np.asarray(gaia_table["ra"], dtype=float)
        dec = np.asarray(gaia_table["dec"], dtype=float)
        if observation_time is not None:
            ra, dec = propagate_gaia_positions(
                ra,
                dec,
                np.asarray(gaia_table["pmra"], dtype=float),
                np.asarray(gaia_table["pmdec"], dtype=float),
                observation_time,
            )
        gaia_coords = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)

    _report(progress, _PROGRESS_TILES_DONE, "Asking SIMBAD for the familiar names")
    simbad_coords, simbad_names, simbad_note = _simbad_names(identifier, wcs, width, height)
    if simbad_note:
        notes.append(simbad_note)
    _check(should_stop)
    _report(progress, _PROGRESS_SIMBAD_DONE, "Matching the detections to the catalogs")

    stars = _match(detections, wcs, gaia_table, gaia_coords, simbad_coords, simbad_names)
    alias_records_updated = _record_simbad_aliases(config, stars, simbad_names)
    predicted_limit = (
        float(prediction.limiting_magnitude)
        if prediction is not None and prediction.limiting_magnitude is not None
        else depth - 1.0
    )
    limit = predicted_limit - CATALOG_ONLY_MARGIN_MAG
    catalog_only = _catalog_only(stars, wcs, width, height, gaia_table, gaia_coords, limit)
    matched = sum(1 for star in stars if star.kind == "matched")
    offset_x, offset_y, viewer_width, viewer_height = _viewer_copy_place(stack_path, width, height)
    identification = StackIdentification(
        path=stack_path,
        width=width,
        height=height,
        solution_reused=reused,
        stars=[*stars, *catalog_only],
        matched_count=matched,
        unmatched_count=len(stars) - matched,
        catalog_only_count=len(catalog_only),
        gaia_depth=float(depth),
        catalog_only_limit=limit,
        gaia_tiles_missing=len(field.missing_tiles),
        simbad_used=simbad_coords is not None,
        notes=notes,
        viewer_offset_x=offset_x,
        viewer_offset_y=offset_y,
        viewer_width=viewer_width,
        viewer_height=viewer_height,
        alias_records_updated=alias_records_updated,
    )
    _save(stack_path, identification)
    _report(progress, 1.0, "Done")
    return identification


def _match(
    detections: list[Any],
    wcs: WCS,
    gaia_table: Any,
    gaia_coords: SkyCoord | None,
    simbad_coords: SkyCoord | None,
    simbad_names: list[SimbadStarNames],
) -> list[IdentifiedStar]:
    """Give each detection its catalog star, name and library id.

    A detection whose Gaia star SIMBAD lists by Gaia number takes that
    SIMBAD star's name; otherwise it takes the nearest SIMBAD star within the
    match radius, and otherwise the Gaia number. The detections are placed on
    the sky through the full plate solution, distortion terms included
    (`catalog_matching.pixels_to_sky`).

    Parameters
    ----------
    detections : `list`
        The stack's detected stars (1-based ``x`` and ``y``).
    wcs : `astropy.wcs.WCS`
        The stack's plate solution.
    gaia_table : `astropy.table.Table` or `None`
        The Gaia stars of the filled tiles.
    gaia_coords : `SkyCoord` or `None`
        Their positions at the stack's date, in the table's order.
    simbad_coords : `SkyCoord` or `None`
        SIMBAD's star positions.
    simbad_names : `list` [`SimbadStarNames`]
        The names of SIMBAD's stars, in the order of `simbad_coords`.

    Returns
    -------
    stars : `list` [`IdentifiedStar`]
        One per detection, in the detections' order.
    """
    if not detections:
        return []
    radius = float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value)
    pixel_x = np.array([detection.x - 1.0 for detection in detections])
    pixel_y = np.array([detection.y - 1.0 for detection in detections])
    ra, dec = pixels_to_sky(wcs, pixel_x, pixel_y)
    coords = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    gaia_index, gaia_separation = nearest_catalog_entries(coords, gaia_coords)
    simbad_index, simbad_separation = nearest_catalog_entries(coords, simbad_coords)
    simbad_by_gaia = {
        str(names.gaia_source_id): index
        for index, names in enumerate(simbad_names)
        if names.gaia_source_id is not None
    }
    stars: list[IdentifiedStar] = []
    for number in range(len(detections)):
        source_id = g_mag = None
        star_ra, star_dec = float(ra[number]), float(dec[number])
        if gaia_separation[number] < radius and gaia_table is not None and gaia_coords is not None:
            row = gaia_table[int(gaia_index[number])]
            source_id = str(row["source_id"])
            magnitude = float(row["phot_g_mean_mag"])
            g_mag = magnitude if math.isfinite(magnitude) else None
            star_ra = float(gaia_coords[int(gaia_index[number])].ra.deg)
            star_dec = float(gaia_coords[int(gaia_index[number])].dec.deg)
        chosen: SimbadStarNames | None = None
        if source_id is not None and source_id in simbad_by_gaia:
            chosen = simbad_names[simbad_by_gaia[source_id]]
        elif simbad_separation[number] < radius:
            chosen = simbad_names[int(simbad_index[number])]
        if chosen is not None:
            name, name_source, aliases = chosen.name, "simbad", list(chosen.aliases)
        elif source_id is not None:
            name, name_source, aliases = gaia_record_id(source_id), "gaia", []
        else:
            name, name_source, aliases = UNNAMED_STAR, "none", []
        stars.append(
            IdentifiedStar(
                id=f"detection-{number}",
                name=name,
                kind="matched" if name_source != "none" else "unmatched",
                x=float(pixel_x[number]) + 1.0,
                y=float(pixel_y[number]) + 1.0,
                ra_deg=star_ra,
                dec_deg=star_dec,
                g_mag=g_mag,
                gaia_source_id=source_id,
                name_source=name_source,
                aliases=aliases,
                library_id=_library_id(source_id, chosen.record_id if chosen is not None else None),
            )
        )
    return stars


def _catalog_only(
    stars: list[IdentifiedStar],
    wcs: WCS,
    width: int,
    height: int,
    gaia_table: Any,
    gaia_coords: SkyCoord | None,
    limit: float,
) -> list[IdentifiedStar]:
    """List the Gaia stars that should show in the stack but have no detection.

    Returns
    -------
    stars : `list` [`IdentifiedStar`]
        Gaia stars brighter than `limit`, inside the stack, with no detection
        within the match radius, brightest first, at most
        `CATALOG_ONLY_LIMIT`.
    """
    if gaia_table is None or gaia_coords is None or len(gaia_table) == 0:
        return []
    magnitudes = np.asarray(gaia_table["phot_g_mean_mag"], dtype=float)
    bright = np.flatnonzero(np.isfinite(magnitudes) & (magnitudes < limit))
    if bright.size == 0:
        return []
    coords = gaia_coords[bright]
    pixel_x, pixel_y = wcs.world_to_pixel(coords)
    inside = (pixel_x >= 0) & (pixel_x <= width - 1) & (pixel_y >= 0) & (pixel_y <= height - 1)
    matched_ids = {star.gaia_source_id for star in stars if star.gaia_source_id}
    detected = (
        SkyCoord(ra=[star.ra_deg for star in stars] * u.deg, dec=[star.dec_deg for star in stars] * u.deg)
        if stars
        else None
    )
    _, detection_separation = nearest_catalog_entries(coords, detected)
    near_detection = detection_separation < float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value)
    order = np.argsort(magnitudes[bright])
    found: list[IdentifiedStar] = []
    for position in order:
        if len(found) >= CATALOG_ONLY_LIMIT:
            break
        if not inside[position] or near_detection[position]:
            continue
        row = gaia_table[int(bright[position])]
        source_id = str(row["source_id"])
        if source_id in matched_ids:
            continue
        found.append(
            IdentifiedStar(
                id=f"gaia-{source_id}",
                name=gaia_record_id(source_id),
                kind="catalog_only",
                x=float(pixel_x[position]) + 1.0,
                y=float(pixel_y[position]) + 1.0,
                ra_deg=float(coords[position].ra.deg),
                dec_deg=float(coords[position].dec.deg),
                g_mag=float(magnitudes[bright[position]]),
                gaia_source_id=source_id,
                name_source="gaia",
                library_id=gaia_record_id(source_id),
            )
        )
    return found


def identify_stack(stack_path: str, *, config: Any = None, drivers: Any = None) -> StackIdentification:
    """Name the detected stars of a stack, reporting to the current job.

    This is the owner of `processing.astrometry.identify`. It runs
    `identify_stack_stars` with the job context: the share done and a
    sentence go to the current job (see `get_current_job`), and a run given
    up on (see `bind_abandonment`) stops before the next step or Gaia tile.

    Parameters
    ----------
    stack_path : `str`
        The imaging stack. A raw frame is refused.
    config : `AppConfiguration`, optional
        The settings.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.

    Returns
    -------
    identification : `StackIdentification`
        The named stars. It is also saved next to the stack.
    """
    from astrometricslib.foundation.jobs.runner import get_current_job
    from astrometricslib.foundation.run_abandonment import raise_if_abandoned

    def report(share: float, message: str) -> None:
        """Pass one progress step to the current job, if there is one.

        Parameters
        ----------
        share : `float`
            The share done, 0 to 1.
        message : `str`
            What is being done.
        """
        job = get_current_job()
        if job is not None:
            job.mark("running", round(share * 100), message=message)

    def stopped() -> bool:
        """Raise when the run was given up on; otherwise say to go on.

        Returns
        -------
        stop : `bool`
            Always `False`: a run given up on raises `RunAbandonedError`.
        """
        raise_if_abandoned()
        return False

    return identify_stack_stars(
        stack_path, config=config, drivers=drivers, progress=report, should_stop=stopped
    )
