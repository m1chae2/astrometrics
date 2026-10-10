"""Purpose: Carry the drivers a caller chose down to the code that uses them.

Description: The library talks to four outside programs or services: a plate
solver, a stacking program, the SIMBAD database and the Gaia DR3 XP spectra.
Each has an abstract base class in `drivers/interfaces/`. `Drivers` holds the
instance a caller wants for each one. A field left as `None` means "use the
built-in driver" (Astrometry.net, Siril and astroquery), and the `*_or_default`
methods build that built-in driver on demand.

`Astrometrics(...)` builds one `Drivers` and passes it to the sub-APIs, which
pass it on to the pipelines. This file is the only place in `pipelines/` or
`api/` that names a built-in driver class, so a test can swap in a fake
solver, stacking engine, SIMBAD or Gaia XP source without any network, Siril or
astrometry.net. A test in `test/test_layer_boundaries.py` keeps it that way.
"""

from dataclasses import dataclass

from astrometricslib.drivers.interfaces.gaia_xp_driver import GaiaXpDriver
from astrometricslib.drivers.interfaces.plate_solve_driver import PlateSolveDriver
from astrometricslib.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.drivers.interfaces.stacking_driver import StackingDriver

__all__ = ["Drivers"]


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
    """

    plate_solve: PlateSolveDriver | None = None
    stacking: StackingDriver | None = None
    simbad: SimbadDriver | None = None
    gaia_xp: GaiaXpDriver | None = None

    @property
    def any_chosen(self) -> bool:
        """Say whether the caller supplied at least one driver.

        Returns
        -------
        any_chosen : `bool`
            `True` if any field is not `None`.
        """
        return any(
            driver is not None for driver in (self.plate_solve, self.stacking, self.simbad, self.gaia_xp)
        )

    def plate_solve_or_default(self, api_key: str | None = None) -> PlateSolveDriver:
        """Give the chosen plate solver, or build the built-in one.

        Parameters
        ----------
        api_key : `str`, optional
            The Astrometry.net online key. Only the built-in solver uses it.

        Returns
        -------
        solver : `PlateSolveDriver`
            The chosen solver, or a new `AstrometryNetPlateSolveDriver`.
        """
        if self.plate_solve is not None:
            return self.plate_solve
        from astrometricslib.drivers.astrometry_net_driver import AstrometryNetPlateSolveDriver

        return AstrometryNetPlateSolveDriver(api_key=api_key)

    def stacking_or_default(self) -> StackingDriver:
        """Give the chosen stacking program, or build the built-in one.

        Returns
        -------
        engine : `StackingDriver`
            The chosen engine, or a new `SirilStackingDriver`.
        """
        if self.stacking is not None:
            return self.stacking
        from astrometricslib.drivers.siril_stacking_driver import SirilStackingDriver

        return SirilStackingDriver()

    def simbad_or_default(self) -> SimbadDriver:
        """Give the chosen SIMBAD driver, or build the built-in one.

        Returns
        -------
        simbad : `SimbadDriver`
            The chosen driver, or a new `AstroquerySimbadDriver`.
        """
        if self.simbad is not None:
            return self.simbad
        from astrometricslib.drivers.astroquery_simbad_driver import AstroquerySimbadDriver

        return AstroquerySimbadDriver()

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
        from astrometricslib.drivers.gaia_xp_driver import AstroqueryGaiaXpDriver

        return AstroqueryGaiaXpDriver()
