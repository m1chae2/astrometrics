"""High-level interfaces for the astrometrics library.

This folder contains the primary classes used to interact
with the library. Think of these classes as the front door: they hide
the complex, lower-level code and provide a clean, easy-to-use API
for scripts, services, or other programs.

Note: Import from this folder should not be done directly. Import these
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
