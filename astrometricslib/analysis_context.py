"""AnalysisContext: A data container for a single analysis session.

Holds the image, detected objects, and world coordinate system.
"""

from dataclasses import dataclass

from astropy.wcs import WCS

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.utilities.image import AstrometricsImage


@dataclass
class AnalysisContext:
    """A context object that bundles image data and metadata for analysis."""

    image: AstrometricsImage
    stellar_objects: list[StellarObject]
    wcs: WCS | None = None
    extended_target: StellarObject | None = None
    sources_detected: int = 0
    solve_attempted: bool = False
