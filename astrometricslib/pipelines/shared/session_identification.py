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
from typing import Any, Protocol

from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib._internal.drivers.fits_access import (
    FITS_READ_ERRORS,
    PLATE_SOLVED_KEYWORD,
    header_is_plate_solved,
    update_first_header,
)
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import (
    PlateSolveDriver,
    read_fit_statistics,
)
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.catalog_star_identity import CATALOG_MATCH_RADIUS_ARCSEC

logger = logging.getLogger(__name__)


class SessionStarIdentifier(Protocol):
    """The parts of a star identifier this module uses.

    The caller passes in the astrometry pipeline's `StarIdentifier`. This
    shared module names only the methods it calls, so it does not import
    the astrometry pipeline, which sits in a layer above it.
    """

    solver: PlateSolveDriver

    @property
    def nearest_catalog_separations_arcsec(self) -> list[float]:
        """Give each star's distance to its nearest catalog entry.

        Returns
        -------
        separations : `list` [`float`]
            One distance, in arcseconds, for each star that had a sky
            position in the last `identify_stars_with_wcs` call, including
            stars too far from any entry to match.
        """
        ...

    def detect_stars(self, data: Any, is_color_frame: bool = False) -> tuple[list[dict], list[dict]]:
        """Find the stars in an image and drop duplicates.

        Returns
        -------
        sources, unique_sources : `tuple` [`list` [`dict`], `list` [`dict`]]
            Every detection, and the detections left after duplicates go.
        """
        ...

    def _calculate_scale_hints(self, image_data_or_path: Any) -> tuple[float | None, float | None]:
        """Guess the image's pixel scale range for the plate solver.

        Returns
        -------
        scale_hints : `tuple` [`float` or `None`, `float` or `None`]
            The smallest and largest likely scale, in arcseconds per
            pixel.
        """
        ...

    def _build_stellar_objects_from_sources(self, sources: list[dict]) -> list[StellarObject]:
        """Make one `StellarObject` for each detected source.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            One star per source, with a temporary name.
        """
        ...

    def use_image_header(self, header: Any) -> None:
        """Set the Gaia query depth from the header of the image to be named.

        Parameters
        ----------
        header : `Any`
            The image's FITS header.
        """
        ...

    def identify_stars_with_wcs(
        self, stellar_objects: list[StellarObject], wcs: WCS, width: int, height: int
    ) -> list[StellarObject]:
        """Name each star from the catalogs, using the image's sky map.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The same stars, with catalog names where one matched.
        """
        ...


# Minimum fraction of a reference frame's detected stars that must resolve
# to a real catalog identity (SIMBAD or Gaia) before a *reused* header WCS
# is trusted. Below this, the header solution is discarded and the frame is
# plate-solved fresh.
# This number is used because tests show that a good alignment matches
# at least ~28% of stars, while a bad alignment matches less than ~6%.
# Setting the limit to 0.10 (10%) easily separates the good from the bad,
# ensuring a correct solution isn't accidentally thrown away just
# because the star field is sparse.
# The share does not separate them in a dense field. A wrong solution still
# pairs a star with whatever catalog star happens to fall within the match
# radius. The chance is 1 - exp(-pi R^2 n) for a catalog of n stars per unit
# area: about 17% for a 10 arcsecond radius in a Milky Way field with
# 7,700 catalog stars per square degree, and about 30% at the 14 arcsecond
# radius of a Nikon D5300 behind a 300 mm lens. There the concentration
# test of `plate_solution_problem` (MIN_MATCH_CONCENTRATION) is what catches
# a wrong solution. Both a reused header map and a photometry
# session's solution go through both tests (AST-D19).
MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS = TunedParameter(
    name="shared.min_catalog_match_fraction_for_reused_wcs",
    default=0.10,
    unit="fraction",
    description="Smallest share of stars that must match the catalog for a reused plate solution to stand.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-D19",
)

