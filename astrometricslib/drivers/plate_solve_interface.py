"""Tools to figure out exactly what part of the sky an image shows.

This uses a tool called Astrometry.net to map the stars in an image to
known coordinate systems. It tries to do this on the computer first,
and if that fails, it tries asking the internet.
"""

import http.client
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable
from typing import Any

from astropy.io import fits
from astroquery.astrometry_net import AstrometryNet

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
    http.client.BadStatusLine,
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    socket.gaierror,
)

# The maximum number of times to retry if the internet connection fails.
# Stop at 3 so the process doesn't get stuck forever if the site is down.
ONLINE_SOLVE_ATTEMPT_LIMIT = 3

# The base number of seconds to wait before retrying a failed connection.
# It doubles each time (2 seconds, 4 seconds, etc.) to give the server
# time to recover if it's overloaded.
ONLINE_SOLVE_RETRY_BACKOFF_SECONDS = 2.0

# Cumulative per-process count of solve uploads, retries included, so a
# quality summary can distinguish a clean first-try solve from one that
# only succeeded after dropped connections. Reset by
# `reset_plate_solve_statistics` at the start of a run.
_plate_solve_attempts = 0


def get_plate_solve_attempt_count() -> int:
    """Check how many times we've tried to solve an image.

    Returns
    -------
    attempts : `int`
        The number of tries, including times it had to retry because
        the internet dropped.
    """
    return _plate_solve_attempts


def reset_plate_solve_statistics() -> None:
    """Reset the retry counter back to zero for a new run."""
    global _plate_solve_attempts
    _plate_solve_attempts = 0


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
    global _plate_solve_attempts
    for attempt_number in range(1, ONLINE_SOLVE_ATTEMPT_LIMIT + 1):
        try:
            _plate_solve_attempts += 1
            return solve_call()
        except Exception as solve_error:
            if not _is_transient_network_error(solve_error):
                logger.warning(f"{description} failed: {solve_error}")
                return None
            if attempt_number == ONLINE_SOLVE_ATTEMPT_LIMIT:
                logger.warning(
                    f"{description} failed after {ONLINE_SOLVE_ATTEMPT_LIMIT} attempts "
                    f"(last error: {solve_error})."
                )
                return None
            backoff_seconds = ONLINE_SOLVE_RETRY_BACKOFF_SECONDS * (2 ** (attempt_number - 1))
            logger.warning(
                f"{description} hit a transient network error on attempt "
                f"{attempt_number}/{ONLINE_SOLVE_ATTEMPT_LIMIT} ({solve_error}); "
                f"retrying in {backoff_seconds:.0f}s."
            )
            time.sleep(backoff_seconds)
    return None


