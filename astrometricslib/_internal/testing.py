"""Purpose: Build an `Astrometrics` with fake drivers, for tests only.

Description: `Astrometrics(config)` takes no driver or database arguments, so
a program always runs the built-in plate solver, stacking program and catalog
clients over the database its settings name. A test that needs a fake one, or
a stand-in database, builds the root with `astrometrics_with_drivers`.
`read_saved_target` reads one target's stored record straight from storage,
to check what a save wrote. `data_version_calls` gives the public
data-version calls of a library folder without building an `Astrometrics`,
so a stand-in library in a test can answer them as the real one does. Only
tests, and the library's own scripts, may import this module.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from astrometricslib import Astrometrics
from astrometricslib._internal.drivers.driver_set import Drivers

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration, Target
    from astrometricslib._internal.drivers.interfaces import (
        GaiaXpDriver,
        PlateSolveDriver,
        ReddeningDriver,
        SimbadDriver,
        StackingDriver,
    )
    from astrometricslib.api import AbstractCatalogAccess

__all__ = ["astrometrics_with_drivers", "data_version_calls", "read_saved_target"]


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


def read_saved_target(astrometrics: Astrometrics, target_id: str) -> Target | None:
    """Read one target's saved record straight from storage.

    The targets held in memory are not used or changed, so the answer is
    what another program would see.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library whose storage to read.
    target_id : `str`
        The exact id of the target.

    Returns
    -------
    target : `Target` or `None`
        The stored target, or `None` if nothing is stored under that id.
    """
    from astrometricslib.pipelines.shared.target_records import read_saved_target as read_saved

    return read_saved(astrometrics.targets, target_id)


def data_version_calls(config: AppConfiguration) -> SimpleNamespace:
    """Give the public data-version calls of a library folder.

    Each call is the owner function behind the public method, over the
    library that `config` names. Nothing is built or written, so a test of a
    stand-in library can give the stand-in the real versions of the rows it
    writes to that folder.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings that name the library folder.

    Returns
    -------
    calls : `types.SimpleNamespace`
        ``targets`` (``get_data_version``, ``get_record_versions``),
        ``records`` (``get_data_version``, ``get_camera_data_version``) and
        ``stacks`` (``get_data_version``), each as on `Astrometrics`.
    """
    from astrometricslib.pipelines.shared import data_version_texts
    from astrometricslib.pipelines.shared.quality import stored_measurements

    return SimpleNamespace(
        targets=SimpleNamespace(
            get_data_version=lambda target: data_version_texts.get_target_data_version(config, target),
            get_record_versions=lambda: data_version_texts.get_target_record_versions(config),
        ),
        records=SimpleNamespace(
            get_data_version=lambda **options: stored_measurements.get_data_version(config, **options),
            get_camera_data_version=lambda **options: data_version_texts.get_camera_data_version(
                config, **options
            ),
        ),
        stacks=SimpleNamespace(get_data_version=lambda: data_version_texts.get_stack_data_version(config)),
    )