# Below this many identified stars the match fraction is too noisy to judge
# a WCS by -- a handful of stars can miss every catalog match by chance.
# The value 20 is small enough to include very sparse star fields, but
# large enough to give a reliable percentage.
MIN_STARS_TO_VERIFY_REUSED_WCS = TunedParameter(
    name="shared.min_stars_to_verify_reused_wcs",
    default=20,
    unit="stars",
    description="Fewest stars needed to check a reused plate solution.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-D19",
)


# The circle the concentration test looks in, as a multiple of the catalog
# match radius R (`CATALOG_MATCH_RADIUS_ARCSEC`, resolved for the run's plate
# scale). The test counts the stars whose nearest catalog entry lies within
# this wider circle W, whether or not that entry is close enough to match,
# and asks what share of them have it inside R. Design estimate (AST-D19).
SOLUTION_CHECK_RADIUS_FACTOR = TunedParameter(
    name="shared.solution_check_radius_factor",
    default=2.0,
    unit="multiple of match radius",
    description="Radius of the plate solution check's search circle, as a multiple of the match radius.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-D19",
)

# Smallest share, among the stars whose nearest catalog entry lies within the
# search circle W, of those whose entry also lies within the match radius R.
# Under a wrong solution each nearest entry is a chance neighbor, about
# equally likely anywhere in the circle, so the share is near (R/W)^2, 0.25
# for W = 2R. It rises in a dense field, where a star often has several
# entries within W: about 0.32 at 10 arcsec and 0.40 at 14 arcsec with 7,700
# catalog stars per square degree. A right solution puts true neighbors
# inside R. Faint detections with no true catalog star add chance neighbors
# to both counts, which lowers the share but does not move it to the
# wrong-solution value. On the real catalog (backup of 2026-11-24), right
# solutions had median match distances of 6.5 arcsec (Algol, ZWO ASI533MM,
# above the old limit of half the 10 arcsec radius) and 6.2 arcsec (M 67,
# Nikon D5300), so a median test cannot separate them. Design estimate
# until shifted real solutions are checked (AST-D19).
MIN_MATCH_CONCENTRATION = TunedParameter(
    name="shared.min_match_concentration",
    default=0.4,
    unit="fraction",
    description=(
        "Smallest share of catalog neighbors in the search circle that lie within the match radius."
    ),
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-D19",
)


def plate_solution_problem(
    stellar_objects: list[StellarObject], nearest_catalog_separations_arcsec: list[float]
) -> str | None:
    """Say why a run's plate solution looks wrong, judged by its stars.

    A wrong solution puts every star somewhere it is not. Few stars then
    find their catalog star (`MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS`),
    and each star's nearest catalog entry is a chance neighbor, spread
    evenly over the sky around it. Among the stars with a neighbor inside
    the search circle (`SOLUTION_CHECK_RADIUS_FACTOR` times the match
    radius), the share whose neighbor is also inside the match radius is
    then near the ratio of the two areas, below `MIN_MATCH_CONCENTRATION`.
    Saving such a run would make new position-only stars at false positions
    and give light curves to the wrong stars.

    The match radius is `CATALOG_MATCH_RADIUS_ARCSEC` as the current run
    resolves it (it follows the plate scale), the same radius the star
    identifier matched with. Call this inside the run that identified the
    stars.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The run's identified stars.
    nearest_catalog_separations_arcsec : `list` [`float`]
        For each star with a sky position, the distance in arcseconds to
        its nearest catalog entry, matched or not (the star identifier's
        `nearest_catalog_separations_arcsec` from the same run).

    Returns
    -------
    problem : `str` or `None`
        A sentence saying what is wrong, or `None` when the solution looks
        right or there are fewer than `MIN_STARS_TO_VERIFY_REUSED_WCS`
        stars to judge by. The concentration test alone is skipped when
        fewer than that many stars have a neighbor in the search circle.
    """
    stars = [star for star in stellar_objects if star.right_ascension is not None]
    minimum_stars = resolve(MIN_STARS_TO_VERIFY_REUSED_WCS).value
    if len(stars) < minimum_stars:
        return None
    matched_fraction = _catalog_matched_count(stars) / len(stars)
    if matched_fraction < resolve(MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS).value:
        return f"only {matched_fraction:.0%} of {len(stars)} stars matched a catalog star"
    match_radius = float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value)
    search_radius = float(resolve(SOLUTION_CHECK_RADIUS_FACTOR).value) * match_radius
    in_search_circle = [
        distance for distance in nearest_catalog_separations_arcsec if distance <= search_radius
    ]
    if len(in_search_circle) < minimum_stars:
        return None
    concentration = sum(1 for distance in in_search_circle if distance <= match_radius) / len(
        in_search_circle
    )
    limit = float(resolve(MIN_MATCH_CONCENTRATION).value)
    if concentration < limit:
        return (
            f"only {concentration:.0%} of the {len(in_search_circle)} stars with a catalog entry within "
            f"{search_radius:.1f} arcsec have it within the {match_radius:.1f} arcsec match radius "
            f"(limit {limit:.0%})"
        )
    return None


