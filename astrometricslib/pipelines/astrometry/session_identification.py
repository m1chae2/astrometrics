"""Figure out what stars are in an image sequence.

This tool looks at the first image in a sequence (the "reference frame"),
maps it to the sky, and identifies all the stars. It's smart enough to
re-use existing map data if the image already has it, saving a lot of time.
"""

import logging
import os
import warnings
from dataclasses import dataclass, field

from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier

logger = logging.getLogger(__name__)

# Minimum fraction of a reference frame's detected stars that must resolve
# to a real catalog identity (SIMBAD or Gaia) before a *reused* header WCS
# is trusted. Below this, the header solution is discarded and the frame is
# plate-solved fresh.
# We use this number because tests show that a good alignment matches
# at least ~28% of stars, while a bad alignment matches less than ~6%.
# Setting the limit to 0.10 (10%) easily separates the good from the bad,
# ensuring we don't accidentally throw away a correct solution just
# because the star field is sparse.
MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS = 0.10

# Below this many identified stars the match fraction is too noisy to judge
# a WCS by -- a handful of stars can miss every catalog match by chance.
# The value 20 is small enough to include very sparse star fields, but
# large enough to give us a reliable percentage.
MIN_STARS_TO_VERIFY_REUSED_WCS = 20


def _write_wcs_to_header(path: str, wcs: WCS) -> None:
    """Save the calculated map data back into the image file.

    This means the next time we load this image, we won't have to
    waste time re-calculating everything.
    """
    if not path or not os.path.exists(path):
        return
    try:
        with fits.open(path, mode="update") as hdul:
            wcs_header = wcs.to_header()
            for card in wcs_header.cards:
                if not card.keyword:
                    continue
                hdul[0].header[card.keyword] = (card.value, card.comment)
            hdul.flush()
        logger.info(f"Updated FITS file {path} header with solved WCS keywords.")
    except Exception as wcs_error:
        logger.warning(f"Failed to update FITS file header with WCS: {wcs_error}")


def resolve_frame_wcs(
    image: AstrometricsImage,
    star_identifier: StarIdentifier,
    allow_solve: bool = True,
    center_ra: float | None = None,
    center_dec: float | None = None,
    sources: list[dict] | None = None,
    write_back: bool = True,
    ignore_existing_wcs: bool = False,
) -> tuple[WCS | None, bool, bool]:
    """Figure out the sky map (WCS) for an image.

    It tries to be lazy and use the map already saved in the image file.
    If there isn't one, it calculates a new one from scratch and saves it.

    Parameters
    ----------
    image : `AstrometricsImage`
        The image we want to map.
    star_identifier : `StarIdentifier`
        The tool that does the heavy lifting to identify stars.
    allow_solve : `bool`, optional
        If False, we just check the file and give up if the map isn't
        already there. We don't try to calculate it from scratch.
    center_ra, center_dec : `float`, optional
        Hints about where the telescope was pointing.
    sources : `list` [`dict`], optional
        A list of stars we already found in the image.
    write_back : `bool`, optional
        Whether we should save our newly calculated map into the image file.
    ignore_existing_wcs : `bool`, optional
        If True, we ignore any saved map and force it to calculate a new one.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The finished map data, or None if it failed.
    reused_existing_header_wcs : `bool`
        True if we were lazy and just used the saved map.
    solve_attempted : `bool`
        True if we actually ran the complex math to calculate a new map.
    """
    if not ignore_existing_wcs and image.wcs is not None and image.wcs.is_celestial:
        # NOTE: is_celestial is a *structural* check (does this WCS have
        # RA/Dec axes), not an accuracy one -- a header solution that is
        # off by tens of arcsec passes it just as readily as a good one.
        # `identify_session_stars` verifies the result against catalog
        # matches and re-solves when this turns out to be untrustworthy.
        return image.wcs, True, False

    if not allow_solve:
        return None, False, False

    data = image.data
    h, w = (data.shape[0], data.shape[1]) if data is not None else (1000, 1000)

    scale_lower, scale_upper = star_identifier._calculate_scale_hints(image)
    header = star_identifier.solver.solve(
        image_path=image.path,
        sources=sources,
        image_width=w,
        image_height=h,
        center_ra=center_ra,
        center_dec=center_dec,
        radius=2.0,
        scale_units="arcsecperpix",
        scale_lower=scale_lower,
        scale_upper=scale_upper,
        solve_timeout=300,
    )
    if header is None:
        logger.warning(f"Plate solve failed for {image.path}; no WCS available.")
        return None, False, True

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        wcs = WCS(header, naxis=2)

    if write_back:
        _write_wcs_to_header(image.path, wcs)

    return wcs, False, True


def _catalog_matched_count(stellar_objects: list[StellarObject]) -> int:
    """Count how many stars we successfully looked up in the database.

    Returns
    -------
    matched : `int`
        The number of stars we positively identified.
    """
    return sum(1 for star in stellar_objects if star.is_catalog_identified)


def _reused_wcs_looks_untrustworthy(stellar_objects: list[StellarObject]) -> bool:
    """Check if the saved map in the image file is actually garbage.

    Sometimes an image has a saved map, but it's completely wrong.
    We can tell because when we try to look up the stars using that map,
    none of them match the real database.

    Returns
    -------
    untrustworthy : `bool`
        True if the saved map is so bad we need to throw it out and
        recalculate it.
    """
    if len(stellar_objects) < MIN_STARS_TO_VERIFY_REUSED_WCS:
        return False
    matched_fraction = _catalog_matched_count(stellar_objects) / len(stellar_objects)
    return matched_fraction < MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS


