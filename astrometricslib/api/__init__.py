"""Domain-astrometrics classes for astrometricslib (Layer 1).

Each astrometrics class is the single entry point external callers (scripts,
backend services, other libraries) should use for a given domain.
Layer 1 may reach into any lower layer (`astrometricslib.tasks`,
`astrometricslib.data_access`, `astrometricslib.drivers`) directly --
that is what a astrometrics is for. The rule runs the other way: nothing
above Layer 1 imports those lower-layer modules directly, and nothing
in those lower-layer modules calls back up into a astrometrics.

Internal. Import these from the top-level `astrometricslib` namespace
instead -- this subpackage is not part of the supported public API.
"""

from astrometricslib.api.moving_objects import MovingObjectRecovery
from astrometricslib.api.processing import CalibrationCatalog, ProcessingPipelines, QualityDiagnostics
from astrometricslib.api.stars import StellarCatalog
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.api.visualization import Visualization

__all__ = [
    "CalibrationCatalog",
    "MovingObjectRecovery",
    "ProcessingPipelines",
    "QualityDiagnostics",
    "StellarCatalog",
    "TargetCatalog",
    "Visualization",
]
