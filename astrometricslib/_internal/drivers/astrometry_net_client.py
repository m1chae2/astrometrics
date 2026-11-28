"""Purpose: The client of the online Astrometry.net plate-solving service.

Description: `AstrometryNetOnlineClient` asks the online Astrometry.net
service (nova.astrometry.net) which part of the sky an image shows. The app's
plate solver (`SirilPlateSolveDriver`, in `drivers/siril/astrometry/`) uses it
only for an image that has no start position, which Siril cannot solve. The
client sends either the positions of the stars found in the image or the whole
image, and retries when the internet connection drops (see
`_call_with_transient_retry`).

The service returns no table of matched stars, so an online solution carries no
fit statistics (see `PlateSolveHeader`).
"""

import contextlib
import contextvars
import logging
import os
import shutil
import socket
import tempfile
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from astropy.io import fits
from astroquery.exceptions import TimeoutError as AstroqueryTimeoutError

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.offline import OfflineModeError, require_online
from astrometricslib.foundation.paths import scratch_directory
from astrometricslib.foundation.run_tally import RunTally, add_to_bound_tally, bind_tally

logger = logging.getLogger(__name__)

# A list of network errors that are safe to retry. Only retry
# if the internet connection dropped or glitched. If the solver fails because
# the image is genuinely unsolvable (like a picture of the moon), retrying
# won't help, so those errors are not included here.
#
# Note that `TimeoutError` is NOT included. The solver library uses that
# error to say "I tried for a long time but couldn't solve it," not "the
# internet disconnected." If timeouts were retried, time would be wasted on
# impossible images.
_TRANSIENT_NETWORK_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    *outside_services.DROPPED_CONNECTION_ERRORS,
    socket.gaierror,
)

# The errors an online solve call can raise. ``OSError`` covers the network
# errors (requests' errors are ``OSError`` too). ``http.client.HTTPException``
# covers a reply the server cut off. astroquery raises ``RuntimeError`` and
# ``ValueError`` for a failed or rejected job, and its own ``TimeoutError``
# when a solve job runs too long. ``KeyError`` comes from a reply that is
# missing a field.
_ONLINE_SOLVE_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    *outside_services.HTTP_PROTOCOL_ERRORS,
    RuntimeError,
    ValueError,
    KeyError,
    AstroqueryTimeoutError,
)

# The maximum number of times to retry if the internet connection fails.
# Stop at 3 so the process doesn't get stuck forever if the site is down.
ONLINE_SOLVE_ATTEMPT_LIMIT = 3

# The base number of seconds to wait before retrying a failed connection.
# It doubles each time (2 seconds, 4 seconds, etc.) to give the server
# time to recover if it's overloaded.
ONLINE_SOLVE_RETRY_BACKOFF_SECONDS = 2.0

# The solve uploads of the current run, retries included, so a quality summary
# can distinguish a clean first-try solve from one that only succeeded after
# dropped connections. The run (`pipelines/astrometry/runner.py`) makes a
# `RunTally`, binds it here with `bind_plate_solve_attempts` around its
# pipeline, and reads it back; `_call_with_transient_retry` adds one for each
# upload to the tally bound where it runs, and to none when no run is counting.
_bound_plate_solve_attempts: contextvars.ContextVar[RunTally | None] = contextvars.ContextVar(
    "plate_solve_attempts", default=None
)


def bind_plate_solve_attempts(tally: RunTally) -> contextlib.AbstractContextManager[RunTally]:
    """Count solve uploads in ``tally`` for the code inside a ``with`` block.

    Parameters
    ----------
    tally : `RunTally`
        The run's count of tries, including times it had to retry because
        the internet dropped.

    Returns
    -------
    scope : `contextlib.AbstractContextManager`
        Use it in a ``with`` statement, in the thread that solves.
    """
    return bind_tally(_bound_plate_solve_attempts, tally)


