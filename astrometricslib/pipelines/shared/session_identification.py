"""Figure out what stars are in an image sequence.

This tool looks at the first image in a sequence (the "reference frame"),
maps it to the sky, and identifies all the stars. It's smart enough to
re-use existing map data if the image already has it, saving a lot of time.

When the plate solver measures its own fit, the result keeps two numbers
from that solve: the fit residual (how far, on average, the fitted star
positions sit from the reference stars) and the count of matched stars.
They stay unknown (`None`) when the map came from the image file or the
solver does not report them.
"""

import configparser
import logging
import os
import re
import warnings
from dataclasses import dataclass, field
from typing import Any

from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.drivers.interfaces.plate_solve_driver import read_fit_statistics
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

logger = logging.getLogger(__name__)

# Minimum fraction of a reference frame's detected stars that must resolve
# to a real catalog identity (SIMBAD or Gaia) before a *reused* header WCS
# is trusted. Below this, the header solution is discarded and the frame is
# plate-solved fresh.
# This number is used because tests show that a good alignment matches
# at least ~28% of stars, while a bad alignment matches less than ~6%.
# Setting the limit to 0.10 (10%) easily separates the good from the bad,
# ensuring a correct solution isn't accidentally thrown away just
# because the star field is sparse.
MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS = 0.10

# Below this many identified stars the match fraction is too noisy to judge
# a WCS by -- a handful of stars can miss every catalog match by chance.
# The value 20 is small enough to include very sparse star fields, but
# large enough to give a reliable percentage.
MIN_STARS_TO_VERIFY_REUSED_WCS = 20


# The keywords that describe where an image points on the sky, for the first
# two axes. `write_wcs_to_fits_header` deletes these before it writes a new
# solution. Without that step, a card from an older solve can survive and
# contradict the new one. For example, an old `CD1_1` matrix outranks a new
# `PC1_1` matrix when astropy reads the header, and an old `A_4_0` term
# stays next to a new second-order SIP fit.
_STALE_WCS_KEYWORD_PATTERN = re.compile(
    r"^(WCSAXES|CTYPE[12]|CRVAL[12]|CRPIX[12]|CDELT[12]|CUNIT[12]|CROTA[12]"
    r"|PC[12]_[12]|CD[12]_[12]|PV[12]_\d+|PS[12]_\d+"
    r"|LONPOLE|LATPOLE|RADESYS|RADECSYS|EQUINOX"
    r"|(A|B|AP|BP)_ORDER|(A|B|AP|BP)_\d+_\d+|(A|B)_DMAX)$"
)


def write_wcs_to_fits_header(path: str, wcs: WCS) -> None:
    """Save a solved sky map (WCS) into a FITS file's header.

    The next program that opens the file (this library, Siril, a FITS
    viewer) then gets the solved pointing without solving again.

    The function first deletes the old sky-map keywords, so an earlier
    solve cannot leave cards that contradict the new one. It then writes
    the new keywords with ``to_header(relax=True)``. Without ``relax=True``,
    astropy leaves out the SIP distortion terms (``A_*``, ``B_*``,
    ``AP_*``, ``BP_*``) and removes ``-SIP`` from ``CTYPE``. A reader of the
    file would then lose the lens distortion near the image edges.

    The function logs a warning and returns if the file cannot be updated.

    Parameters
    ----------
    path : `str`
        The FITS file to update. A missing or empty path does nothing.
    wcs : `astropy.wcs.WCS`
        The solved sky map to store.
    """
    if not path or not os.path.exists(path):
        return
    try:
        with fits.open(path, mode="update", memmap=False) as hdul:
            header = hdul[0].header
            for keyword in [key for key in header if _STALE_WCS_KEYWORD_PATTERN.match(key)]:
                del header[keyword]
            for card in wcs.to_header(relax=True).cards:
                if not card.keyword:
                    continue
                header[card.keyword] = (card.value, card.comment)
            hdul.flush()
        logger.info("Updated FITS file %s header with solved WCS keywords.", path)
    except FITS_READ_ERRORS as wcs_error:
        logger.warning("Failed to update FITS file header with WCS: %s", wcs_error)


