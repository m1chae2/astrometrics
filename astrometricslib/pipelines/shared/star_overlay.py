"""Purpose: Place a target's catalog stars on its image, for drawing labels.

Description: `StellarCatalog.query(target_id=..., detail="overlay")` calls
`build_overlay`. The app draws the result as labels over the target's
reference image: its stacked image, or its processed image when there is no
stack.

A star's pixel position comes from the image's world coordinate system (WCS).
The WCS is the block of FITS header keywords that maps pixels to sky
positions, so a star's right ascension and declination give its pixel
column and row. Stars that land outside the image are left out.

Two passes find the stars:

1. The target's star summaries, most useful first (stars with spectra, then
   named stars, then stars with photometry, then the brightest). Each is
   projected through the WCS. Only the first ``max(100, 3 * limit)`` are
   tried, which keeps the work small on a target with tens of thousands of
   stars.
2. When the first pass places nothing (for example, the image has no WCS),
   the target's full star records are read. A star then uses the pixel
   center saved when it was detected, or the WCS when there is one.

Star clusters and ``Star_<n>`` field detections are never labeled.
"""

import logging
from typing import Any

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib.models.catalog_queries import OverlayStar
from astrometricslib.models.stellar_source import StellarObject, has_catalog_magnitude
from astrometricslib.pipelines.shared.star_catalog_queries import (
    POSITION_ONLY_STAR_ID_PREFIX,
    StarQuery,
    run_star_query,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["build_overlay", "target_id_spellings"]

SUMMARY_PASS_MINIMUM = 100
"""Fewest summaries the first pass tries, whatever the limit."""

CLUSTER_SPECTRAL_TYPES = ("GlC", "Cluster")
"""Spectral types that mark a star cluster, which is not labeled."""


def target_id_spellings(target_id: str) -> list[str]:
    """List the spellings a target id may be stored under.

    Some targets were saved as ``"M 13"`` and others as ``"M_13"``, so both
    are tried.

    Parameters
    ----------
    target_id : `str`
        The id as given.

    Returns
    -------
    spellings : `list` [`str`]
        The id itself first, then the form with spaces and underscores
        swapped, without repeats.
    """
    spellings = [target_id]
    for other in (target_id.replace("_", " "), target_id.replace(" ", "_")):
        if other not in spellings:
            spellings.append(other)
    return spellings


def _reference_image_path(target: Any) -> str | None:
    """Find the image whose pixel grid the overlay positions use.

    Parameters
    ----------
    target : `Target` or `None`
        The target record.

    Returns
    -------
    path : `str` or `None`
        The stacked image path, else the processed image path, or `None`
        when the target has neither.
    """
    stacking = getattr(target, "stacking", None)
    path = getattr(stacking, "stacked_image", None) or getattr(stacking, "processed_image", None)
    return str(path) if path else None


def _read_reference_geometry(image_path: str | None) -> tuple[int | None, int | None, Any]:
    """Read an image's size and its WCS, if it is a FITS file.

    Parameters
    ----------
    image_path : `str` or `None`
        The reference image.

    Returns
    -------
    width : `int` or `None`
        ``NAXIS1``, or `None` when unknown.
    height : `int` or `None`
        ``NAXIS2``, or `None` when unknown.
    wcs : `astropy.wcs.WCS` or `None`
        The image's sky mapping, or `None` when it has none.
    """
    if not image_path or not str(image_path).endswith(".fits"):
        return None, None, None
    from astropy.wcs import WCS

    width = height = wcs = None
    try:
        header = read_header(image_path)
    except FITS_READ_ERRORS as error:
        logger.debug("Could not read the WCS of reference image %s: %s", image_path, error)
        return None, None, None
    if header.get("NAXIS1") is not None:
        width = int(header["NAXIS1"])
    if header.get("NAXIS2") is not None:
        height = int(header["NAXIS2"])
    try:
        candidate = WCS(header)
        wcs = candidate if candidate.is_celestial else None
    except DATA_ERRORS:
        wcs = None
    return width, height, wcs


def _project(wcs: Any, ra_deg: Any, dec_deg: Any) -> tuple[float, float] | None:
    """Turn a sky position into a pixel position through a WCS.

    Returns
    -------
    pixel : `tuple` [`float`, `float`] or `None`
        Column and row counted from 0, or `None` when there is no WCS or the
        position cannot be projected.
    """
    if wcs is None or ra_deg is None or dec_deg is None:
        return None
    try:
        column, row = wcs.all_world2pix(float(ra_deg), float(dec_deg), 0)
        return float(column), float(row)
    except DATA_ERRORS as error:
        logger.debug("WCS projection failed for RA %s, Dec %s: %s", ra_deg, dec_deg, error)
        return None


def _inside(pixel: tuple[float, float], width: int | None, height: int | None) -> bool:
    """Tell whether a pixel lies on the image.

    Returns
    -------
    bool
        `True` when the image size is unknown, or the pixel is inside it.
    """
    if width is None or height is None:
        return True
    return 0 <= pixel[0] <= width and 0 <= pixel[1] <= height


def _is_labelable(star_id: str, spectral_type: str, name: str) -> bool:
    """Tell whether a star should get a label at all.

    Returns
    -------
    bool
        `False` for a star cluster and for a ``Star_<n>`` field detection.
    """
    if spectral_type in CLUSTER_SPECTRAL_TYPES or "Cluster" in name:
        return False
    return not star_id.startswith("Star_")


def _summary_pass(
    rows: list[dict[str, Any]],
    wcs: Any,
    width: int | None,
    height: int | None,
    limit: int | None,
) -> list[OverlayStar]:
    """Place stars from their summary rows through the WCS.

    Returns
    -------
    overlay : `list` [`OverlayStar`]
        The placed stars, in row order, at most ``limit``. The radius is
        filled in later.
    """
    if wcs is None:
        return []
    tried = rows if limit is None else rows[: max(SUMMARY_PASS_MINIMUM, limit * 3)]
    placed: list[OverlayStar] = []
    for row in tried:
        star_id = str(row["id"] or "")
        spectral_type = str(row["spectralType"] or "")
        if not _is_labelable(star_id, spectral_type, str(row["name"] or "")):
            continue
        pixel = _project(wcs, row["ra"], row["dec"])
        if pixel is None or not _inside(pixel, width, height):
            continue
        placed.append(
            OverlayStar(
                id=star_id,
                name=str(row["name"] or star_id),
                x=round(pixel[0], 1),
                y=round(pixel[1], 1),
                spectral_type=spectral_type,
                is_catalog_identified=True,
                reference_width=width,
                reference_height=height,
            )
        )
        if limit is not None and len(placed) >= limit:
            break
    return placed


def _saved_center(star: StellarObject) -> tuple[float, float] | None:
    """Read the pixel center saved when a star was detected.

    Returns
    -------
    pixel : `tuple` [`float`, `float`] or `None`
        The saved center, or `None` when there is none or it is not a
        number.
    """
    star_data = star.star_data if isinstance(star.star_data, dict) else {}
    column = star_data.get("xcentroid", star_data.get("x_centroid"))
    row = star_data.get("ycentroid", star_data.get("y_centroid"))
    if column is None or row is None:
        return None
    try:
        return float(column), float(row)
    except ValueError, TypeError:
        return None


def _record_order_key(star: StellarObject) -> tuple:
    """Sort key for the second pass: the most trustworthy stars first.

    Returns
    -------
    key : `tuple`
        Catalog-identified stars first, then stars with a real name (not a
        ``FIELD_J`` position-only id), then by catalog magnitude, brightest
        first, then by measured flux, highest first, then by id.
    """
    known_magnitude = has_catalog_magnitude(star.magnitude)
    flux = star.flux if isinstance(star.flux, int | float) and not isinstance(star.flux, bool) else 0.0
    return (
        not star.is_catalog_identified,
        star.id.startswith(POSITION_ONLY_STAR_ID_PREFIX),
        not known_magnitude,
        float(star.magnitude) if known_magnitude else 0.0,
        -float(flux),
        star.id,
    )


def _record_pass(
    records: list[StellarObject],
    target_spellings: list[str],
    wcs: Any,
    width: int | None,
    height: int | None,
    limit: int | None,
) -> list[OverlayStar]:
    """Place stars from their full records, by saved center or WCS.

    Returns
    -------
    overlay : `list` [`OverlayStar`]
        The placed stars, most trustworthy first, at most ``limit``.
    """
    wanted_targets = {spelling.replace("_", " ").strip().lower() for spelling in target_spellings}
    candidates: list[tuple[StellarObject, tuple[float, float]]] = []
    for star in records:
        star_targets = {str(tid).replace("_", " ").strip().lower() for tid in star.target_ids or []}
        if not wanted_targets & star_targets:
            continue
        if star.stellar_spectral_type == "Cluster" or star.id.startswith("Star_"):
            continue
        pixel = _saved_center(star) or _project(wcs, star.right_ascension, star.declination)
        if pixel is None or not _inside(pixel, width, height):
            continue
        candidates.append((star, pixel))
    candidates.sort(key=lambda candidate: _record_order_key(candidate[0]))
    kept = candidates if limit is None else candidates[:limit]
    return [
        OverlayStar(
            id=star.id,
            name=str(star.name or star.id),
            x=round(pixel[0], 1),
            y=round(pixel[1], 1),
            spectral_type=str(star.stellar_spectral_type or star.spectral_type or ""),
            is_catalog_identified=bool(star.is_catalog_identified),
            reference_width=width,
            reference_height=height,
            radius_px=star.radius_px,
        )
        for star, pixel in kept
    ]


def build_overlay(storage: Any, target_id: str, limit: int | None) -> list[OverlayStar]:
    """Place a target's catalog stars on its reference image.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    target_id : `str`
        The target. ``"M 13"`` and ``"M_13"`` name the same target.
    limit : `int` or `None`
        Most stars to place. `None` places every star found.

    Returns
    -------
    overlay : `list` [`OverlayStar`]
        The placed stars, most useful first. Empty when the target has no
        stars that can be placed.
    """
    spellings = target_id_spellings(target_id)
    target = None
    for spelling in spellings:
        found = storage.get_by_ids("target_catalog", [spelling])
        if found:
            target = found[0]
            break
    width, height, wcs = _read_reference_geometry(_reference_image_path(target))

    for spelling in spellings:
        rows = run_star_query(
            storage, StarQuery(target_id=spelling), "summary", None, 0, order="useful"
        ).stars
        if rows:
            placed = _summary_pass(rows, wcs, width, height, limit)
            if placed:
                records = storage.get_by_ids("stellar_catalog", [star.id for star in placed])
                radii = {record.id: record.radius_px for record in records}
                for star in placed:
                    star.radius_px = radii.get(star.id)
                return placed
            break

    # Only the stars of the target itself are read, under the first spelling
    # of its id that has any. The other spellings name the same target, and
    # reading its tens of thousands of stars again only repeats the work.
    for spelling in spellings:
        records = run_star_query(storage, StarQuery(target_id=spelling), "objects", None, 0).objects or []
        if records:
            return _record_pass(records, spellings, wcs, width, height, limit)
    return []
