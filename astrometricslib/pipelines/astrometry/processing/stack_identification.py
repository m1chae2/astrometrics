"""Names every star a stack's own star finder detected, from Gaia and SIMBAD.

The viewer's Astrometry button on a stack asks for this. It works on the
stack only, never on a raw frame, and adds nothing to the target's star
catalog. The steps are:

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
   matched the same way, and a detection takes the most familiar designation
   SIMBAD lists (see `star_designations`). Offline, names are Gaia's only,
   and a note says why. A detection with no catalog star is
   ``Unnamed star``.
5. **Catalog stars with no detection.** Gaia stars inside the stack that are
   one magnitude brighter than the predicted detection limit (where nearly
   every isolated star is found), but have no detection within the match
   radius, are listed so the viewer can show what was missed.

The result is saved next to the stack (``<stack>_identification.json``) with
the stack's size and modification time, and `read_stack_identification`
returns it while the stack is unchanged.
"""

import json
import logging
import math
import os
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
from astrometricslib.models.stack_identification import IdentifiedStar, StackIdentification
from astrometricslib.pipelines.astrometry.processing.detections import measure_detections_in_file
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import (
    TileFetcher,
    load_field_gaia_stars,
    tiles_under_image,
)
from astrometricslib.pipelines.astrometry.processing.star_identifier import (
    StarIdentifier,
    make_gaia_tile_fetcher,
    propagate_gaia_positions,
    read_observation_time,
)
from astrometricslib.pipelines.shared.catalog_star_identity import CATALOG_MATCH_RADIUS_ARCSEC
from astrometricslib.pipelines.shared.star_designations import most_familiar_designation

__all__ = [
    "IDENTIFICATION_SIDECAR_SUFFIX",
    "StackIdentificationStoppedError",
    "identify_stack_stars",
    "read_stack_identification",
]

logger = logging.getLogger(__name__)

# What follows the stack's name (without .fits) in the saved result's name.
IDENTIFICATION_SIDECAR_SUFFIX = "_identification.json"

# Raise this when the layout of the saved file changes.
IDENTIFICATION_FILE_VERSION = 1

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

# The name of a detection no catalog star is close to.
UNNAMED_STAR = "Unnamed star"

# The share of the work each step stands for, for the progress report.
_PROGRESS_SOLVED = 0.25
_PROGRESS_DETECTED = 0.35
_PROGRESS_TILES_DONE = 0.8
_PROGRESS_SIMBAD_DONE = 0.9

ProgressReport = Callable[[float, str], None]
StopCheck = Callable[[], bool]


class StackIdentificationStoppedError(AstrometricsError):
    """The person stopped the identification before it finished."""


def _sidecar_path(stack_path: str) -> str:
    """Give the path of the saved result next to a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    path : `str`
        ``<stack>_identification.json``.
    """
    return os.path.splitext(stack_path)[0] + IDENTIFICATION_SIDECAR_SUFFIX


def _file_stamp(path: str) -> list[int] | None:
    """Give a file's size and modification time, to tell when it changed.

    Parameters
    ----------
    path : `str`
        The file.

    Returns
    -------
    stamp : `list` [`int`] or `None`
        ``[size, mtime_ns]``, or `None` when the file cannot be read.
    """
    try:
        status = os.stat(path)
    except OSError:
        return None
    return [int(status.st_size), int(status.st_mtime_ns)]


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
    saved = _sidecar_path(stack_path)
    stamp = _file_stamp(stack_path)
    if stamp is None or not os.path.isfile(saved):
        return None
    try:
        with open(saved, encoding="utf-8") as handle:
            record = json.load(handle)
        if record.get("version") != IDENTIFICATION_FILE_VERSION or record.get("stack_stamp") != stamp:
            return None
        return StackIdentification.model_validate(record["identification"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.debug("Could not read the saved identification %s: %s", saved, error)
        return None


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
    stamp = _file_stamp(stack_path)
    if stamp is None:
        return
    saved = _sidecar_path(stack_path)
    record = {
        "version": IDENTIFICATION_FILE_VERSION,
        "stack_stamp": stamp,
        "identification": identification.model_dump(),
    }
    temporary = f"{saved}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(temporary, saved)
    except OSError as error:
        logger.warning("Could not save the star names of %s: %s", os.path.basename(stack_path), error)


def _check(should_stop: StopCheck | None) -> None:
    """Stop the work when the person asked for it.

    Parameters
    ----------
    should_stop : `Callable` or `None`
        Returns True when the work should stop.

    Raises
    ------
    StackIdentificationStoppedError
        When `should_stop` says so.
    """
    if should_stop is not None and should_stop():
        raise StackIdentificationStoppedError("The star identification was stopped.")


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
) -> tuple[SkyCoord | None, list[str], list[str | None], str]:
    """Ask SIMBAD for the stars of the field and their familiar names.

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
    names : `list` [`str`]
        The most familiar designation of each.
    gaia_ids : `list` [`str` or `None`]
        The Gaia DR3 source ID SIMBAD lists for each, if any.
    note : `str`
        Why there are no SIMBAD names; empty when there are.
    """
    if network_is_offline():
        return None, [], [], "Offline: the names come from Gaia only, because SIMBAD cannot be asked."
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
        return None, [], [], "SIMBAD did not answer, so the names come from Gaia only."
    ids_column = next((name for name in ("ids", "IDS") if name in table.colnames), None)
    main_column = next((name for name in ("main_id", "MAIN_ID") if name in table.colnames), None)
    names: list[str] = []
    gaia_ids: list[str | None] = []
    for row in table:
        identifiers: list[str] = []
        if ids_column is not None:
            raw = row[ids_column]
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            identifiers = [part.strip() for part in text.split("|") if part.strip()]
        if not identifiers and main_column is not None:
            identifiers = [str(row[main_column])]
        names.append(most_familiar_designation(identifiers) or UNNAMED_STAR)
        gaia = next((part for part in identifiers if part.startswith("Gaia DR3 ")), None)
        gaia_ids.append(gaia.split()[-1] if gaia else None)
    return coords, names, gaia_ids, ""


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
    from astrometricslib.pipelines.shared.session_identification import header_is_plate_solved
    from astrometricslib.pipelines.stacking.stage import ensure_stack_plate_solution

    header = read_header(stack_path)
    reused = header_is_plate_solved(header)
    wcs, reason, _ = ensure_stack_plate_solution(stack_path, drivers)
    if wcs is None:
        raise InvalidArgumentError(f"The stack could not be plate-solved: {reason}.")
    if not reused:
        header = read_header(stack_path)
    return WCS(header, naxis=2), reused, header


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
    A `StackIdentificationStoppedError` passes through when `should_stop`
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
    simbad_coords, simbad_names, simbad_gaia_ids, simbad_note = _simbad_names(identifier, wcs, width, height)
    if simbad_note:
        notes.append(simbad_note)
    _check(should_stop)
    _report(progress, _PROGRESS_SIMBAD_DONE, "Matching the detections to the catalogs")

    stars = _match(detections, wcs, gaia_table, gaia_coords, simbad_coords, simbad_names, simbad_gaia_ids)
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
    )
    _save(stack_path, identification)
    _report(progress, 1.0, "Done")
    return identification