class PlateSolver:
    """The tool that manages all attempts to map out an image.

    It tries the local computer first, then the internet as a backup.
    """

    def __init__(self, api_key: str | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Set up the solver.

        Parameters
        ----------
        api_key : `str`, optional
            The password needed to use the online service. If there isn't
            one, the internet backup won't be tried.
        """
        self.api_key = api_key
        self.astrometry_net = AstrometryNet()
        if api_key:
            self.astrometry_net.api_key = api_key

    def solve(
        self,
        image_path: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        image_width: int = 1000,
        image_height: int = 1000,
        **kwargs,  # ruff: ignore[missing-type-kwargs]
    ) -> fits.Header | None:
        """Try everything possible to figure out where the image is pointing.

        Three things are tried in order:
        1. Run the solver locally on this computer (fastest).
        2. Send just the dots (stars) to the internet (saves bandwidth).
        3. Upload the whole giant image to the internet (last resort).

        Parameters
        ----------
        image_path : `str`, optional
            The path to the image file on the hard drive.
        sources : `list` [`dict`], optional
            The list of stars already found in the image.
        image_width : `int`, optional
            How wide the image is (needed for the "just dots" internet solve).
        image_height : `int`, optional
            How tall the image is (needed for the "just dots" internet solve).
        **kwargs
            Extra settings like hints about where the telescope was pointing.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The final map metadata, or None if the process gave up.
        """
        # astroquery.astrometry_net defaults verbose=True, which prints
        # progress dots and dumps the full source table to stdout. Silence
        # it unless a caller explicitly asks for that output.
        kwargs.setdefault("verbose", False)

        # 1. Local Solve
        if image_path:
            header = self._solve_locally(image_path, **kwargs)
            if header:
                return header

        # 2. Online Source-based Solve
        if sources:
            header = self._solve_online_sources(sources, image_width, image_height, **kwargs)
            if header:
                return header

        # 3. Online Image-based Solve (Fallback)
        if image_path:
            return self._solve_online_image(image_path, **kwargs)

        return None

    def _solve_locally(self, image_path: str, **kwargs) -> fits.Header | None:  # ruff: ignore[missing-type-kwargs]
        """Try to solve the image using the program installed on this computer.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The map metadata if it worked, or None if it failed.
        """
        from astrometricslib.utilities.config_loader import get_configuration

        config = get_configuration()

        # Build solver config
        if config.app_config.has_section("Processing.Astrometry.Local Solver"):
            solver_config = config.app_config["Processing.Astrometry.Local Solver"]
        elif config.app_config.has_section("Astrometry.Solver"):
            solver_config = config.app_config["Astrometry.Solver"]
        else:
            solver_config = {}

        index_path = solver_config.get("index_path", "/usr/share/astrometry")
        autoindex = True
        if hasattr(solver_config, "getboolean"):
            autoindex = solver_config.getboolean("autoindex", fallback=True)
        cpulimit = solver_config.get("cpulimit", "300")

        config_lines = [f"add_path {index_path}"]
        if autoindex:
            config_lines.append("autoindex")
        config_lines.append(f"cpulimit {cpulimit}")

        # Isolated execution environment
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_cfg_path = os.path.join(tmp_dir, "astrometry.cfg")
            with open(tmp_cfg_path, "w") as tmp_cfg:
                tmp_cfg.write("\n".join(config_lines))

            try:
                original_path = os.path.abspath(os.path.expanduser(image_path))
                # Use a symlink in the temp dir to the image to keep the
                # output files isolated avoid spaces in path issues too.
                working_path = os.path.join(tmp_dir, "input_image.fits")
                os.symlink(original_path, working_path)

                # 1. Base command with robust parameters
                cmd = [
                    "solve-field",
                    "--config",
                    tmp_cfg_path,
                    "--no-plots",
                    "--overwrite",
                    "--downsample",
                    "2",
                    "--uniformize",
                    "0",
                    "--dir",
                    tmp_dir,
                ]

                # Scale constraints
                hinted_command = list(cmd)
                applied_hints = False
                if "scale_units" in kwargs and kwargs.get("scale_lower") and kwargs.get("scale_upper"):
                    s_low = float(kwargs["scale_lower"]) * 0.8
                    s_high = float(kwargs["scale_upper"]) * 1.2
                    logger.info(
                        f"Using relaxed scale constraints: {s_low:.2f} - {s_high:.2f} {kwargs['scale_units']}"
                    )
                    hinted_command.extend(["--scale-units", kwargs["scale_units"]])
                    hinted_command.extend(["--scale-low", str(s_low)])
                    hinted_command.extend(["--scale-high", str(s_high)])
                    applied_hints = True

                # RA/Dec hints
                if "center_ra" in kwargs and kwargs["center_ra"] is not None:
                    hinted_command.extend(["--ra", str(kwargs["center_ra"])])
                    applied_hints = True
                if "center_dec" in kwargs and kwargs["center_dec"] is not None:
                    hinted_command.extend(["--dec", str(kwargs["center_dec"])])
                    applied_hints = True
                if ("center_ra" in kwargs or "center_dec" in kwargs) and "radius" in kwargs:
                    hinted_command.extend(["--radius", str(kwargs["radius"])])

                hinted_command.append(working_path)
                timeout = kwargs.get("solve_timeout", 300)

                header = self._run_solve_field(hinted_command, tmp_dir, timeout)
                if header is not None:
                    return header

                # Try again without any hints if the first attempt fails.
                # If the user's telescope settings are slightly wrong (like
                # forgetting to account for a focal reducer), the strict hints
                # will actually prevent the solver from finding the right
                # answer.
                # A blind retry fixes this by searching everywhere.
                if applied_hints:
                    logger.info("Hinted local solve failed; retrying blind (no scale or position hints).")
                    blind_command = [*cmd, working_path]
                    header = self._run_solve_field(blind_command, tmp_dir, timeout)
                    if header is not None:
                        logger.info("Blind local solve succeeded where the hinted solve did not.")
                        return header
            except Exception as e:
                logger.warning(f"Local solve failed: {e}")

        return None

    def _run_solve_field(
        self, command: list[str], working_directory: str, timeout: int
    ) -> fits.Header | None:
        """Run the actual shell command to launch the local solver program.

        Parameters
        ----------
        command : `list` [`str`]
            The exact terminal command to run.
        working_directory : `str`
            The temporary folder where it can dump its math files.
        timeout : `int`
            How long to let it think before it is killed.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The map metadata if it worked, or None.
        """
        logger.info(f"Executing: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.warning(f"solve-field returned error code {result.returncode}")

        # solve-field signals success by writing the .new file; a
        # zero exit status alone does not mean the field was solved.
        solved_path = os.path.join(working_directory, "input_image.new")
        if not os.path.exists(solved_path):
            return None
        with fits.open(solved_path) as hdul:
            return hdul[0].header.copy()

    def _solve_online_sources(
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
        if not self.api_key:
            return None
        x = [s.get("x_centroid", s.get("xcentroid")) for s in sources]
        y = [s.get("y_centroid", s.get("ycentroid")) for s in sources]
        return _call_with_transient_retry(
            lambda: self.astrometry_net.solve_from_source_list(x, y, width, height, **kwargs),
            description="Online source solve",
        )

    def _solve_online_image(self, image_path: str, **kwargs) -> fits.Header | None:  # ruff: ignore[missing-type-kwargs]
        """Try to solve by uploading the whole image to the internet.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The map metadata if it worked, or None.
        """
        if not self.api_key:
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
                    import numpy as np

                    temporary_directory = tempfile.mkdtemp(prefix="plate_solve_mono_")
                    upload_path = os.path.join(temporary_directory, "mono_for_solve.fits")
                    flattened = np.mean(image_data, axis=0).astype("float32")
                    fits.PrimaryHDU(data=flattened, header=hdul[0].header).writeto(
                        upload_path, overwrite=True
                    )
                    logger.info(f"Flattened {image_data.shape} colour stack to 2D for the online solve.")
        except Exception as flatten_error:
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