def resolve_frame_wcs(
    image: AstrometricsImage,
    star_identifier: StarIdentifier,
    allow_solve: bool = True,
    center_ra: float | None = None,
    center_dec: float | None = None,
    sources: list[dict] | None = None,
    write_back: bool = True,
    ignore_existing_wcs: bool = False,
    solve_timeout: int = 300,
) -> tuple[WCS | None, bool, bool]:
    """Figure out the sky map (WCS) for an image.

    It tries to be lazy and use the map already saved in the image file.
    If there isn't one, it calculates a new one from scratch and saves it.

    Parameters
    ----------
    image : `AstrometricsImage`
        The image to map.
    star_identifier : `StarIdentifier`
        The tool that does the heavy lifting to identify stars.
    allow_solve : `bool`, optional
        If False, just check the file and give up if the map isn't
        already there. It won't try to calculate it from scratch.
    center_ra, center_dec : `float`, optional
        Hints about where the telescope was pointing.
    sources : `list` [`dict`], optional
        A list of stars already found in the image.
    write_back : `bool`, optional
        Whether the newly calculated map should be saved into the image file.
    ignore_existing_wcs : `bool`, optional
        If True, ignore any saved map and force it to calculate a new one.
    solve_timeout : `int`, optional
        The maximum time in seconds to let the solver run.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The finished map data, or None if it failed.
    reused_existing_header_wcs : `bool`
        True if the saved map was used.
    solve_attempted : `bool`
        True if the complex math was actually run to calculate a new map.
    """
    wcs, reused_existing_header_wcs, solve_attempted, _fit_statistics = _resolve_frame_wcs_with_fit(
        image,
        star_identifier,
        allow_solve=allow_solve,
        center_ra=center_ra,
        center_dec=center_dec,
        sources=sources,
        write_back=write_back,
        ignore_existing_wcs=ignore_existing_wcs,
        solve_timeout=solve_timeout,
    )
    return wcs, reused_existing_header_wcs, solve_attempted


def _resolve_frame_wcs_with_fit(
    image: AstrometricsImage,
    star_identifier: StarIdentifier,
    allow_solve: bool = True,
    center_ra: float | None = None,
    center_dec: float | None = None,
    sources: list[dict] | None = None,
    write_back: bool = True,
    ignore_existing_wcs: bool = False,
    solve_timeout: int = 300,
) -> tuple[WCS | None, bool, bool, tuple[float | None, int | None]]:
    """Find an image's sky map (WCS) and keep the solver's fit numbers.

    This does the work of `resolve_frame_wcs`. The only difference is one
    extra return value, the fit statistics of a fresh solve.

    Parameters
    ----------
    image : `AstrometricsImage`
        The image to map.
    star_identifier : `StarIdentifier`
        The tool that does the heavy lifting to identify stars.
    allow_solve : `bool`, optional
        If False, just check the file and give up if the map isn't
        already there.
    center_ra, center_dec : `float`, optional
        Hints about where the telescope was pointing.
    sources : `list` [`dict`], optional
        A list of stars already found in the image.
    write_back : `bool`, optional
        Whether the newly calculated map should be saved into the image file.
    ignore_existing_wcs : `bool`, optional
        If True, ignore any saved map and force it to calculate a new one.
    solve_timeout : `int`, optional
        The maximum time in seconds to let the solver run.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The finished map data, or None if it failed.
    reused_existing_header_wcs : `bool`
        True if the saved map was used.
    solve_attempted : `bool`
        True if the complex math was actually run to calculate a new map.
    fit_statistics : `tuple` [`float` or `None`, `int` or `None`]
        The fit residual in arcseconds and the matched-star count from the
        solve. Both are `None` when the saved map was reused, no solve ran,
        or the solver did not report them.
    """
    no_fit_statistics: tuple[float | None, int | None] = (None, None)
    if not ignore_existing_wcs and image.wcs is not None and image.wcs.is_celestial:
        # NOTE: is_celestial is a *structural* check (does this WCS have
        # RA/Dec axes), not an accuracy one -- a header solution that is
        # off by tens of arcsec passes it just as readily as a good one.
        # `identify_session_stars` verifies the result against catalog
        # matches and re-solves when this turns out to be untrustworthy.
        return image.wcs, True, False, no_fit_statistics

    if not allow_solve:
        return None, False, False, no_fit_statistics

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
        solve_timeout=solve_timeout,
    )
    if header is None:
        logger.warning("Plate solve failed for %s; no WCS available.", image.path)
        return None, False, True, no_fit_statistics

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        wcs = WCS(header, naxis=2)

    if write_back:
        write_wcs_to_fits_header(image.path, wcs)

    return wcs, False, True, read_fit_statistics(header)


