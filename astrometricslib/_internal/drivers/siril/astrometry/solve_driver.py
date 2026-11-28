"""Purpose: Siril as the app's local plate solver.

Description: `SirilPlateSolveDriver` implements the `PlateSolveDriver`
interface (`drivers/interfaces/plate_solve_driver.py`) for image astrometry
and for mount centering. Both reach it through the astrometry pipeline's star
identifier, so both solve the same way and the result feeds the same solution
check against Gaia and the same star identification as before.

The driver solves locally with Siril (`plate_solve.solve_image`). An image that
Siril cannot solve at all, because its header holds no position, goes to the
online Astrometry.net service, which the base class already reaches (it needs
an API key and the internet). The base class
(`AstrometryNetPlateSolveDriver`) keeps that online path and its retry on
dropped connections. Its local ``solve-field`` solver is replaced here; the
code that runs it is removed in a later step of the same issue.

`siril_is_ready` says whether Siril and its Gaia catalog are installed. The
driver set (`drivers/driver_set.py`) builds this driver only then, and
otherwise keeps ``solve-field``.
"""

import logging
from typing import Any

from astropy.io import fits

from astrometricslib._internal.drivers.astrometry_net_driver import AstrometryNetPlateSolveDriver
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import SOLVE_TIMEOUT_SECONDS, solve_image
from astrometricslib._internal.drivers.siril.core.program import find_local_gaia_catalog
from astrometricslib.foundation.config import AppConfiguration, get_configuration

logger = logging.getLogger(__name__)


def siril_is_ready(config: AppConfiguration | None = None) -> bool:
    """Say whether Siril can solve plates on this computer.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The settings. Defaults to the shared configuration.

    Returns
    -------
    ready : `bool`
        `True` when the ``siril_executable`` setting names a program and
        Siril's Gaia astrometry catalog is on the disk. The program itself is
        not started here.
    """
    configuration = config or get_configuration()
    return bool(configuration.get_siril_executable()) and find_local_gaia_catalog() is not None


class SirilPlateSolveDriver(AstrometryNetPlateSolveDriver):
    """Plate solves an image with Siril, and online when Siril cannot."""

    def __init__(self, api_key: str | None = None, config: AppConfiguration | None = None) -> None:
        """Set up the solver.

        Parameters
        ----------
        api_key : `str`, optional
            The password for the online service. Without one, an image that
            Siril cannot solve is not solved.
        config : `AppConfiguration`, optional
            The settings that name Siril. Defaults to the shared
            configuration.
        """
        super().__init__(api_key=api_key, config=config)
        self.siril_executable = self.config.get_siril_executable() or ""

    def _local_solve_is_worth_trying(self, sources: list[dict[str, Any]] | None) -> bool:
        """Say that the local solve is always tried.

        Siril finds its own stars, so the number of stars the app found does
        not decide whether Siril can solve the image.

        Parameters
        ----------
        sources : `list` [`dict`] or `None`
            The stars the app found. Not used.

        Returns
        -------
        worth_trying : `bool`
            Always `True`.
        """
        del sources
        return True

    def _solve_locally(self, image_path: str, **kwargs: Any) -> fits.Header | None:
        """Solve the image with Siril, in a short Siril process of its own.

        The run never waits for the stack's Siril slot, so mount centering is
        not held up by a stack that is running.

        Parameters
        ----------
        image_path : `str`
            The image file.
        **kwargs
            The hints of `PlateSolveDriver.solve`.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solved header, or `None` if Siril could not solve the image.
        """
        timeout = kwargs.get("solve_timeout")
        limit = min(float(timeout), SOLVE_TIMEOUT_SECONDS) if timeout else SOLVE_TIMEOUT_SECONDS
        return solve_image(image_path, self.siril_executable, kwargs, timeout_seconds=limit)
