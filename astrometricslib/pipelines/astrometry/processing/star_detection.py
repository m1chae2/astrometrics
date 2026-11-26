"""Finds the unique stars in an image and estimates how faint they reach.

`StarIdentifier.detect_stars` uses `find_unique_stars`: it runs the source
detector, bins a color (Bayer) frame first so the detector sees whole stars,
and removes duplicate detections. `estimate_faintest_detected_magnitude`
reads how faint the detected stars go from their catalog magnitudes, so the
Gaia query depth can be checked against it.
"""

import logging
import math

import numpy as np
from astropy.nddata import block_reduce

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_blob_width_from_data
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import CatalogBand, catalog_band_of
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.table_cells import optional_float

logger = logging.getLogger(__name__)


# --- Preparing the Image for Star Detection ---------------------------------
#
# The star-finding algorithm assumes background noise is random for
# every pixel. Color images break this rule because the debayering
# process interpolates colors, linking neighboring pixels together.
# This tricks the algorithm into thinking the noise clumps are
# actually stars. Tests showed this causes thousands of false
# detections and makes it nearly impossible to match real stars to
# the catalog.
#
# To fix this, shrink the image to half its size (2x2 averaging)
# before looking for stars. Tests prove this successfully breaks up
# the noise clumps without missing any real stars, dropping false
# detections significantly.
COLOR_DETECTION_BIN_FACTOR = TunedParameter(
    name="astrometry.color_detection_bin_factor",
    default=2,
    unit="px per bin side",
    description="How many pixels per side are averaged into one before stars are found in a color frame.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.EQUIPMENT_PROFILE,
    equipment=frozenset({EquipmentKind.CAMERA}),
    register_entry="AST-D03",
)


# The faint end of a frame's detections, as a percentile of their fluxes. On
# the NGC 7331 stack (2026-11-20) the single faintest detection read G 20.2
# and flagged the depth as exceeded, while 99 percent of the Gaia-matched
# stars were brighter than G 19.6. Structure of the check, not a tuned value.
FAINT_END_PERCENTILE = 1.0


MINIMUM_STARS_FOR_DEPTH_ESTIMATE = TunedParameter(
    name="astrometry.minimum_stars_for_depth_estimate",
    default=5,
    unit="stars",
    description="Fewest catalog-identified stars needed to estimate the Gaia search depth from the frame.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-D11",
)


def _block_average(data: np.ndarray, factor: int) -> np.ndarray:
    """Shrink an image by averaging groups of pixels together.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image data.
    factor : `int`
        How many pixels to group together (e.g., 2 means 2x2 blocks).

    Returns
    -------
    binned : `numpy.ndarray`
        The shrunk image.
    """
    return block_reduce(data, factor, func=np.mean)


def _rescale_source_centroids(sources: list[dict], factor: int) -> None:
    """Convert star locations found in a shrunk image back to normal size.

    Parameters
    ----------
    sources : `list` [`dict`]
        The list of stars found.
    factor : `int`
        The same shrink factor used in `_block_average`.
    """
    offset = (factor - 1) / 2.0
    for source in sources:
        for x_key, y_key in (("x_centroid", "y_centroid"), ("xcentroid", "ycentroid")):
            if x_key in source and source[x_key] is not None:
                source[x_key] = source[x_key] * factor + offset
            if y_key in source and source[y_key] is not None:
                source[y_key] = source[y_key] * factor + offset
        if source.get("radius_px") is not None:
            source["radius_px"] = source["radius_px"] * factor