def _nearest(
    coords: SkyCoord, catalog: SkyCoord | None, radius_arcsec: float
) -> tuple[np.ndarray, np.ndarray]:
    """Find each position's nearest catalog star within a radius.

    Parameters
    ----------
    coords : `SkyCoord`
        The positions.
    catalog : `SkyCoord` or `None`
        The catalog.
    radius_arcsec : `float`
        The match radius.

    Returns
    -------
    index, found : `numpy.ndarray`
        The catalog index of each position's nearest star, and whether it is
        within the radius.
    """
    if catalog is None or len(catalog) == 0 or len(coords) == 0:
        return np.zeros(len(coords), dtype=int), np.zeros(len(coords), dtype=bool)
    index, separation, _ = coords.match_to_catalog_sky(catalog)
    return np.asarray(index, dtype=int), np.asarray(separation.arcsec) < radius_arcsec


def _match(
    detections: list[Any],
    wcs: WCS,
    gaia_table: Any,
    gaia_coords: SkyCoord | None,
    simbad_coords: SkyCoord | None,
    simbad_names: list[str],
    simbad_gaia_ids: list[str | None],
) -> list[IdentifiedStar]:
    """Give each detection its catalog star and name.

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
    ra, dec = wcs.wcs_pix2world(pixel_x, pixel_y, 0)
    coords = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    gaia_index, gaia_found = _nearest(coords, gaia_coords, radius)
    simbad_index, simbad_found = _nearest(coords, simbad_coords, radius)
    simbad_by_gaia = {gaia: name for gaia, name in zip(simbad_gaia_ids, simbad_names, strict=True) if gaia}
    stars: list[IdentifiedStar] = []
    for number in range(len(detections)):
        source_id = g_mag = None
        star_ra, star_dec = float(ra[number]), float(dec[number])
        if gaia_found[number] and gaia_table is not None and gaia_coords is not None:
            row = gaia_table[int(gaia_index[number])]
            source_id = str(row["source_id"])
            magnitude = float(row["phot_g_mean_mag"])
            g_mag = magnitude if math.isfinite(magnitude) else None
            star_ra = float(gaia_coords[int(gaia_index[number])].ra.deg)
            star_dec = float(gaia_coords[int(gaia_index[number])].dec.deg)
        name, name_source = UNNAMED_STAR, "none"
        if source_id is not None and source_id in simbad_by_gaia:
            name, name_source = simbad_by_gaia[source_id], "simbad"
        elif simbad_found[number]:
            name, name_source = simbad_names[int(simbad_index[number])], "simbad"
        elif source_id is not None:
            name, name_source = f"Gaia DR3 {source_id}", "gaia"
        kind = "matched" if name_source != "none" else "unmatched"
        stars.append(
            IdentifiedStar(
                id=f"detection-{number}",
                name=name,
                kind=kind,
                x=float(pixel_x[number]),
                y=float(pixel_y[number]),
                ra_deg=star_ra,
                dec_deg=star_dec,
                g_mag=g_mag,
                gaia_source_id=source_id,
                name_source=name_source,
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
    _, near_detection = _nearest(coords, detected, float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value))
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
                name=f"Gaia DR3 {source_id}",
                kind="catalog_only",
                x=float(pixel_x[position]),
                y=float(pixel_y[position]),
                ra_deg=float(coords[position].ra.deg),
                dec_deg=float(coords[position].dec.deg),
                g_mag=float(magnitudes[bright[position]]),
                gaia_source_id=source_id,
                name_source="gaia",
            )
        )
    return found
