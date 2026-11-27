"""Detect stars, map them to the sky, and identify their names.

This follows a step-by-step process to give every detected star a permanent
name:
  1. Search the SIMBAD database (matching stars within 10 arcseconds).
  2. If SIMBAD fails, search the Gaia DR3 database (matching within 10
  arcseconds).
  3. If both fail, name the star based on its coordinates (e.g.,
  ``FIELD_J{ra:.4f}{dec:+.4f}``).

This makes sure every star gets a stable, consistent name before it is saved to
the database, replacing temporary labels so duplicates are not saved.

`StarIdentifier` runs the steps for one image (`process_image`) and holds the
run's results. It builds on three layers, each in its own module, and each
may use the layers below it: `gaia_field_matching` (the run's state and the
Gaia naming), `simbad_matching` (the SIMBAD naming and the center-star rule)
and `plate_solution_steps` (the solver's scale hints and the Gaia refit). The
detection is in `star_detection` and the Gaia downloads in
`gaia_tile_queries`.
"""

import logging
import math
import warnings
from typing import Any

import numpy as np
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.drivers.fits_access import collapse_to_2d
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import (
    PlateSolveDriver,
    read_fit_statistics,
)
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.plate_solution import PlateSolution
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.astrometry.processing.gaia_depth import (
    GaiaDepthPrediction,
    predict_gaia_depth_from_header,
)
from astrometricslib.pipelines.astrometry.processing.gaia_field_matching import read_observation_time
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import (
    FieldGaiaStars,
)
from astrometricslib.pipelines.astrometry.processing.plate_solution_steps import PlateSolutionSteps
from astrometricslib.pipelines.astrometry.processing.star_detection import find_unique_stars
from astrometricslib.pipelines.astrometry.processing.wcs_refinement import (
    WcsRefinement,
    record_plate_scale,
)
from astrometricslib.utilities.exceptions import PlateSolveFailedError

logger = logging.getLogger(__name__)


# SIMBAD's client configuration, request timeout and thread lock live in
# drivers/astroquery_simbad_driver.py, alongside every other external-service
# driver, rather than here: they describe how to talk to astroquery, not
# how this pipeline identifies a star.

# --- Gaia connection health -------------------------------------------------
#
# When a Gaia service keeps failing, the outside-service gateway pauses its
# host (see `_internal/drivers/outside_services`), and every later tile
# request fails at once with no network use. The pause is shared by every
# process and ends on its own. This run only records that it happened (see
# `_GaiaRunState.circuit_open`), so the quality summary can say so. Locally
# cached Gaia data is still used; SIMBAD identification is unaffected.

# The maximum number of stars to send to the plate solver (which figures out
# where the telescope is pointing). The solver only needs the brightest stars
# to work. Sending every faint star makes the math take much longer without
# helping the solve succeed. This limit only applies to solving the image;
# we still measure the brightness of every star later.
MAXIMUM_PLATE_SOLVE_SOURCES = 100


def _brightest_pixel(data: Any) -> float | None:
    """Find the brightest pixel of an image, if it has a usable one.

    Parameters
    ----------
    data : `numpy.ndarray` or `None`
        The image pixels.

    Returns
    -------
    peak : `float` or `None`
        The largest finite pixel value, or `None` when the image is missing,
        empty or holds no finite value.
    """
    try:
        with warnings.catch_warnings():
            # An all-NaN image is expected here and handled below.
            warnings.simplefilter("ignore", RuntimeWarning)
            peak = float(np.nanmax(data))
    except TypeError, ValueError:
        return None
    return peak if math.isfinite(peak) else None