def estimate_faintest_detected_magnitude(stellar_objects: list[StellarObject]) -> float | None:
    """Estimate the Gaia G magnitude of the faintest detected star of a frame.

    The detected stars that Gaia identified have both a measured flux and a
    Gaia G magnitude. Magnitude is ``zero_point - 2.5 * log10(flux)``, so
    each such star gives a G zero point, and the median is the frame's. Stars
    SIMBAD named carry a V magnitude and are left out (see
    `catalog_zero_point.catalog_band_of`), so the answer is in the G band the
    query depth is set in. The faint end is the flux that 99 percent of the
    detections exceed (`FAINT_END_PERCENTILE`), not the single faintest one,
    because the very faintest detections are mostly noise peaks. It is only
    a check of the predicted query depth (see `gaia_depth.depth_exceeded`);
    it does not set the depth.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        Every detected star of the frame, after the catalog matching.

    Returns
    -------
    magnitude : `float` or `None`
        The estimate, or `None` when fewer than
        `MINIMUM_STARS_FOR_DEPTH_ESTIMATE` stars have both a positive flux
        and a Gaia G magnitude.
    """
    fluxes: list[float] = []
    zero_points: list[float] = []
    for star in stellar_objects:
        flux = optional_float(star.flux)
        if flux is None or flux <= 0:
            continue
        fluxes.append(flux)
        # Only Gaia G magnitudes give a G zero point: a SIMBAD match carries a
        # V magnitude, which differs from G by the star's color.
        magnitude = optional_float(star.magnitude)
        if (
            star.is_catalog_identified
            and magnitude is not None
            and catalog_band_of(star) is CatalogBand.GAIA_G
        ):
            zero_points.append(magnitude + 2.5 * math.log10(flux))
    if len(zero_points) < resolve(MINIMUM_STARS_FOR_DEPTH_ESTIMATE).value:
        return None
    faint_flux = float(np.percentile(fluxes, FAINT_END_PERCENTILE, method="lower"))
    return float(np.median(zero_points)) - 2.5 * math.log10(faint_flux)


def find_unique_stars(
    detector: SourceDetector,
    data: np.ndarray,
    is_color_frame: bool = False,
    with_radius: bool = True,
    size_kernel_from_image: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Find the stars of an image the way the astrometry run does.

    The run (`StarIdentifier.detect_stars`) and the viewer's detection
    overlay both call this one function, so the markers the viewer draws are
    the stars the run finds. A color frame is averaged in blocks first. A
    black-and-white frame sizes the finder's kernel from the star width
    measured in the image. Then the finder runs, and two reports of one star
    are merged into one.

    Parameters
    ----------
    detector : `SourceDetector`
        The star finder. Its ``fwhm`` is set from the image when the width
        can be measured, so the caller may read the width actually used.
    data : `numpy.ndarray`
        The image to search, two-dimensional.
    is_color_frame : `bool`, optional
        True if the image started as a color image.
    with_radius : `bool`, optional
        Whether to measure each star's ``radius_px``. The run reads it. The
        viewer's overlay does not, and skipping it saves most of the time.
        The stars, their positions and their fluxes are the same either way.
    size_kernel_from_image : `bool`, optional
        Whether a black-and-white frame sizes the kernel from its measured
        star width, as the run does. `False` keeps the detector's own
        ``fwhm``, for a caller that chose the width.

    Returns
    -------
    sources : `list` [`dict`]
        Everything the finder reported.
    unique_sources : `list` [`dict`]
        The same list with each star reported twice merged into one.
    """
    if is_color_frame and data is not None:
        bin_factor = resolve(COLOR_DETECTION_BIN_FACTOR).value
        binned = _block_average(data, bin_factor)
        sources = detector.detect(binned, with_radius=with_radius)
        unique_sources = detector.deduplicate(sources)
        _rescale_source_centroids(sources, bin_factor)
        _rescale_source_centroids(unique_sources, bin_factor)
        return sources, unique_sources

    try:
        blob_width = (
            measure_blob_width_from_data(data) if data is not None and size_kernel_from_image else None
        )
    except DATA_ERRORS as width_error:
        logger.debug(
            "Could not measure the detection kernel width, keeping the configured default: %s",
            width_error,
        )
        blob_width = None
    if blob_width is not None:
        logger.debug("Sizing the detection kernel from the measured blob width: %.2fpx", blob_width)
        detector.fwhm = blob_width

    sources = detector.detect(data, with_radius=with_radius)
    unique_sources = detector.deduplicate(sources)
    return sources, unique_sources
