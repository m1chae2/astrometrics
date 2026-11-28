"""Purpose: Private `drivers` names of `astrometricslib`.

Description: The private names of the `drivers` group: the driver interfaces
and the driver code. Only `astrometricslib` itself, and the tests of either
library, may import this module. Each name is imported from the module where
its code lives today, so moving the code later changes this file only.

The old package text, kept: Tools that talk directly to hardware, files, and
other programs.

This includes the Siril interface, the target/stellar catalog database
(`catalog_access.py`, `local_database.py`, `catalog_store.py`), and the basic
tools every driver uses to read and write FITS image files (`fits_access.py`,
`image.py`, `filter_detection.py`).

Pure image-quality measurement (FWHM, saturation, background level, per-target
frame statistics) doesn't belong here: it doesn't manage an external resource
(a subprocess, a database connection) the way these tools do, so it lives in
`pipelines/shared/quality/` instead. The parsers of Siril's own output and file
formats are not quality measurements; they live in the Siril driver
(`siril/core/output_parsing.py`).
"""

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess, CatalogAccess
from astrometricslib._internal.drivers.interfaces.gaia_xp_driver import GaiaXpDriver
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import PlateSolveDriver, PlateSolveHeader
from astrometricslib._internal.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib._internal.drivers.interfaces.stacking_driver import (
    StackingDriver,
    StackRunResult,
    StackSettings,
)
from astrometricslib._internal.drivers.siril.stacking import ImageProcessing

__all__ = [
    "AbstractCatalogAccess",
    "CatalogAccess",
    "GaiaXpDriver",
    "ImageProcessing",
    "PlateSolveDriver",
    "PlateSolveHeader",
    "ReddeningDriver",
    "ReddeningEstimate",
    "SimbadDriver",
    "StackRunResult",
    "StackSettings",
    "StackingDriver",
]
