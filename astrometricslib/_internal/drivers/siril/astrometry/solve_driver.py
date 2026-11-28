"""Purpose: Siril as the app's plate solver.

Description: `SirilPlateSolveDriver` implements the `PlateSolveDriver`
interface (`drivers/interfaces/plate_solve_driver.py`) for image astrometry
and for mount centering. Both reach it through the astrometry pipeline's star
identifier, so both solve the same way and the result feeds the same solution
check against Gaia and the same star identification.

The driver solves with Siril (`plate_solve.solve_image`). A frame with a start
position that Siril cannot solve is reported as not solved; there is no other
solver to fall back to. Siril's own reason is kept in `last_failure_reason`
and logged as a warning, so the job that asked for the solve can list it.
Only a frame with no start position, which Siril cannot solve, goes to the
online Astrometry.net service (`AstrometryNetOnlineClient`). That needs an API
key and the internet, and it retries when the connection drops.
"""

import logging
from collections.abc import Sequence
from typing import Any

from astropy.io import fits

from astrometricslib._internal.drivers.astrometry_net_client import AstrometryNetOnlineClient
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import FrameSolve, PlateSolveDriver
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import (
    SOLVE_TIMEOUT_SECONDS,
    read_inputs,
    solve_image,
)
from astrometricslib._internal.drivers.siril.astrometry.sequence_solve import solve_frames
from astrometricslib.foundation.config import AppConfiguration, get_configuration

logger = logging.getLogger(__name__)


class SirilPlateSolveDriver(PlateSolveDriver):
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
        self.config = config or get_configuration()
        self.siril_executable = self.config.get_siril_executable() or ""
        self.online_client = AstrometryNetOnlineClient(api_key)

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
        stack. If Siril cannot solve it, the answer is "not solved", and
        Siril's reason is kept in `last_failure_reason` and logged as a
        warning: there is no other solver to fall back to. An image with no
        start position, which Siril cannot solve, goes to the online service
        (its stars first, then the whole image).

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
        self.last_failure_reason = None
        hints.setdefault("verbose", False)
        if image_path:
            try:
                header = read_header(image_path, first_hdu_only=True)
            except FITS_READ_ERRORS as read_error:
                self._record_failure(image_path, f"the image cannot be read: {read_error}")
                return None
            if read_inputs(header, hints) is not None:
                timeout = hints.get("solve_timeout")
                limit = min(float(timeout), SOLVE_TIMEOUT_SECONDS) if timeout else SOLVE_TIMEOUT_SECONDS
                outcome = solve_image(image_path, self.siril_executable, hints, timeout_seconds=limit)
                if outcome.header is None:
                    self._record_failure(image_path, outcome.failure_reason or "Siril gave no reason")
                return outcome.header
            logger.info("%s has no start position; asking the online solver.", image_path)
        if sources:
            online = self.online_client.solve_sources(sources, image_width, image_height, **hints)
            if online:
                return online
        if image_path:
            return self.online_client.solve_image(image_path, **hints)
        return None

    def solve_frames(self, image_paths: Sequence[str], **hints: Any) -> dict[str, FrameSolve]:
        """Solve every frame of a session with Siril, each on its own.

        The first frame is solved alone, and the others in groups with one
        Siril run for each group (`sequence_solve.solve_frames`). A frame
        with no solution is not sent to the online service: photometry needs
        a solution that is the same kind for every frame of the session, and
        a frame Siril cannot solve from its start position is rarely one the
        online service would solve better.

        Parameters
        ----------
        image_paths : `Sequence` [`str`]
            The frames of one session, the first being the reference frame.
        **hints
            The hints of `PlateSolveDriver.solve`.

        Returns
        -------
        solves : `dict` [`str`, `FrameSolve`]
            One entry for each frame, with its solved header or the reason it
            has none.
        """
        hints.setdefault("verbose", False)
        timeout = hints.get("solve_timeout")
        limit = min(float(timeout), SOLVE_TIMEOUT_SECONDS) if timeout else SOLVE_TIMEOUT_SECONDS
        return solve_frames(image_paths, self.siril_executable, hints, timeout_seconds=limit)

    def _record_failure(self, image_path: str, reason: str) -> None:
        """Keep a failure's reason and log it as a warning.

        The warning goes to the job log of the running job. The jobs that
        collect the warnings they log (photometry and spectroscopy
        sessions) list it with the job's other warnings, and a run that
        raises `PlateSolveFailedError` carries the reason in the error.

        Parameters
        ----------
        image_path : `str`
            The image that was not solved.
        reason : `str`
            Why, in Siril's own words when it gave any.
        """
        self.last_failure_reason = reason
        logger.warning("Not solved: %s: %s", image_path, reason)
