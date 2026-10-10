"""Purpose: Driver interfaces for outside programs and services.

Description: One abstract base class per outside program or service the
library uses: `StackingDriver` (a stacking program such as Siril),
`PlateSolveDriver` (a plate solver such as Astrometry.net),
`SimbadDriver` (the SIMBAD database) and `ReddeningDriver` (a catalog of
interstellar reddening values). The rest of the library depends on
these interfaces, and each concrete driver in `drivers/` implements one.
"""

from astrometricslib.drivers.interfaces.plate_solve_driver import PlateSolveDriver, PlateSolveHeader
from astrometricslib.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
from astrometricslib.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.drivers.interfaces.stacking_driver import StackingDriver, StackRunResult, StackSettings

__all__ = [
    "PlateSolveDriver",
    "PlateSolveHeader",
    "ReddeningDriver",
    "ReddeningEstimate",
    "SimbadDriver",
    "StackRunResult",
    "StackSettings",
    "StackingDriver",
]