# The header card that says the sky map was refit to Gaia stars after the
# solver made it (see `wcs_refinement`). A file with no card, or a false one,
# holds the solver's own solution, which the analysis step may still refit
# and save (`stage.save_refit_stack_plate_solution`).
REFIT_TO_GAIA_KEYWORD = "WCSREFIT"

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


def header_is_refit_to_gaia(header: fits.Header) -> bool:
    """Say whether a header's sky map was refit to Gaia stars.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The first header of a FITS file.

    Returns
    -------
    is_refit : `bool`
        True when the header carries `REFIT_TO_GAIA_KEYWORD` as true.
    """
    return bool(header.get(REFIT_TO_GAIA_KEYWORD, False))


def put_wcs_in_header(
    header: fits.Header, wcs: WCS, *, mark_plate_solved: bool, refit_to_gaia: bool | None = None
) -> None:
    """Replace the sky-map keywords of a header in memory.

    The old sky-map keywords are deleted first (see `write_wcs_to_fits_header`
    for why), then the keywords of ``wcs`` are written, with the SIP
    distortion terms.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header to change.
    wcs : `astropy.wcs.WCS`
        The sky map to store.
    mark_plate_solved : `bool`
        Whether to write `PLATE_SOLVED_KEYWORD`.
    refit_to_gaia : `bool`, optional
        Whether to write `REFIT_TO_GAIA_KEYWORD` as true or false. Left out,
        the card is not touched, so a solution that is only moved (turned or
        cropped) keeps its mark.
    """
    for keyword in [key for key in header if _STALE_WCS_KEYWORD_PATTERN.match(key)]:
        del header[keyword]
    for card in wcs.to_header(relax=True).cards:
        if not card.keyword:
            continue
        header[card.keyword] = (card.value, card.comment)
    if mark_plate_solved:
        header[PLATE_SOLVED_KEYWORD] = (True, "sky map written by a plate solve")
    if refit_to_gaia is not None:
        header[REFIT_TO_GAIA_KEYWORD] = (refit_to_gaia, "sky map refit to Gaia stars")