@dataclass
class SessionIdentificationResult:
    """Result of identifying a session's stars from its reference frame."""

    wcs: WCS | None
    stellar_objects: list[StellarObject] = field(default_factory=list)
    reused_existing_header_wcs: bool = False
    solve_attempted: bool = False
    plate_solve_succeeded: bool = False
    simbad_matched_count: int = 0
    sources_detected: int = 0
    # True when a reused header WCS matched too few catalog stars to be
    # trusted and was replaced by a fresh plate solve; see
    # MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS.
    header_wcs_replaced_after_verification: bool = False


def identify_session_stars(
    reference_image: AstrometricsImage,
    star_identifier: StarIdentifier,
    center_ra: float | None = None,
    center_dec: float | None = None,
    max_detections: int | None = None,
    write_back: bool = True,
) -> SessionIdentificationResult:
    """Find and name all the stars in the first image of a sequence.

    This looks at the image, maps it out, and then checks the database
    to figure out the name of every single star it can see.

    Parameters
    ----------
    reference_image : `AstrometricsImage`
        The image we are analyzing.
    star_identifier : `StarIdentifier`
        The tool that does the math and database lookups.
    center_ra, center_dec : `float`, optional
        Hints about where the telescope was pointing.
    max_detections : `int`, optional
        If we find 5,000 stars, looking them all up might take forever.
        This puts a cap on how many of the brightest stars we identify.
    write_back : `bool`, optional
        Whether to save our calculated map data back into the image file.

    Returns
    -------
    result : `SessionIdentificationResult`
        A bundle containing the map data, the list of identified stars,
        and some stats about how well the process worked.
    """
    data = reference_image.data
    is_color_frame = data is not None and data.ndim == 3
    if is_color_frame:
        from astrometricslib.image_processing.fits_access import collapse_to_2d

        data = collapse_to_2d(data)

    # See StarIdentifier.detect_stars: a colour session reference frame
    # needs the same block-averaging treatment `process_image` gives the
    # astrometry pass's stacked image, for the same reason -- a raw,
    # never-debayered single light (the common case here) has no
    # correlated-interpolation-noise problem and this branch is a no-op
    # for it, but a reference frame that does arrive as a colour cube
    # would otherwise hit the same false-detection failure mode.
    _sources, unique_sources = star_identifier.detect_stars(data, is_color_frame=is_color_frame)
    sources_detected = len(unique_sources)

    # An explicit argument wins over configuration; an explicit 0 means
    # no limit, so a caller can override a configured ceiling without
    # reading configuration first. Matches process_image's precedence.
    identification_limit = max_detections
    if identification_limit is None:
        try:
            identification_limit = star_identifier.config.get_maximum_identified_stars()
        except Exception:
            identification_limit = None
    if isinstance(identification_limit, int) and identification_limit > 0:
        unique_sources = unique_sources[:identification_limit]

    stellar_objects = star_identifier._build_stellar_objects_from_sources(unique_sources)

    wcs, reused_existing_header_wcs, solve_attempted = resolve_frame_wcs(
        reference_image,
        star_identifier,
        allow_solve=True,
        center_ra=center_ra,
        center_dec=center_dec,
        sources=unique_sources,
        write_back=write_back,
    )

    height, width = (data.shape[0], data.shape[1]) if data is not None else (0, 0)

    if wcs is not None and stellar_objects:
        star_identifier.identify_stars_with_wcs(stellar_objects, wcs, width, height)

    header_wcs_replaced = False
    if reused_existing_header_wcs and _reused_wcs_looks_untrustworthy(stellar_objects):
        matched_before = _catalog_matched_count(stellar_objects)
        logger.warning(
            f"Reused header WCS for {reference_image.path} identified only "
            f"{matched_before}/{len(stellar_objects)} stars against a catalog; "
            "discarding it and plate-solving this frame fresh."
        )
        # write_back=False: the header is only corrected below, once the
        # fresh solve has actually proven better. Overwriting first would
        # destroy the existing solution even when the re-solve turns out
        # worse (or fails outright).
        fresh_wcs, _, fresh_solve_attempted = resolve_frame_wcs(
            reference_image,
            star_identifier,
            allow_solve=True,
            center_ra=center_ra,
            center_dec=center_dec,
            sources=unique_sources,
            write_back=False,
            ignore_existing_wcs=True,
        )
        solve_attempted = solve_attempted or fresh_solve_attempted

        if fresh_wcs is not None:
            # Identify onto *fresh* objects: the first pass already mutated
            # the originals (ids, names, coordinates), so reusing them would
            # compare a re-identified list against itself.
            fresh_objects = star_identifier._build_stellar_objects_from_sources(unique_sources)
            star_identifier.identify_stars_with_wcs(fresh_objects, fresh_wcs, width, height)
            matched_after = _catalog_matched_count(fresh_objects)

            if matched_after > matched_before:
                logger.info(
                    f"Fresh plate solve for {reference_image.path} improved catalog matches "
                    f"{matched_before} -> {matched_after}; using it instead of the header WCS."
                )
                wcs, stellar_objects = fresh_wcs, fresh_objects
                reused_existing_header_wcs = False
                header_wcs_replaced = True
                if write_back:
                    _write_wcs_to_header(reference_image.path, fresh_wcs)
            else:
                logger.info(
                    f"Fresh plate solve for {reference_image.path} did not improve catalog "
                    f"matches ({matched_before} -> {matched_after}); keeping the header WCS."
                )

    simbad_matched_count = sum(1 for star in stellar_objects if star.spectral_type)

    return SessionIdentificationResult(
        wcs=wcs,
        stellar_objects=stellar_objects,
        reused_existing_header_wcs=reused_existing_header_wcs,
        solve_attempted=solve_attempted,
        plate_solve_succeeded=wcs is not None,
        simbad_matched_count=simbad_matched_count,
        sources_detected=sources_detected,
        header_wcs_replaced_after_verification=header_wcs_replaced,
    )
