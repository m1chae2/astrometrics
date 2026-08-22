"""Astrometry orchestration tasks: plate solving, star identification."""

from astrometricslib.tasks.shared.source_detection_shared import SourceDetector
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import PlateSolver
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

__all__ = ["PlateSolver", "SourceDetector", "StarIdentifier"]