def write_wcs_to_fits_header(path: str, wcs: WCS, *, refit_to_gaia: bool = False) -> None:
    """Save a solved sky map (WCS) into a FITS file's header.

    The next program that opens the file (this library, Siril, a FITS
    viewer) then gets the solved pointing without solving again.

    The function first deletes the old sky-map keywords, so an earlier
    solve cannot leave cards that contradict the new one. It then writes
    the new keywords with ``to_header(relax=True)``. Without ``relax=True``,
    astropy leaves out the SIP distortion terms (``A_*``, ``B_*``,
    ``AP_*``, ``BP_*``) and removes ``-SIP`` from ``CTYPE``. A reader of the
    file would then lose the lens distortion near the image edges.

    It also writes the card `PLATE_SOLVED_KEYWORD`, so a reader can tell this
    solution from a pointing guess (see `header_is_plate_solved`), and the
    card `REFIT_TO_GAIA_KEYWORD`, so a reader can tell a refit solution from
    the solver's own (see `header_is_refit_to_gaia`).

    The function logs a warning and returns if the file cannot be updated.

    Parameters
    ----------
    path : `str`
        The FITS file to update. A missing or empty path does nothing.
    wcs : `astropy.wcs.WCS`
        The solved sky map to store.
    refit_to_gaia : `bool`, optional
        True when ``wcs`` was refit to Gaia stars. Written to the header either
        way, so an older mark never survives a new solution.
    """
    if not path or not os.path.exists(path):
        return
    try:

        def _write_solved_wcs(header: fits.Header) -> bool:
            """Replace the old WCS keywords in the header with the solved ones.

            Parameters
            ----------
            header : `astropy.io.fits.Header`
                The header of HDU 0.

            Returns
            -------
            changed : `bool`
                Always `True`, because the header is changed.
            """
            put_wcs_in_header(header, wcs, mark_plate_solved=True, refit_to_gaia=refit_to_gaia)
            return True

        update_first_header(path, _write_solved_wcs)
        logger.info("Updated FITS file %s header with solved WCS keywords.", path)
    except FITS_READ_ERRORS as wcs_error:
        logger.warning("Failed to update FITS file header with WCS: %s", wcs_error)


def resolve_frame_wcs(
    image: AstrometricsImage,
    star_identifier: SessionStarIdentifier,
    allow_solve: bool = True,
    center_ra: float | None = None,
    center_dec: float | None = None,
    sources: list[dict] | None = None,
    write_back: bool = True,
    ignore_existing_wcs: bool = False,
    solve_timeout: int = 300,
    trust_image_wcs: bool = False,
) -> tuple[WCS | None, bool, bool]:
    """Figure out the sky map (WCS) for an image.

    It tries to be lazy and use the map already saved in the image file.
    If there isn't one, it calculates a new one from scratch and saves it.

    Parameters
    ----------
    image : `AstrometricsImage`
        The image to map.
    star_identifier : `SessionStarIdentifier`
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
    trust_image_wcs : `bool`, optional
        Use the image's sky map even when its header does not mark it as
        plate solved (`header_is_plate_solved`). Set when the caller put a
        solved stack's map on the image itself. Otherwise a header map that
        is only the mount's pointing (as INDI writes) is not used.

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
        trust_image_wcs=trust_image_wcs,
    )
    return wcs, reused_existing_header_wcs, solve_attempted


def _resolve_frame_wcs_with_fit(
    image: AstrometricsImage,
    star_identifier: SessionStarIdentifier,
    allow_solve: bool = True,
    center_ra: float | None = None,
    center_dec: float | None = None,
    sources: list[dict] | None = None,
    write_back: bool = True,
    ignore_existing_wcs: bool = False,
    solve_timeout: int = 300,
    trust_image_wcs: bool = False,
) -> tuple[WCS | None, bool, bool, tuple[float | None, int | None]]:
    """Find an image's sky map (WCS) and keep the solver's fit numbers.

    This does the work of `resolve_frame_wcs`. The only difference is one
    extra return value, the fit statistics of a fresh solve.

    Parameters
    ----------
    image : `AstrometricsImage`
        The image to map.
    star_identifier : `SessionStarIdentifier`
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
    trust_image_wcs : `bool`, optional
        Use the image's sky map even when its header does not mark it as
        plate solved (`header_is_plate_solved`). Set when the caller put a
        solved stack's map on the image itself. Otherwise a header map that
        is only the mount's pointing (as INDI writes) is not used.

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
    if (
        not ignore_existing_wcs
        and image.wcs is not None
        and image.wcs.is_celestial
        and (trust_image_wcs or _image_header_is_plate_solved(image))
    ):
        # NOTE: is_celestial is a *structural* check (does this WCS have
        # RA/Dec axes), not an accuracy one -- a header solution that is
        # off by tens of arcsec passes it just as readily as a good one.
        # A header map that is only the mount's pointing is refused above:
        # on the M 31 frames of 2026-11-04 (ZWO ASI 533MM Pro, 405 mm) the
        # one INDI writes sits dRA +34", dDec +44" from the solved frame,
        # which put 303 stars outside the catalog match radius.
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


