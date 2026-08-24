"""Modular interface for plate solving using Astrometry.net.

Supports both local and online (nova.astrometry.net) solving.

Notes
-----
Implements requirement REQ: SR-3.1 (isolated local-solve execution
environment).
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

# Transport-level failures reaching nova.astrometry.net, as distinct from
# the server answering "I could not solve this field". Only the former is
# worth repeating: a dropped connection says nothing about the image,
# whereas an genuine unsolvable field (e.g. a lunar disk with no star
# pattern) fails identically no matter how many times it is uploaded.
#
# Observed on the 2026-08-23 full-catalog run: one M 13 session's
# reference frame died on
# ``('Connection aborted.', RemoteDisconnected('Remote end closed
# connection without response'))``. The very next solve in the same block
# succeeded, but that session had already lost its WCS -- and with it all
# 100 of its stars, which were dropped for having no sky position.
#
# Note that the builtin `TimeoutError` is deliberately absent: astroquery
# raises it when the *solve job* exceeds ``solve_timeout``, which is a
# statement about the field's difficulty, not the network. That exclusion
# is also why `socket.timeout` cannot be listed here -- since Python 3.10
# it *is* `TimeoutError`, so including it would silently make every
# solve-job timeout retryable. Genuine network read timeouts still match
# via the requests-style name check in `_is_transient_network_error`.
_TRANSIENT_NETWORK_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    http.client.BadStatusLine,
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    socket.gaierror,
)

# 3 attempts total. The observed failure recovered on the immediately
# following request, so the goal is only to ride out a single dropped
# connection -- not to keep hammering a service that is genuinely down,
# which would multiply an already slow step across every frame.
ONLINE_SOLVE_ATTEMPT_LIMIT = 3

# Seconds before retrying, doubled each attempt (2s, then 4s). Short
# enough not to stall a batch run, long enough to let a momentary
# server-side hiccup clear.
ONLINE_SOLVE_RETRY_BACKOFF_SECONDS = 2.0

# Cumulative per-process count of solve uploads, retries included, so a
# quality summary can distinguish a clean first-try solve from one that
# only succeeded after dropped connections. Reset by
# `reset_plate_solve_statistics` at the start of a run.
_plate_solve_attempts = 0


def get_plate_solve_attempt_count() -> int:
    """Report how many solve uploads this process has made.

    Returns
    -------
    attempts : `int`
        Total attempts, including retries after transient failures.
    """
    return _plate_solve_attempts


def reset_plate_solve_statistics() -> None:
    """Clear the solve-attempt tally, so a run counts only its own work."""
    global _plate_solve_attempts
    _plate_solve_attempts = 0


def _is_transient_network_error(error: BaseException) -> bool:
    """Report whether an exception looks like a retryable transport fault.

    `requests` wraps the underlying transport error in its own
    `ConnectionError`, and astroquery in turn may re-wrap that, so the
    exception's ``__cause__``/``__context__`` chain is walked rather than
    only checking the outermost type.

    Parameters
    ----------
    error : `BaseException`
        The exception raised by an online solve attempt.

    Returns
    -------
    is_transient : `bool`
        `True` if the failure is transport-level and worth retrying.
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
    """Run an online solve, repeating it only on transient network faults.

    Parameters
    ----------
    solve_call : `Callable`
        Zero-argument callable performing one solve attempt.
    description : `str`
        Short label for this solve strategy, used in log messages.

    Returns
    -------
    header : `astropy.io.fits.Header` or `None`
        The solved header, or `None` if every attempt failed.
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
    """Handle plate solving by interfacing with Astrometry.net.

    Supports both local and online solvers.
    """

    def __init__(self, api_key: str | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the plate solver.

        Parameters
        ----------
        api_key : `str`, optional
            Astrometry.net API key used for online solving. If `None`
            (default), online solving is disabled.
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
        """Solve a field, trying local and online strategies in order.

        Tries a local solve first, then a source-based online solve,
        then falls back to a full image upload.

        Parameters
        ----------
        image_path : `str`, optional
            Path to the FITS image to solve (default `None`).
        sources : `list` [`dict`], optional
            Detected source list used for source-based online solving
            (default `None`).
        image_width : `int`, optional
            Image width in pixels, used for source-based solving
            (default 1000).
        image_height : `int`, optional
            Image height in pixels, used for source-based solving
            (default 1000).
        **kwargs
            Additional solver options forwarded to the underlying
            local or online solve methods (e.g. ``scale_units``,
            ``scale_lower``, ``scale_upper``, ``center_ra``,
            ``center_dec``, ``radius``, ``solve_timeout``).

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solved WCS FITS header, or `None` if no solve
            strategy succeeded.
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
        """Attempt to solve using the local 'solve-field' command.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solved WCS FITS header, or `None` if the local
            solve did not produce a result.
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

        # REQ: SR-3.1 - Isolated execution environment
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

                # A hint that excludes the truth makes the field
                # unsolvable no matter how good the data is, so a failed
                # hinted solve is retried blind rather than given up on.
                #
                # The hints come from FOCALLEN/XPIXSZ, and this library's
                # own catalog shows how wrong they can be: the DSLR's
                # "Nikkor 300mm" images actually resolve at ~404mm, so
                # the computed 2.68 arcsec/px window of 2.03-3.37
                # excluded M 31's true 1.996 arcsec/px entirely. Same
                # image, same solver, on 2026-08-24: constrained did not
                # solve, blind solved in seconds against
                # index-tycho2-10. Four DSLR targets were lost this way
                # in one run, each with a perfectly good stack.
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
        """Run one `solve-field` invocation and return its solved header.

        Parameters
        ----------
        command : `list` [`str`]
            The full `solve-field` argv to execute.
        working_directory : `str`
            Directory `solve-field` writes its outputs into.
        timeout : `int`
            Seconds before the invocation is abandoned.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solved WCS header, or `None` when this invocation did
            not produce one.
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
        """Attempt an online solve from a list of detected sources.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solved WCS FITS header, or `None` if no API key is
            configured or the online solve did not succeed.
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
        """Attempt an online solve by uploading the full image.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solved WCS FITS header, or `None` if no API key is
            configured or the online solve did not succeed.
        """
        if not self.api_key:
            return None

        # astrometry.net's uploader runs source extraction over the
        # array as loaded, and its weighting step is written for a 2D
        # frame: handed a colour stack it raises "weights.ndim (2) must
        # match len(axes) (3)" and the solve is lost to a crash rather
        # than to the data. Every DSLR stack in this library is
        # (3, H, W), so the fallback path was unusable for exactly the
        # targets most likely to need it.
        #
        # Collapsing to a mean across channels rather than picking one
        # keeps every photon that contributes to a star's centroid,
        # which is all a solve needs -- colour carries no astrometric
        # information.
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