def _catalog_matched_count(stellar_objects: list[StellarObject]) -> int:
    """Count how many stars were successfully looked up in the database.

    Returns
    -------
    matched : `int`
        The number of stars positively identified.
    """
    return sum(1 for star in stellar_objects if star.is_catalog_identified)


def _reused_wcs_looks_untrustworthy(stellar_objects: list[StellarObject]) -> bool:
    """Check if the saved map in the image file is actually garbage.

    Sometimes an image has a saved map, but it's completely wrong.
    This can be determined because when trying to look up the stars
    using that map, none of them match the real database.

    Returns
    -------
    untrustworthy : `bool`
        True if the saved map is so bad it needs to be thrown out and
        recalculated.
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
    # How well the plate solve that produced `wcs` fit its reference stars.
    # `fit_residual_rms_arcsec` is the root mean square (RMS) distance, in
    # arcseconds, between the matched stars' fitted positions and the
    # reference positions. `matched_star_count` is how many stars matched.
    # Both are None when `wcs` was reused from the image header (nobody
    # measured it), when no solve ran, or when the solver did not report them.
    fit_residual_rms_arcsec: float | None = None
    matched_star_count: int | None = None


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
        The image being analyzed.
    star_identifier : `StarIdentifier`
        The tool that does the math and database lookups.
    center_ra, center_dec : `float`, optional
        Hints about where the telescope was pointing.
    max_detections : `int`, optional
        If 5,000 stars are found, looking them all up might take forever.
        This puts a cap on how many of the brightest stars are identified.
    write_back : `bool`, optional
        Whether to save the calculated map data back into the image file.

    Returns
    -------
    result : `SessionIdentificationResult`
        A bundle containing the map data, the list of identified stars,
        and some stats about how well the process worked. It includes the
        plate solver's fit residual and matched-star count when the map
        came from a fresh solve and the solver reported them.
    """
    data, unique_sources, sources_detected = _detect_and_limit_session_sources(
        reference_image, star_identifier, max_detections
    )

    stellar_objects = star_identifier._build_stellar_objects_from_sources(unique_sources)

    wcs, reused_existing_header_wcs, solve_attempted, fit_statistics = _resolve_frame_wcs_with_fit(
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

    (
        wcs,
        stellar_objects,
        reused_existing_header_wcs,
        solve_attempted,
        header_wcs_replaced,
        fit_statistics,
    ) = _reverify_wcs_solution(
        reference_image,
        star_identifier,
        unique_sources,
        center_ra,
        center_dec,
        wcs,
        stellar_objects,
        reused_existing_header_wcs,
        solve_attempted,
        width,
        height,
        write_back,
        fit_statistics,
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
        fit_residual_rms_arcsec=fit_statistics[0],
        matched_star_count=fit_statistics[1],
    )


def _detect_and_limit_session_sources(
    reference_image: AstrometricsImage, star_identifier: StarIdentifier, max_detections: int | None
) -> tuple[Any, list[dict], int]:
    """Detect a reference frame's stars and cap how many will be identified.

    Returns
    -------
    data : `numpy.ndarray` or `None`
        The frame's pixel data, color-collapsed to 2-D if needed.
    unique_sources : `list` [`dict`]
        The detected sources, capped at the identification limit.
    sources_detected : `int`
        How many unique sources were detected before capping.
    """
    data = reference_image.data
    is_color_frame = data is not None and data.ndim == 3
    if is_color_frame:
        from astrometricslib.drivers.fits_access import collapse_to_2d

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
        # A stand-in identifier (as in tests) may have no configuration.
        configuration = getattr(star_identifier, "config", None)
        if configuration is not None:
            try:
                identification_limit = configuration.get_maximum_identified_stars()
            except configparser.Error:
                identification_limit = None
    if isinstance(identification_limit, int) and identification_limit > 0:
        unique_sources = unique_sources[:identification_limit]

    return data, unique_sources, sources_detected


def _reverify_wcs_solution(
    reference_image: AstrometricsImage,
    star_identifier: StarIdentifier,
    unique_sources: list[dict],
    center_ra: float | None,
    center_dec: float | None,
    wcs: WCS | None,
    stellar_objects: list[StellarObject],
    reused_existing_header_wcs: bool,
    solve_attempted: bool,
    width: int,
    height: int,
    write_back: bool,
    fit_statistics: tuple[float | None, int | None],
) -> tuple[WCS | None, list[StellarObject], bool, bool, bool, tuple[float | None, int | None]]:
    """Re-solve a reused header WCS that turns out to be untrustworthy.

    Parameters
    ----------
    reference_image : `AstrometricsImage`
        The frame being identified.
    star_identifier : `StarIdentifier`
        The tool that solves and identifies.
    unique_sources : `list` [`dict`]
        The detected sources.
    center_ra, center_dec : `float` or `None`
        Hints about where the telescope was pointing.
    wcs : `astropy.wcs.WCS` or `None`
        The WCS from the first pass.
    stellar_objects : `list` [`StellarObject`]
        The stars identified against `wcs`.
    reused_existing_header_wcs : `bool`
        Whether `wcs` came from the image header.
    solve_attempted : `bool`
        Whether a plate solve has run so far.
    width, height : `int`
        The image size in pixels.
    write_back : `bool`
        Whether a better WCS is saved into the image file.
    fit_statistics : `tuple` [`float` or `None`, `int` or `None`]
        The fit residual and matched-star count of the first pass's solve.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The WCS to use going forward -- the fresh solve if it proved
        better, otherwise unchanged.
    stellar_objects : `list` [`StellarObject`]
        The stars identified against `wcs`.
    reused_existing_header_wcs : `bool`
        Whether the returned WCS is still the reused header one.
    solve_attempted : `bool`
        Whether a fresh plate solve was attempted, folded in with the
        caller's own `solve_attempted`.
    header_wcs_replaced : `bool`
        True if the reused header WCS was discarded in favor of a
        fresh solve.
    fit_statistics : `tuple` [`float` or `None`, `int` or `None`]
        The fit numbers that belong to the returned WCS: the fresh solve's
        when it replaced the header WCS, otherwise `fit_statistics`
        unchanged.
    """
    if not (reused_existing_header_wcs and _reused_wcs_looks_untrustworthy(stellar_objects)):
        return wcs, stellar_objects, reused_existing_header_wcs, solve_attempted, False, fit_statistics

    matched_before = _catalog_matched_count(stellar_objects)
    logger.warning(
        "Reused header WCS for %s identified only %s/%s stars against a catalog; discarding it and "
        "plate-solving this frame fresh.",
        reference_image.path,
        matched_before,
        len(stellar_objects),
    )
    # write_back=False: the header is only corrected below, once the
    # fresh solve has actually proven better. Overwriting first would
    # destroy the existing solution even when the re-solve turns out
    # worse (or fails outright).
    fresh_wcs, _, fresh_solve_attempted, fresh_fit_statistics = _resolve_frame_wcs_with_fit(
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

    if fresh_wcs is None:
        return wcs, stellar_objects, reused_existing_header_wcs, solve_attempted, False, fit_statistics

    # Identify onto *fresh* objects: the first pass already mutated
    # the originals (ids, names, coordinates), so reusing them would
    # compare a re-identified list against itself.
    fresh_objects = star_identifier._build_stellar_objects_from_sources(unique_sources)
    star_identifier.identify_stars_with_wcs(fresh_objects, fresh_wcs, width, height)
    matched_after = _catalog_matched_count(fresh_objects)

    if matched_after <= matched_before:
        logger.info(
            "Fresh plate solve for %s did not improve catalog matches (%s -> %s); keeping the header WCS.",
            reference_image.path,
            matched_before,
            matched_after,
        )
        return wcs, stellar_objects, reused_existing_header_wcs, solve_attempted, False, fit_statistics

    logger.info(
        "Fresh plate solve for %s improved catalog matches %s -> %s; using it instead of the header WCS.",
        reference_image.path,
        matched_before,
        matched_after,
    )
    if write_back:
        write_wcs_to_fits_header(reference_image.path, fresh_wcs)
    return fresh_wcs, fresh_objects, False, solve_attempted, True, fresh_fit_statistics
