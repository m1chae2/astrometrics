"""A container for the data used during an image analysis session.

It holds the image itself, any objects (like stars) found in it, and the
mapping that connects image pixels to real sky coordinates (WCS).
"""

from dataclasses import dataclass

from astropy.wcs import WCS

from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.models.stellar_source import StellarObject


@dataclass
class ExtendedSourceHint:
    """Where a large, non-stellar target (like a nebula) sits in an image.

    Astrometry finds this out by matching the target's name against
    SIMBAD and mapping its catalog size onto the image with the solved
    WCS. It doesn't know what to do with that information beyond
    reporting it -- turning it into a measurement region is specific
    to whichever pipeline needs one (spectroscopy sizes a dispersion
    box; a future pipeline might size something else), so this hint
    only carries the plain facts, not a ready-made region.
    """

    object_name: str
    otype: str
    extraction_center: tuple[float, float]
    extraction_radius_px: int


@dataclass
class AnalysisContext:
    """Everything one image's analysis pass builds up as it runs."""

    image: AstrometricsImage
    stellar_objects: list[StellarObject]
    wcs: WCS | None = None
    extended_target: StellarObject | None = None
    extended_source_hint: ExtendedSourceHint | None = None
    sources_detected: int = 0
    solve_attempted: bool = False
    astrometric_residual_rms_arcsec: float | None = None
