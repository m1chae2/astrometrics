"""Tools for figuring out exactly where our picture is pointing in the sky.

Includes plate solving (mapping pixels to coordinates) and identifying
which specific stars are in the image.
"""

from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

__all__ = ["SourceDetector", "StarIdentifier"]
