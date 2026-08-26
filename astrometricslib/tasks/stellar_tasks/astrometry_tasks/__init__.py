"""Tools for figuring out exactly where our picture is pointing in the sky.

Includes plate solving (mapping pixels to coordinates) and identifying
which specific stars are in the image.
"""

from astrometricslib.tasks.shared.source_detection_shared import SourceDetector
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import PlateSolver
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

__all__ = ["PlateSolver", "SourceDetector", "StarIdentifier"]
