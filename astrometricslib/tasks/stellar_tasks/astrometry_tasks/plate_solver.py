"""Modular interface for plate solving using Astrometry.net.

Supports both local and online (nova.astrometry.net) solving.

Notes
-----
Implements requirement REQ: SR-3.1 (isolated local-solve execution
environment).
"""

import logging
import os
import subprocess
import tempfile
from typing import Any

from astropy.io import fits
from astroquery.astrometry_net import AstrometryNet

logger = logging.getLogger(__name__)


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
                if "scale_units" in kwargs and kwargs.get("scale_lower") and kwargs.get("scale_upper"):
                    s_low = float(kwargs["scale_lower"]) * 0.8
                    s_high = float(kwargs["scale_upper"]) * 1.2
                    logger.info(
                        f"Using relaxed scale constraints: {s_low:.2f} - {s_high:.2f} {kwargs['scale_units']}"
                    )
                    cmd.extend(["--scale-units", kwargs["scale_units"]])
                    cmd.extend(["--scale-low", str(s_low)])
                    cmd.extend(["--scale-high", str(s_high)])

                # RA/Dec hints
                if "center_ra" in kwargs and kwargs["center_ra"] is not None:
                    cmd.extend(["--ra", str(kwargs["center_ra"])])
                if "center_dec" in kwargs and kwargs["center_dec"] is not None:
                    cmd.extend(["--dec", str(kwargs["center_dec"])])
                if ("center_ra" in kwargs or "center_dec" in kwargs) and "radius" in kwargs:
                    cmd.extend(["--radius", str(kwargs["radius"])])

                cmd.append(working_path)

                timeout = kwargs.get("solve_timeout", 300)
                logger.info(f"Executing: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

                if result.returncode != 0:
                    logger.warning(f"solve-field returned error code {result.returncode}")

                new_fits = os.path.join(tmp_dir, "input_image.new")
                if os.path.exists(new_fits):
                    with fits.open(new_fits) as hdul:
                        header = hdul[0].header.copy()
                    return header
            except Exception as e:
                logger.warning(f"Local solve failed: {e}")

        return None

        return None

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
        try:
            x = [s.get("x_centroid", s.get("xcentroid")) for s in sources]
            y = [s.get("y_centroid", s.get("ycentroid")) for s in sources]
            return self.astrometry_net.solve_from_source_list(x, y, width, height, **kwargs)
        except Exception as e:
            logger.warning(f"Online source solve failed: {e}")
        return None

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
        try:
            return self.astrometry_net.solve_from_image(image_path, **kwargs)
        except Exception as e:
            logger.warning(f"Online image solve failed: {e}")
        return None
