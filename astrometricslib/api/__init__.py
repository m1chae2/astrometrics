"""High-level interfaces for the astrometrics library.

This folder contains the primary classes you should use to interact
with the library. Think of these classes as the front door: they hide
the complex, lower-level code and provide a clean, easy-to-use API
for scripts, services, or other programs.

Note: You shouldn't import from this folder directly. Import these
classes from the main `astrometricslib` namespace instead.
"""

from astrometricslib.api.moving_objects import MovingObjectRecovery
from astrometricslib.api.processing import CalibrationCatalog, ProcessingPipelines, QualityDiagnostics
from astrometricslib.api.stars import StellarCatalog
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.api.visualization import Visualization
from astrometricslib.data_access.catalog_access import AbstractCatalogAccess, CatalogAccess

__all__ = [
    "AbstractCatalogAccess",
    "CalibrationCatalog",
    "CatalogAccess",
    "MovingObjectRecovery",
    "ProcessingPipelines",
    "QualityDiagnostics",
    "StellarCatalog",
    "TargetCatalog",
    "Visualization",
]
