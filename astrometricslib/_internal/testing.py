"""Purpose: Build an `Astrometrics` with fake drivers, for tests only.

Description: `Astrometrics(...)` takes no driver arguments, so a program
always runs the built-in plate solver, stacking program and catalog clients.
A test that needs a fake one builds the root with `astrometrics_with_drivers`.
Only tests, and the library's own scripts, may import this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrometricslib import Astrometrics
from astrometricslib._internal.drivers.driver_set import Drivers

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration
    from astrometricslib._internal.drivers.interfaces import (
        GaiaXpDriver,
        PlateSolveDriver,
        ReddeningDriver,
        SimbadDriver,
        StackingDriver,
    )
    from astrometricslib.api import AbstractCatalogAccess

__all__ = ["astrometrics_with_drivers"]


def astrometrics_with_drivers(
    config: AppConfiguration | None = None,
    catalog_access: AbstractCatalogAccess | None = None,
    *,
    plate_solve_driver: PlateSolveDriver | None = None,
    stacking_driver: StackingDriver | None = None,
    simbad_driver: SimbadDriver | None = None,
    gaia_xp_driver: GaiaXpDriver | None = None,
    reddening_driver: ReddeningDriver | None = None,
) -> Astrometrics:
    """Build an `Astrometrics` that uses the given drivers, for tests.

    `Astrometrics(...)` itself takes no drivers. A test that needs a fake
    plate solver, stacking program or catalog client builds the root with
    this function instead. A driver left out stays the built-in one.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application settings. The default settings are loaded if not
        given.
    catalog_access : `AbstractCatalogAccess`, optional
        The database. A default one is built from `config` if not given.
    plate_solve_driver : `PlateSolveDriver`, optional
        The plate solver.
    stacking_driver : `StackingDriver`, optional
        The stacking program.
    simbad_driver : `SimbadDriver`, optional
        The SIMBAD database client.
    gaia_xp_driver : `GaiaXpDriver`, optional
        The source of Gaia DR3 XP spectra.
    reddening_driver : `ReddeningDriver`, optional
        The source of interstellar reddening values.

    Returns
    -------
    astrometrics : `Astrometrics`
        A root whose sub-APIs use the given drivers.
    """
    astrometrics = Astrometrics.__new__(Astrometrics)
    astrometrics._build(
        config,
        catalog_access,
        Drivers(
            plate_solve=plate_solve_driver,
            stacking=stacking_driver,
            simbad=simbad_driver,
            gaia_xp=gaia_xp_driver,
            reddening=reddening_driver,
        ),
    )
    return astrometrics