class StarIdentifier(PlateSolutionSteps):
    """The main tool for finding stars, mapping the image, and naming them."""

    def __init__(self, config: AppConfiguration | None = None, *, drivers: Drivers | None = None) -> None:
        """Set up the tools.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The system settings.
        drivers : `Drivers`, optional
            The plate solver and SIMBAD driver to use. Any left out is the
            built-in one (Astrometry.net, astroquery).
        """
        if config is None:
            from astrometricslib.foundation.config import get_configuration

            config = get_configuration()

        self.config = config
        self.detector = SourceDetector()

        # Extract API key for solver
        api_key = config.get_value(
            "Processing.Astrometry.Online Solver", "api_key", config.get_value("Astrometry", "api_key")
        )
        if api_key is not None and not isinstance(api_key, str) and hasattr(api_key, "_mock_methods"):
            api_key = "mock-api-key"
        drivers = drivers or Drivers()
        self.solver: PlateSolveDriver = drivers.plate_solve_or_default(api_key)
        self.simbad: SimbadDriver = drivers.simbad_or_default()
        self.stellar_objects: list[StellarObject] = []
        self.sources_detected: int = 0
        # The stars the last `process_image` call found, every one of them
        # (before the cap on how many are named). The astrometry run saves
        # them next to a stack for the viewer's Detections overlay.
        self.detected_unique_sources: list[dict] = []
        self.solve_attempted: bool = False
        # The brightest pixel of the last image processed, used to tell a real
        # star from a noise detection (see `_select_target_star`).
        self.frame_peak: float | None = None
        # The distance between where we calculated a star is and where the
        # database says it should be (measured in arcseconds). We use this
        # to figure out how accurate our image alignment is.
        self.catalog_match_separations_arcsec: list[float] = []
        # For each star with a sky position in the last
        # `identify_stars_with_wcs` call, keyed by `id(star)`: the distance,
        # in arcseconds, to its nearest SIMBAD or Gaia entry, whether or not
        # that entry is close enough to count as a match. The plate solution
        # check reads it through `nearest_catalog_separations_arcsec`.
        self._nearest_catalog_by_star: dict[int, float] = {}
        # The Gaia stars per square degree over the last image, counted in
        # `identify_stars_with_wcs` when Gaia was searched with every tile
        # under the image complete (see `field_catalog_density`). The plate
        # solution check sets its chance share from it. `None` otherwise.
        self.field_catalog_density_per_square_degree: float | None = None
        # The plate solver's own fit residual (RMS, arcseconds) and matched
        # star count for the last solve. `None` when the solver gave none.
        self.plate_solve_fit_residual_rms_arcsec: float | None = None
        self.plate_solve_matched_star_count: int | None = None
        # When the last image was taken (from DATE-OBS), or `None`. Gaia
        # positions are moved to this time.
        self.observation_time: Time | None = None
        # Stable keys for conditions that limit the last run's results; see
        # the ``FLAG_*`` constants.
        self.astrometry_flags: list[str] = []
        # What the Gaia refit of the plate solution found for the last image
        # (see `_refine_wcs_against_gaia`), or `None` before one was tried.
        self.wcs_refinement: WcsRefinement | None = None
        # One plain sentence saying what happened to the Gaia refit of the last
        # image: it was used, it was rejected (and why), it found no cached
        # Gaia stars, or it was not tried. Empty before any image.
        self.wcs_refit_note: str = ""
        # The peak over the background noise of each star the final Gaia fit
        # used, and why there is none (see `_record_fit_star_signal`). The
        # expected position error is found from them.
        self.gaia_fit_peak_over_noise: tuple[float, ...] = ()
        self.gaia_fit_signal_reason: str = "no Gaia fit was made"
        self._detected_on_binned_copy = False
        # The predicted Gaia query depth of the last image (from its header),
        # or `None` when no image header was read. A caller that names stars
        # of another image may set it before `identify_stars_with_wcs`.
        self.gaia_depth_prediction: GaiaDepthPrediction | None = None
        # The Gaia stars of the last image's tiles, and the tiles behind them
        # (see `_prepare_field_gaia_stars`). `None` before the first field.
        self.field_gaia_stars: FieldGaiaStars | None = None

    def use_image_header(self, header: Any) -> None:
        """Set the Gaia query depth and observation time from an image header.

        The observation time (``DATE-OBS``) is what moves Gaia positions from
        epoch 2016.0 to the night of the image. It is reset here too, so a
        header with no usable ``DATE-OBS`` never keeps the last image's time.

        `process_image` does this itself. A caller that names the stars of
        another image with `identify_stars_with_wcs` calls it first.

        Parameters
        ----------
        header : `Any`
            The image's FITS header, or `None` for the default depth.
        """
        self.gaia_depth_prediction = (
            None if header is None else predict_gaia_depth_from_header(header, self.config)
        )
        self.observation_time = read_observation_time(header)
        self.field_gaia_stars = None

    @staticmethod
    def _build_stellar_objects_from_sources(sources: list[dict]) -> list[StellarObject]:
        """Create a `StellarObject` for each dot of light found.

        Every star is given a temporary name like "Star_1", "Star_2".
        Later, an attempt is made to replace these with real database names.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The list of star objects ready for identification.
        """
        stellar_objects = []
        for i, src in enumerate(sources):
            obj = StellarObject()

            # Convert numpy scalars to native Python types for
            # serialization safety
            cleaned_src = {}
            if isinstance(src, dict):
                for k, v in src.items():
                    if hasattr(v, "item"):
                        cleaned_src[k] = v.item()
                    else:
                        cleaned_src[k] = v
            else:
                cleaned_src = src

            obj.star_data = cleaned_src
            radius_px = cleaned_src.get("radius_px") if isinstance(cleaned_src, dict) else None
            obj.radius_px = float(radius_px) if radius_px is not None else None
            obj.flux = float(cleaned_src.get("flux", 0.0)) if isinstance(cleaned_src, dict) else 0.0
            # Give every object a unique, non-empty placeholder id so
            # it is addressable before catalog identification runs.
            # The underscore format matches VariabilityAnalyzer's own
            # blind-detection id scheme (Star_N), keeping naming
            # consistent across both code paths.
            obj.id = f"Star_{i + 1}"
            obj.name = f"Star_{i + 1}"
            stellar_objects.append(obj)
        return stellar_objects

    def get_catalog_match_separation_rms_arcsec(self) -> float | None:
        """Measure how far detected stars sit from their catalog matches.

        This is the root mean square (RMS) of the distance from each detected
        star to its nearest SIMBAD or Gaia star. It is not the plate-solve fit
        residual. Only stars within `CATALOG_MATCH_RADIUS_ARCSEC` of a catalog
        star count, so the number cannot exceed that radius. A wrong match,
        and SIMBAD's uneven position precision, make it larger.

        Returns
        -------
        separation_rms_arcsec : `float` or `None`
            The RMS separation in arcseconds, or `None` if no star was
            matched.
        """
        separations = self.catalog_match_separations_arcsec
        if not separations:
            return None
        return round(math.sqrt(sum(value**2 for value in separations) / len(separations)), 4)

    def detect_stars(self, data: np.ndarray, is_color_frame: bool = False) -> tuple[list[dict], list[dict]]:
        """Find all the dots of light in the image and remove duplicates.

        This uses different tricks depending on whether it's a color image
        or a black-and-white (monochrome) image.

        Parameters
        ----------
        data : `numpy.ndarray`
            The image to search.
        is_color_frame : `bool`, optional
            True if this started as a color image. We handle color images
            differently to avoid false detections from debayering noise.

        Returns
        -------
        sources : `list` [`dict`]
            Everything we found that looks like a star.
        unique_sources : `list` [`dict`]
            The same list, but with duplicates removed.
        """
        return find_unique_stars(self.detector, data, is_color_frame=is_color_frame)

    def process_image(
        self,
        image_data_or_path: Any,
        attempt_plate_solving: bool = True,
        center_ra: float | None = None,
        center_dec: float | None = None,
        maximum_identified_stars: int | None = None,
        target_name: str | None = None,
        solve_only: bool = False,
        refit_from_cache: bool = False,
    ) -> tuple[list[StellarObject], WCS | None]:
        """Run the full process: find the stars, map the image, and name them.

        With `solve_only`, the process stops as soon as the solver has
        answered: no SIMBAD or Gaia naming and no online query of any kind.
        The Gaia refit is skipped too, unless `refit_from_cache` asks for a
        refit against the local cache alone.

        Parameters
        ----------
        image_data_or_path : `AstrometricsImage`, `numpy.ndarray`, or `str`
            The image we are analyzing.
        attempt_plate_solving : `bool`, optional
            If True, figure out exactly where the telescope was pointing.
        center_ra, center_dec : `float`, optional
            Hints about where the telescope was pointing.
        maximum_identified_stars : `int`, optional
            A cap on how many stars we look up in the database.
        target_name : `str`, optional
            The name of the target being imaged. Used only when the field is
            not solved: it names the star at the frame centre (see
            `_identify_stars_with_simbad`) and stops a planet from being
            given a star's name.
        solve_only : `bool`, optional
            If True, return the solver's solution as it is and skip every
            step that asks an online catalog. The stars come back unnamed.
        refit_from_cache : `bool`, optional
            Used only with `solve_only`. If True, refit the solution to the
            Gaia stars of a complete cached download that covers the field. The
            cache is the only source: nothing is sent to a remote service. With
            no such download the refit is skipped and the flag
            ``wcs_refinement_skipped`` is recorded.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The final list of named stars (unnamed when `solve_only`).
        wcs : `astropy.wcs.WCS` or `None`
            The map data, or None if the mapping process failed.

        Raises
        ------
        PlateSolveFailedError
            If plate solving is attempted and the field cannot be solved.
        """
        # 1. Load Image
        header = None
        if isinstance(image_data_or_path, str):
            img = AstrometricsImage(image_data_or_path)
            data = img.data
            path = image_data_or_path
            header = img.header
        elif isinstance(image_data_or_path, AstrometricsImage):
            data = image_data_or_path.data
            path = image_data_or_path.path
            header = image_data_or_path.header
        else:
            data = image_data_or_path
            path = None
        self.observation_time = read_observation_time(header)
        self.use_image_header(header)

        is_color_frame = data is not None and data.ndim == 3
        if is_color_frame:
            data = collapse_to_2d(data)
        self.frame_peak = _brightest_pixel(data)

        # 2. Detect Stars
        logger.info("Detecting stars...")
        sources, unique_sources = self.detect_stars(data, is_color_frame=is_color_frame)
        logger.debug("Detected %s sources, %s unique.", len(sources), len(unique_sources))
        self.sources_detected = len(unique_sources)
        self.detected_unique_sources = list(unique_sources)
        self.catalog_match_separations_arcsec = []
        self._nearest_catalog_by_star = {}
        self.plate_solve_fit_residual_rms_arcsec = None
        self.plate_solve_matched_star_count = None
        self.astrometry_flags = []
        self.wcs_refinement = None
        self.wcs_refit_note = "not refit: it was not tried"
        self.gaia_fit_peak_over_noise = ()
        self.gaia_fit_signal_reason = "no Gaia fit was made"
        self._detected_on_binned_copy = is_color_frame

        # We limit how many stars we send to the plate solver because it
        # only needs the brightest ones to figure out where the image is
        # pointing. Sending too many faint stars just slows it down.
        # However, the rest of the program wants as many stars as possible
        # for analysis, so we keep the full list for them.
        #
        # We take the stars from the beginning of the list because they
        # are already sorted from brightest to dimmest.
        solver_sources = unique_sources[:MAXIMUM_PLATE_SOLVE_SOURCES]
        # Every detection, kept for the Gaia refit of the solution. The
        # identification limit below keeps the brightest stars, which in a
        # globular cluster such as M 13 are the unresolved core, where Gaia
        # has no star to match.
        all_detected_sources = unique_sources

        # An explicit argument wins over configuration, and an explicit
        # 0 means "no limit" so a caller can override a configured cap
        # without having to read the configuration first.
        identification_limit = maximum_identified_stars
        if identification_limit is None:
            identification_limit = self.config.get_maximum_identified_stars()
        if isinstance(identification_limit, int) and identification_limit > 0:
            if len(unique_sources) > identification_limit:
                logger.info(
                    "Identifying the brightest %s of %s detected sources, per the configured limit.",
                    identification_limit,
                    len(unique_sources),
                )
            unique_sources = unique_sources[:identification_limit]

        self.stellar_objects = self._build_stellar_objects_from_sources(unique_sources)
        self.solve_attempted = False

        if not self.stellar_objects:
            return [], None

        # 3. Plate Solve
        wcs = None
        if attempt_plate_solving:
            # Gated on solver_sources (built from unique_sources before the
            # maximum_identified_stars truncation), not on self.stellar_objects
            # (built after it) -- maximum_identified_stars is documented as
            # only capping database lookups, so a low value must not also
            # silently disable a solve that would otherwise succeed.
            self.solve_attempted = len(solver_sources) >= 4
            if not self.solve_attempted:
                logger.info("Skipping plate solve: only %s sources detected.", len(solver_sources))
            else:
                logger.info(
                    "Solving field with %s of %s sources...", len(solver_sources), len(unique_sources)
                )
                h, w = data.shape

                # Determine scale hints dynamically. We
                # use a very narrow 5% window here because the
                # plate solve driver will relax it by another 20%.
                scale_lower, scale_upper = self._calculate_scale_hints(image_data_or_path)

                solved_header = self.solver.solve(
                    image_path=path,
                    sources=solver_sources,
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

                if solved_header:
                    logger.info("Field solved!" if solve_only else "Field solved! Querying SIMBAD...")
                    # The solver's own fit statistics, when it gave any. They
                    # are the residual the quality gate judges; the catalog
                    # match separation stays a separate, weaker number.
                    (
                        self.plate_solve_fit_residual_rms_arcsec,
                        self.plate_solve_matched_star_count,
                    ) = read_fit_statistics(solved_header)
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", FITSFixedWarning)
                        wcs = WCS(solved_header, naxis=2)
                    record_plate_scale(wcs)
                    if solve_only:
                        if refit_from_cache:
                            wcs = self._refine_wcs_against_gaia(
                                wcs, all_detected_sources, h, w, cache_only=True
                            )
                        return self.stellar_objects, wcs
                    wcs = self._refine_wcs_against_gaia(wcs, all_detected_sources, h, w)
                    self._identify_stars_with_simbad(wcs, center_ra, center_dec, w, h)
                else:
                    logger.error("Could not solve field.")
                    raise PlateSolveFailedError("Plate solving failed: could not solve field.")
        else:
            # Not attempting plate solving, but we can still identify
            # if we have hints
            if center_ra is not None and center_dec is not None:
                logger.info("Using RA/Dec hints for center star identification (skipping solve)...")
                h, w = data.shape
                self._identify_stars_with_simbad(None, center_ra, center_dec, w, h, target_name=target_name)

        return self.stellar_objects, wcs

        # If we never got a sky position (centroid projection failed),
        # keep the Star_N placeholder id from
        # _build_stellar_objects_from_sources -- it is at least unique
        # and non-empty.


def solve_image(image_path: str, *, config: Any = None, drivers: Any = None) -> PlateSolution | None:
    """Plate solve one image in memory and describe the solution.

    This is the owner of `processing.astrometry.solve`. Nothing is written:
    neither the image nor the catalog.

    Parameters
    ----------
    image_path : `str`
        The FITS file to solve.
    config : `AppConfiguration`, optional
        The settings.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.

    Returns
    -------
    solution : `PlateSolution` or `None`
        The image center, pixel scale, rotation and the sky map's header
        cards. `None` when the image cannot be read or too few stars are
        found to try a solve. A solve that is tried and fails raises
        `PlateSolveFailedError`.
    """
    from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
    from astrometricslib.pipelines.shared.header_wcs import describe_plate_solution

    _, wcs = StarIdentifier(config=config, drivers=drivers).process_image(
        image_path, attempt_plate_solving=True
    )
    if wcs is None:
        return None
    if wcs.pixel_shape:
        width, height = wcs.pixel_shape
    else:
        try:
            header = read_header(image_path, first_hdu_only=True)
        except FITS_READ_ERRORS:
            return None
        width, height = int(header.get("NAXIS1", 0)), int(header.get("NAXIS2", 0))
    return describe_plate_solution(image_path, wcs, int(width), int(height))