def _image_header_is_plate_solved(image: Any) -> bool:
    """Say whether an image's header map came from a plate solve.

    Parameters
    ----------
    image : `AstrometricsImage`
        The image.

    Returns
    -------
    is_solved : `bool`
        `True` when the header passes `header_is_plate_solved`. An image
        without a header gives `False`.
    """
    header = getattr(image, "header", None)
    if header is None:
        return False
    return header_is_plate_solved(header)


def _catalog_matched_count(stellar_objects: list[StellarObject]) -> int:
    """Count how many stars were successfully looked up in the database.

    Returns
    -------
    matched : `int`
        The number of stars positively identified.
    """
    return sum(1 for star in stellar_objects if star.is_catalog_identified)


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
    # True when a reused header WCS failed `plate_solution_problem` (too few
    # catalog matches, or nearest catalog entries not concentrated inside
    # the match radius) and was replaced by a fresh plate solve.
    header_wcs_replaced_after_verification: bool = False
    # For each star in `stellar_objects` with a sky position, the distance in
    # arcseconds to its nearest catalog entry, matched or not. It belongs to
    # the identification that made `stellar_objects`, and is what
    # `plate_solution_problem` needs beside the stars.
    nearest_catalog_separations_arcsec: list[float] = field(default_factory=list)
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
    star_identifier: SessionStarIdentifier,
    center_ra: float | None = None,
    center_dec: float | None = None,
    max_detections: int | None = None,
    write_back: bool = True,
    trust_image_wcs: bool = False,
) -> SessionIdentificationResult:
    """Find and name all the stars in the first image of a sequence.

    This looks at the image, maps it out, and then checks the database
    to figure out the name of every single star it can see.

    Parameters
    ----------
    reference_image : `AstrometricsImage`
        The image being analyzed.
    star_identifier : `SessionStarIdentifier`
        The tool that does the math and database lookups.
    center_ra, center_dec : `float`, optional
        Hints about where the telescope was pointing.
    max_detections : `int`, optional
        If 5,000 stars are found, looking them all up might take forever.
        This puts a cap on how many of the brightest stars are identified.
    write_back : `bool`, optional
        Whether to save the calculated map data back into the image file.
    trust_image_wcs : `bool`, optional
        Use a sky map the caller put on the image even though its header
        is not marked as plate solved (see `resolve_frame_wcs`). It is
        still checked against the catalog.

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
    star_identifier.use_image_header(getattr(reference_image, "header", None))

    stellar_objects = star_identifier._build_stellar_objects_from_sources(unique_sources)

    wcs, reused_existing_header_wcs, solve_attempted, fit_statistics = _resolve_frame_wcs_with_fit(
        reference_image,
        star_identifier,
        allow_solve=True,
        center_ra=center_ra,
        center_dec=center_dec,
        sources=unique_sources,
        write_back=write_back,
        trust_image_wcs=trust_image_wcs,
    )

    height, width = (data.shape[0], data.shape[1]) if data is not None else (0, 0)

    nearest_separations: list[float] = []
    if wcs is not None and stellar_objects:
        star_identifier.identify_stars_with_wcs(stellar_objects, wcs, width, height)
        nearest_separations = list(star_identifier.nearest_catalog_separations_arcsec)

    (
        wcs,
        stellar_objects,
        nearest_separations,
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
        nearest_separations,
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
        nearest_catalog_separations_arcsec=nearest_separations,
        fit_residual_rms_arcsec=fit_statistics[0],
        matched_star_count=fit_statistics[1],
    )


def _detect_and_limit_session_sources(
    reference_image: AstrometricsImage, star_identifier: SessionStarIdentifier, max_detections: int | None
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
        from astrometricslib._internal.drivers.fits_access import collapse_to_2d

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
    star_identifier: SessionStarIdentifier,
    unique_sources: list[dict],
    center_ra: float | None,
    center_dec: float | None,
    wcs: WCS | None,
    stellar_objects: list[StellarObject],
    nearest_separations: list[float],
    reused_existing_header_wcs: bool,
    solve_attempted: bool,
    width: int,
    height: int,
    write_back: bool,
    fit_statistics: tuple[float | None, int | None],
) -> tuple[WCS | None, list[StellarObject], list[float], bool, bool, bool, tuple[float | None, int | None]]:
    """Re-solve a reused header WCS that turns out to be untrustworthy.

    Parameters
    ----------
    reference_image : `AstrometricsImage`
        The frame being identified.
    star_identifier : `SessionStarIdentifier`
        The tool that solves and identifies.
    unique_sources : `list` [`dict`]
        The detected sources.
    center_ra, center_dec : `float` or `None`
        Hints about where the telescope was pointing.
    wcs : `astropy.wcs.WCS` or `None`
        The WCS from the first pass.
    stellar_objects : `list` [`StellarObject`]
        The stars identified against `wcs`.
    nearest_separations : `list` [`float`]
        The nearest catalog distances of that identification.
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
    nearest_separations : `list` [`float`]
        The nearest catalog distances of the identification against the
        returned `wcs`.
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
    problem = (
        plate_solution_problem(stellar_objects, nearest_separations) if reused_existing_header_wcs else None
    )
    if problem is None:
        return (
            wcs,
            stellar_objects,
            nearest_separations,
            reused_existing_header_wcs,
            solve_attempted,
            False,
            fit_statistics,
        )

    matched_before = _catalog_matched_count(stellar_objects)
    logger.warning(
        "Reused header WCS for %s looks wrong (%s); discarding it and plate-solving this frame fresh.",
        reference_image.path,
        problem,
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
    # What is returned when the header WCS stays: the first pass's stars and
    # distances, not the fresh pass's, which the identifier now holds.
    kept = (
        wcs,
        stellar_objects,
        nearest_separations,
        reused_existing_header_wcs,
        solve_attempted,
        False,
        fit_statistics,
    )

    if fresh_wcs is None:
        return kept

    # Identify onto *fresh* objects: the first pass already mutated
    # the originals (ids, names, coordinates), so reusing them would
    # compare a re-identified list against itself.
    fresh_objects = star_identifier._build_stellar_objects_from_sources(unique_sources)
    star_identifier.identify_stars_with_wcs(fresh_objects, fresh_wcs, width, height)
    fresh_separations = list(star_identifier.nearest_catalog_separations_arcsec)
    matched_after = _catalog_matched_count(fresh_objects)

    if matched_after <= matched_before:
        logger.info(
            "Fresh plate solve for %s did not improve catalog matches (%s -> %s); keeping the header WCS.",
            reference_image.path,
            matched_before,
            matched_after,
        )
        return kept

    logger.info(
        "Fresh plate solve for %s improved catalog matches %s -> %s; using it instead of the header WCS.",
        reference_image.path,
        matched_before,
        matched_after,
    )
    if write_back:
        write_wcs_to_fits_header(reference_image.path, fresh_wcs)
    return fresh_wcs, fresh_objects, fresh_separations, False, solve_attempted, True, fresh_fit_statistics
