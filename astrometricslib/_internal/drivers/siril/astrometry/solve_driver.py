"""Purpose: Siril as the app's local plate solver.

Description: `SirilPlateSolveDriver` implements the `PlateSolveDriver`
interface (`drivers/interfaces/plate_solve_driver.py`) for image astrometry
and for mount centering. Both reach it through the astrometry pipeline's star
identifier, so both solve the same way and the result feeds the same solution
check against Gaia and the same star identification as before.

The driver solves with Siril (`plate_solve.solve_image`) and never falls back
to ``solve-field``. A frame with a start position that Siril cannot solve is
reported as not solved. Only a frame with no start position, which Siril cannot
solve, goes to the online Astrometry.net service, which the base class
(`AstrometryNetPlateSolveDriver`) reaches (it needs an API key and the
internet) with its retry on dropped connections. The base class's local
``solve-field`` code is not called here; a later step of the same issue
removes it.
"""

import logging
from typing import Any

from astropy.io import fits

from astrometricslib._internal.drivers.astrometry_net_driver import AstrometryNetPlateSolveDriver
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import (
    SOLVE_TIMEOUT_SECONDS,
    read_inputs,
    solve_image,
)
from astrometricslib.foundation.config import AppConfiguration

logger = logging.getLogger(__name__)


class SirilPlateSolveDriver(AstrometryNetPlateSolveDriver):
    """Plate solves an image with Siril, and online when it has no position."""

    def __init__(self, api_key: str | None = None, config: AppConfiguration | None = None) -> None:
        """Set up the solver.

        Parameters
        ----------
        api_key : `str`, optional
            The password for the online service. Without one, an image with
            no start position is not solved.
        config : `AppConfiguration`, optional
            The settings that name Siril. Defaults to the shared
            configuration.
        """
        super().__init__(api_key=api_key, config=config)
        self.siril_executable = self.config.get_siril_executable() or ""

    def solve(
        self,
        image_path: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        image_width: int = 1000,
        image_height: int = 1000,
        **hints: Any,
    ) -> fits.Header | None:
        """Solve the image with Siril, or online when it has no position.

        An image with a start position (in the hints or the header) is solved
        only by Siril, in a short Siril process of its own that never waits
        for the stack's Siril slot, so mount centering is not held up by a
        stack. If Siril cannot solve it, the answer is "not solved", with the
        reason logged: there is no other solver to fall back to. An image
        with no start position, which Siril cannot solve, goes to the online
        service (its stars first, then the whole image).

        Parameters
        ----------
        image_path : `str`, optional
            The image file.
        sources : `list` [`dict`], optional
            Stars already found, used only for the online solve.
        image_width, image_height : `int`, optional
            The image size, needed for the online solve from `sources`.
        **hints
            The hints of `PlateSolveDriver.solve`. ``solve_timeout`` is cut
            to Siril's own limit.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solved header, or `None` if the image was not solved.
        """
        hints.setdefault("verbose", False)
        if image_path:
            try:
                header = read_header(image_path, first_hdu_only=True)
            except FITS_READ_ERRORS as read_error:
                logger.warning("Not solved: %s cannot be read: %s", image_path, read_error)
                return None
            if read_inputs(header, hints) is not None:
                timeout = hints.get("solve_timeout")
                limit = min(float(timeout), SOLVE_TIMEOUT_SECONDS) if timeout else SOLVE_TIMEOUT_SECONDS
                solved = solve_image(image_path, self.siril_executable, hints, timeout_seconds=limit)
                if solved is None:
                    logger.warning(
                        "Not solved: Siril found no solution for %s from its header position "
                        "(see the Siril lines above for the reason).",
                        image_path,
                    )
                return solved
            logger.info("%s has no start position; asking the online solver.", image_path)
        if sources:
            online = self._solve_online_sources(sources, image_width, image_height, **hints)
            if online:
                return online
        if image_path:
            return self._solve_online_image(image_path, **hints)
        return None
