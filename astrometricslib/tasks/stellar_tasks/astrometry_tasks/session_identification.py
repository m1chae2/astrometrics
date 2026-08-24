"""Resolve a WCS and identify stars once per observing session.

`identify_session_stars` is the single entry point both the
photometry and spectroscopy pipelines call to turn a session's
reference frame into a WCS plus a list of SIMBAD-identified stars,
reusing any WCS already present in the frame's FITS header instead of
always plate-solving from scratch.
"""

import logging
import os
import warnings
from dataclasses import dataclass, field

from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier
from astrometricslib.utilities.image import AstrometricsImage

logger = logging.getLogger(__name__)

# Minimum fraction of a reference frame's detected stars that must resolve
# to a real catalog identity (SIMBAD or Gaia) before a *reused* header WCS
# is trusted. Below this, the header solution is discarded and the frame is
# plate-solved fresh.
#
# Derivation: measured across the 12 targets processed in the 2026-08-23
# full-catalog batch run, comparing each session's header WCS against its
# target's independently plate-solved stacked-image WCS. Sessions whose
# header WCS agreed to within the 10 arcsec SIMBAD/Gaia match tolerance
# used by `StarIdentifier.identify_stars_with_wcs` matched 28.5-42% of
# detected stars; sessions disagreeing by 33-73 arcsec collapsed to
# 0-6.2%. The two populations separate cleanly with no observed values
# between 6.2% and 17%, so 0.10 sits inside that gap with margin on both
# sides -- high enough to catch every inaccurate-WCS session observed, low
# enough that a correctly-solved but genuinely sparse field is not
# re-solved needlessly.
MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS = 0.10

# Below this many identified stars the match fraction is too noisy to judge
# a WCS by -- a handful of stars can miss every catalog match by chance.
# Derivation: Alcor, the sparsest real field in the same run, contributed
# 34 detected stars at an 11.8% match rate; 20 keeps a field that sparse
# eligible for verification while excluding ones too thin to conclude
# anything from.
MIN_STARS_TO_VERIFY_REUSED_WCS = 20


def _write_wcs_to_header(path: str, wcs: WCS) -> None:
    """Merge `wcs`'s header cards into the FITS file at `path` in place.

    Mirrors the writeback already performed by the astrometry branch
    of `pipeline_tasks.analyze_target` so a future run against the
    same file can reuse this solve instead of re-solving.
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
    """Resolve a WCS for `image`, reusing an existing header WCS when possible.

    Checks `image.wcs` (auto-populated from the FITS header on load)
    first; if it's already a valid celestial solution, it's reused
    with no plate-solve at all. Otherwise, if `allow_solve`, a fresh
    solve is attempted via `star_identifier.solver`, using the same
    scale-hint estimation `StarIdentifier.process_image` uses. On a
    successful fresh solve, the result is written back to `image`'s
    FITS header (when `write_back`) so a later call against the same
    file can reuse it too.

    Parameters
    ----------
    image : `AstrometricsImage`
        The frame to resolve a WCS for.
    star_identifier : `StarIdentifier`
        Provides the configured `PlateSolver` and scale-hint
        estimation used for a fresh solve.
    allow_solve : `bool`, optional
        If `False`, only an existing header WCS can be returned; no
        plate-solve is ever attempted (default `True`). Used to
        forbid solving non-reference frames independently.
    center_ra, center_dec : `float`, optional
        RA/Dec hints, in degrees, for the field center (default
        `None`).
    sources : `list` [`dict`], optional
        Detected source list, used for a source-based online solve
        fallback if a local solve fails (default `None`).
    write_back : `bool`, optional
        Whether to write a freshly-solved WCS back to `image`'s FITS
        header (default `True`).
    ignore_existing_wcs : `bool`, optional
        If `True`, skip the header-WCS reuse path entirely and always
        plate-solve (default `False`). Used by
        `identify_session_stars` to re-derive a WCS after a reused
        header solution failed catalog verification -- `image.wcs` is
        still populated in that case, so without this the same
        untrustworthy solution would just be handed back again.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The resolved WCS, or `None` if no header WCS existed and
        either solving wasn't allowed or it failed.
    reused_existing_header_wcs : `bool`
        `True` if an existing header WCS was reused without solving.
    solve_attempted : `bool`
        `True` if a fresh plate-solve was actually attempted.
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
    """Count stars resolved to a real catalog identity.

    Uses `is_catalog_identified`, which both `_apply_simbad_match` and
    `_apply_gaia_match` set, rather than the presence of a spectral
    type -- a Gaia match records its spectral type as ``"Unknown"``,
    so a spectral-type test would silently miscount Gaia-identified
    stars as unidentified.

    Returns
    -------
    matched : `int`
        How many of `stellar_objects` carry a catalog identity.
    """
    return sum(1 for star in stellar_objects if star.is_catalog_identified)


def _reused_wcs_looks_untrustworthy(stellar_objects: list[StellarObject]) -> bool:
    """Report whether a reused header WCS matched too few catalog stars.

    A structurally-valid but astrometrically inaccurate header WCS
    projects stars far enough from their true sky positions that they
    fall outside the 10 arcsec SIMBAD/Gaia match radius, so almost
    nothing identifies. That collapse is the signal this looks for.

    Returns
    -------
    untrustworthy : `bool`
        `True` if there were enough stars to judge by and the catalog
        match fraction fell below
        `MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS`.
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
    max_detections: int = 100,
    write_back: bool = True,
) -> SessionIdentificationResult:
    """Detect and identify the stars on one session's reference frame.

    Detects sources on `reference_image`, resolves its WCS (reusing
    an existing header solution when present, per `resolve_frame_wcs`),
    and — when a WCS is available — runs full SIMBAD identification
    against every detected star via `StarIdentifier.identify_stars_with_wcs`,
    not just the single star nearest the image center.

    Parameters
    ----------
    reference_image : `AstrometricsImage`
        The session's reference frame to detect and identify stars on.
    star_identifier : `StarIdentifier`
        Provides source detection, the plate solver, and SIMBAD
        identification.
    center_ra, center_dec : `float`, optional
        RA/Dec hints, in degrees, for the field center (default
        `None`).
    max_detections : `int`, optional
        Maximum number of detected sources to identify, ranked by
        flux (default 100, matching `StarIdentifier.process_image`'s
        existing cap).
    write_back : `bool`, optional
        Whether to write a freshly-solved WCS back to the reference
        frame's FITS header (default `True`).

    Returns
    -------
    result : `SessionIdentificationResult`
        The resolved WCS, identified stars, and identification
        quality counters for this session.
    """
    data = reference_image.data
    if data is not None and data.ndim == 3:
        import numpy as np

        data = np.mean(data, axis=0) if data.shape[0] in (3, 4) else np.mean(data, axis=-1)

    sources = star_identifier.detector.detect(data)
    unique_sources = star_identifier.detector.deduplicate(sources)
    sources_detected = len(unique_sources)
    if len(unique_sources) > max_detections:
        unique_sources = unique_sources[:max_detections]

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