def _is_transient_network_error(error: BaseException) -> bool:
    """Check if an error is just a temporary internet glitch.

    Retry should not happen if the image is truly broken, but it SHOULD happen
    to retry if the Wi-Fi just blinked off for a second. Dig through
    the error message to see what the root cause was.

    Parameters
    ----------
    error : `BaseException`
        The error the program threw.

    Returns
    -------
    is_transient : `bool`
        True if it's a temporary internet glitch that should be retried.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _TRANSIENT_NETWORK_ERROR_TYPES):
            return True
        # requests' exception hierarchy is not importable here without
        # taking a hard dependency on it, so match its ConnectionError
        # (and friends) by name as a fallback.
        if type(current).__name__ in {
            "ConnectionError",
            "ConnectTimeout",
            "ChunkedEncodingError",
            "ProtocolError",
            "ReadTimeout",
            "Timeout",
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


def _call_with_transient_retry(
    solve_call: Callable[[], fits.Header | None], *, description: str
) -> fits.Header | None:
    """Try to solve via the internet, retrying if the connection drops.

    Parameters
    ----------
    solve_call : `Callable`
        The block of code that actually tries the internet solve.
    description : `str`
        A name for this attempt so it can be logged cleanly.

    Returns
    -------
    header : `astropy.io.fits.Header` or `None`
        The solved map data, or None if it completely failed.
    """
    for attempt_number in range(1, ONLINE_SOLVE_ATTEMPT_LIMIT + 1):
        try:
            add_to_bound_tally(_bound_plate_solve_attempts, 1)
            return solve_call()
        except _ONLINE_SOLVE_ERRORS as solve_error:
            if not _is_transient_network_error(solve_error):
                logger.warning("%s failed: %s", description, solve_error)
                return None
            if attempt_number == ONLINE_SOLVE_ATTEMPT_LIMIT:
                logger.warning(
                    "%s failed after %s attempts (last error: %s).",
                    description,
                    ONLINE_SOLVE_ATTEMPT_LIMIT,
                    solve_error,
                )
                return None
            backoff_seconds = ONLINE_SOLVE_RETRY_BACKOFF_SECONDS * (2 ** (attempt_number - 1))
            logger.warning(
                "%s hit a transient network error on attempt %s/%s (%s); retrying in %.0fs.",
                description,
                attempt_number,
                ONLINE_SOLVE_ATTEMPT_LIMIT,
                solve_error,
                backoff_seconds,
            )
            time.sleep(backoff_seconds)
    return None


class AstrometryNetOnlineClient:
    """Solves an image with the online Astrometry.net service.

    Both solves give up quietly (return `None`) when there is no API key or
    the launch is offline, so the caller can treat the service as not there.
    """

    def __init__(self, api_key: str | None = None) -> None:
        """Set up the client.

        Parameters
        ----------
        api_key : `str`, optional
            The password needed to use the online service. Without one,
            nothing is sent.
        """
        self.api_key = api_key
        self.astrometry_net = outside_services.AstrometryNetSolver(api_key)

    def _is_usable(self) -> bool:
        """Say whether the online solver may be asked.

        Returns
        -------
        usable : `bool`
            `False` without an API key, and `False` when this launch sends
            nothing off the computer. Either way the caller gives up on the
            online solve, which is the same as the solver being unavailable.
        """
        if not self.api_key:
            return False
        try:
            require_online("The online plate solver")
        except OfflineModeError as offline_error:
            logger.debug("%s", offline_error)
            return False
        return True

    def solve_sources(
        self,
        sources: list[dict[str, Any]],
        width: int,
        height: int,
        **kwargs,  # ruff: ignore[missing-type-kwargs]
    ) -> fits.Header | None:
        """Try to solve by sending just the star locations to the internet.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The map metadata if it worked, or None if there isn't an
            API key or it failed.
        """
        if not self._is_usable():
            return None
        x = [s.get("x_centroid", s.get("xcentroid")) for s in sources]
        y = [s.get("y_centroid", s.get("ycentroid")) for s in sources]
        return _call_with_transient_retry(
            lambda: self.astrometry_net.solve_from_source_list(x, y, width, height, **kwargs),
            description="Online source solve",
        )

    def solve_image(self, image_path: str, **kwargs: Any) -> fits.Header | None:
        """Try to solve by uploading the whole image to the internet.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The map metadata if it worked, or None.
        """
        if not self._is_usable():
            return None

        # The online solver at astrometry.net only accepts black-and-white (2D)
        # images. If a color (3D) image is sent, it will crash.
        #
        # To fix this, the color channels are squashed together by taking the
        # average. This turns it into a black-and-white image but keeps all
        # the starlight in the right place so the solver can still work.
        upload_path = image_path
        temporary_directory = None
        try:
            with fits.open(image_path, memmap=False) as hdul:
                image_data = hdul[0].data
                needs_flattening = image_data is not None and image_data.ndim == 3
                if needs_flattening:
                    temporary_directory = tempfile.mkdtemp(
                        prefix="plate_solve_mono_", dir=scratch_directory()
                    )
                    upload_path = os.path.join(temporary_directory, "mono_for_solve.fits")
                    flattened = np.mean(image_data, axis=0).astype("float32")
                    fits.PrimaryHDU(data=flattened, header=hdul[0].header).writeto(
                        upload_path, overwrite=True
                    )
                    logger.info("Flattened %s colour stack to 2D for the online solve.", image_data.shape)
        except FITS_READ_ERRORS as flatten_error:
            # Falling back to the original path keeps behaviour no worse
            # than before if the frame cannot be read or rewritten.
            logger.debug("Could not prepare image for online solve: %s", flatten_error)
            upload_path = image_path

        try:
            return _call_with_transient_retry(
                lambda: self.astrometry_net.solve_from_image(upload_path, **kwargs),
                description="Online image solve",
            )
        finally:
            if temporary_directory:
                shutil.rmtree(temporary_directory, ignore_errors=True)
