"""Purpose: Carry the drivers a caller chose down to the code that uses them.

Description: The library talks to five outside programs or services: a plate
solver, a stacking program, the SIMBAD database, the Gaia DR3 XP spectra
and a catalog of interstellar reddening values. Each has an abstract base
class in `drivers/interfaces/`. `Drivers` holds the instance a caller wants
for each one. A field left as `None` means "use the built-in driver"
(Astrometry.net, Siril, astroquery, and Gaia through astroquery), and the
`*_or_default` methods build that built-in driver on demand.

`Astrometrics(...)` builds one `Drivers` (empty, so every driver is the
built-in one; a test builds its own through
`astrometricslib._internal.testing`) and passes it to the sub-APIs, which
pass it on to the pipelines. This file is the only place in `pipelines/` or
`api/` that names a built-in driver class, so a test can swap in a fake
solver, stacking engine, SIMBAD or Gaia XP source without any network, Siril or
astrometry.net. A test in `test/test_layer_boundaries.py` keeps it that way.
"""

from dataclasses import dataclass

from astrometricslib._internal.drivers.interfaces.gaia_xp_driver import GaiaXpDriver
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import PlateSolveDriver
from astrometricslib._internal.drivers.interfaces.reddening_driver import ReddeningDriver
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib._internal.drivers.interfaces.stacking_driver import StackingDriver

__all__ = ["Drivers", "get_stacking_driver"]


@dataclass(frozen=True)
class Drivers:
    """The drivers a caller chose, with `None` for "use the built-in one".

    Attributes
    ----------
    plate_solve : `PlateSolveDriver` or `None`
        The plate solver. `None` means Astrometry.net.
    stacking : `StackingDriver` or `None`
        The stacking program. `None` means Siril.
    simbad : `SimbadDriver` or `None`
        The SIMBAD database. `None` means astroquery.
    gaia_xp : `GaiaXpDriver` or `None`
        The Gaia DR3 XP spectra. `None` means astroquery, with a cache on disk.
    reddening : `ReddeningDriver` or `None`
        The source of interstellar reddening values. `None` means Gaia DR3
        through astroquery.
    """

    plate_solve: PlateSolveDriver | None = None
    stacking: StackingDriver | None = None
    simbad: SimbadDriver | None = None
    gaia_xp: GaiaXpDriver | None = None
    reddening: ReddeningDriver | None = None

    @property
    def any_chosen(self) -> bool:
        """Say whether the caller supplied at least one driver.

        Returns
        -------
        any_chosen : `bool`
            `True` if any field is not `None`.
        """
        chosen = (self.plate_solve, self.stacking, self.simbad, self.gaia_xp, self.reddening)
        return any(driver is not None for driver in chosen)

    def plate_solve_or_default(self, api_key: str | None = None) -> PlateSolveDriver:
        """Give the chosen plate solver, or build the built-in one.

        Parameters
        ----------
        api_key : `str`, optional
            The Astrometry.net online key. Only the built-in solvers use it.

        Returns
        -------
        solver : `PlateSolveDriver`
            The chosen solver, or a new `SirilPlateSolveDriver`.
        """
        if self.plate_solve is not None:
            return self.plate_solve
        from astrometricslib._internal.drivers.siril.astrometry.solve_driver import SirilPlateSolveDriver

        return SirilPlateSolveDriver(api_key=api_key)

    def stacking_or_default(self) -> StackingDriver:
        """Give the chosen stacking program, or build the built-in one.

        Returns
        -------
        engine : `StackingDriver`
            The chosen engine, or a new `SirilStackingDriver`.
        """
        if self.stacking is not None:
            return self.stacking
        from astrometricslib._internal.drivers.siril.stacking.stacking_driver import SirilStackingDriver

        return SirilStackingDriver()

    def simbad_or_default(self) -> SimbadDriver:
        """Give the chosen SIMBAD driver, or build the built-in one.

        Returns
        -------
        simbad : `SimbadDriver`
            The chosen driver, or a `CachedSimbadDriver` around a new
            `AstroquerySimbadDriver` (answers are stored and reused).
        """
        if self.simbad is not None:
            return self.simbad
        from astrometricslib._internal.drivers.astroquery_simbad_driver import AstroquerySimbadDriver
        from astrometricslib._internal.drivers.cached_simbad_driver import CachedSimbadDriver

        return CachedSimbadDriver(AstroquerySimbadDriver())

    def gaia_xp_or_default(self) -> GaiaXpDriver:
        """Give the chosen Gaia XP driver, or build the built-in one.

        Returns
        -------
        gaia_xp : `GaiaXpDriver`
            The chosen driver, or a new `AstroqueryGaiaXpDriver` that caches
            spectra in the library's data folder.
        """
        if self.gaia_xp is not None:
            return self.gaia_xp
        from astrometricslib._internal.drivers.gaia_xp_driver import AstroqueryGaiaXpDriver

        return AstroqueryGaiaXpDriver()

    def reddening_or_default(self) -> ReddeningDriver:
        """Give the chosen reddening source, or build the built-in one.

        Returns
        -------
        reddening : `ReddeningDriver`
            The chosen driver, or a new `AstroqueryGaiaReddeningDriver`.
        """
        if self.reddening is not None:
            return self.reddening
        from astrometricslib._internal.drivers.astroquery_gaia_reddening_driver import (
            AstroqueryGaiaReddeningDriver,
        )

        return AstroqueryGaiaReddeningDriver()


def get_stacking_driver() -> StackingDriver:
    """Give the built-in stacking program's driver (Siril).

    The backend uses it for the two things a desktop app does with the
    stacking program that are not a pipeline run: open a stack in Siril's
    window (``open_in_viewer(path)``) and stop every Siril this program
    started when the app stops (``stop_running_programs()``).

    Returns
    -------
    engine : `StackingDriver`
        A new `SirilStackingDriver`.
    """
    return Drivers().stacking_or_default()
