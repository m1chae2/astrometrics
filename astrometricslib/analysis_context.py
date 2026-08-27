"""A container for the data used during an image analysis session.

It holds the image itself, any objects (like stars) found in it, and the
mapping that connects image pixels to real sky coordinates (WCS).
"""

from dataclasses import dataclass

from astropy.wcs import WCS

from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.models.stellar_source import StellarObject


@dataclass
class AnalysisContext:
    """Everything one image's analysis pass builds up as it runs."""

    image: AstrometricsImage
    stellar_objects: list[StellarObject]
    wcs: WCS | None = None
    extended_target: StellarObject | None = None
    sources_detected: int = 0
    solve_attempted: bool = False
    astrometric_residual_rms_arcsec: float | None = None
