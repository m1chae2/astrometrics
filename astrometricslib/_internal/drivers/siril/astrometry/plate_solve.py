"""Purpose: Siril's plate solve: its command and the run that solves an image.

Description: Siril's ``platesolve`` command finds where an image points on the
sky. It needs a position to start from, the focal length and the pixel size,
and a star catalog: Siril's Gaia catalog on the disk (``localgaia``) or the
same catalog asked from a server (``gaia``). Siril cannot solve an image with
no start position; the caller sends that image to the online service.

Every plate solve carries ``-noflip``. Without it, Siril turns an image stored
bottom-up the other way round after solving it, which would change the pixels
the app then measures.

`platesolve_command` is the one place this command is written. The preview's
colour calibration (`preview.color_calibration`) uses it with only a catalog.
`solve_image` is the operation the app's plate-solve driver
(`astrometry.solve_driver`) calls for image astrometry and for mount centering:

1. It reads the start position, focal length and pixel size (`read_inputs`)
   from the caller's hints and the image header.
2. It runs Siril on a staged copy of the image in the app's scratch folder, so
   the image itself is never written. Each run is a short Siril process of its
   own, started with the private settings file and in offline mode. It never
   waits for the stack's Siril slot (`script_run.run_siril_script` with
   ``wait_for_slot=False``), so a centering solve is not held up by a stack.
3. If the solve fails, it solves again with a shorter focal length
   (`FOCAL_LENGTH_RETRY_FACTORS`). Camera programs often write the longest
   focal length of a zoom lens, and Siril accepts a scale error of only 20%.
4. It reads the solution from the file Siril saved and the fit's size from
   what Siril printed (`parse_fit`), and returns them as a `PlateSolveHeader`.
"""

import logging
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

from astropy.coordinates import SkyCoord
from astropy.io import fits

from astrometricslib._internal.drivers.file_staging import stage_file
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import PlateSolveHeader
from astrometricslib._internal.drivers.siril.core.program import find_local_gaia_catalog
from astrometricslib._internal.drivers.siril.core.script_run import last_siril_output, run_siril_script
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.paths import scratch_directory
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

logger = logging.getLogger(__name__)

# Siril's catalog names: Gaia from the disk, or Gaia from a server.
LOCAL_GAIA_CATALOG = "localgaia"
ONLINE_GAIA_CATALOG = "gaia"

# Siril's plate solve needs the 1.4 release for ``-catalog=localgaia``.
PLATE_SOLVE_MINIMUM_SIRIL_VERSION = "1.4.0"

# The most a plate-solve run may take, in seconds. Siril's own search limit is
# 30 s (its default), and it starts in about a second, so a run that is still
# going after 60 s has hung. The study (AST-23) solved in 0.5 to 1.6 s.
SOLVE_TIMEOUT_SECONDS = 60

# The focal lengths the retries try, each as a fraction of the header's.
# The header of a zoom lens often holds its longest focal length, and the true
# one lies lower. In the study a focal length 10% too long solved and 20% too
# long failed, while 20% too short solved (AST-23), so Siril tolerates a
# little over 10% on the long side. A step of 0.8 therefore leaves no gap:
# the attempts at 1.0, 0.8 and 0.64 of the header's value cover true focal
# lengths from the header's own down to about 0.58 of it. Two retries cost
# about 3 s on a frame that never solves.
FOCAL_LENGTH_RETRY_FACTORS = TunedParameter(
    name="internal.siril_solve_focal_length_retry_factors",
    default=(0.8, 0.64),
    unit="× header focal length",
    description="Fractions of the header's focal length that a failed Siril plate solve retries with.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-23",
)

# The name of the staged copy of the image and of the solved file, in the
# scratch folder Siril works in.
_STAGED_IMAGE_NAME = "input_image.fits"
_SOLVED_STEM = "solved"

_SUCCESS_TEXT = "Siril solve succeeded"
_FIT_PATTERN = re.compile(r"order: (\d+), nmatched: (\d+), sigx:([\d.eE+-]+), sigy:([\d.eE+-]+)")
_FAILURE_PATTERN = re.compile(r"Plate solving failed:?[^\n]*|no target coordinates[^\n]*", re.IGNORECASE)


def platesolve_command(
    catalog: str,
    *,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    focal_length_mm: float | None = None,
    pixel_size_um: float | None = None,
    force: bool = False,
) -> str:
    """Write Siril's command that plate-solves the loaded image.

    Parameters
    ----------
    catalog : `str`
        The star catalog Siril matches the stars to: `LOCAL_GAIA_CATALOG` or
        `ONLINE_GAIA_CATALOG`.
    ra_deg, dec_deg : `float`, optional
        The position to start the search from, in degrees. Both or neither.
        Without them Siril reads the image header.
    focal_length_mm : `float`, optional
        The focal length in millimeters. Without it Siril reads the header.
    pixel_size_um : `float`, optional
        The pixel size in micrometers. Without it Siril reads the header.
    force : `bool`, optional
        Whether to solve an image that already holds a solution. Default
        `False`.

    Returns
    -------
    command : `str`
        The ``platesolve`` command, always with ``-noflip``.
    """
    words = ["platesolve"]
    if ra_deg is not None and dec_deg is not None:
        words.append(f"{ra_deg:.6f},{dec_deg:.6f}")
    words.append(f"-catalog={catalog}")
    if focal_length_mm is not None:
        words.append(f"-focal={focal_length_mm:.3f}")
    if pixel_size_um is not None:
        words.append(f"-pixelsize={pixel_size_um:.4f}")
    if force:
        words.append("-force")
    words.append("-noflip")
    return " ".join(words)


@dataclass(frozen=True)
class SolveInputs:
    """What Siril needs to solve one image.

    Attributes
    ----------
    ra_deg, dec_deg : `float`
        The position to start the search from, in degrees.
    focal_length_mm : `float`
        The focal length in millimeters, as the header or the hints give it.
    pixel_size_um : `float`
        The pixel size in micrometers, binning included.
    """

    ra_deg: float
    dec_deg: float
    focal_length_mm: float
    pixel_size_um: float


def _positive_number(value: Any) -> float | None:
    """Turn a header value into a positive number, if it is one.

    Parameters
    ----------
    value : `object`
        A header value, or `None`.

    Returns
    -------
    number : `float` or `None`
        The value, or `None` if it is missing, not a number or not above 0.
    """
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _header_pixel_size(header: fits.Header) -> float | None:
    """Read the pixel size, in micrometers, from an image header.

    ``XPIXSZ`` already includes binning. Without it, the camera's own pixel
    size (``PIXSIZE1``) is multiplied by the binning (``XBINNING``, 1 if
    absent).

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The image header.

    Returns
    -------
    pixel_size_um : `float` or `None`
        The size, or `None` if the header names none.
    """
    binned = _positive_number(header.get("XPIXSZ"))
    if binned is not None:
        return binned
    unbinned = _positive_number(header.get("PIXSIZE1"))
    if unbinned is None:
        return None
    return unbinned * (_positive_number(header.get("XBINNING")) or 1.0)


def _header_position(header: fits.Header) -> tuple[float, float] | None:
    """Read where the telescope pointed, in degrees, from an image header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The image header. ``RA`` and ``DEC`` in degrees are tried first, then
        ``OBJCTRA`` and ``OBJCTDEC`` in hours and degrees (sexagesimal).

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        Right ascension and declination in degrees, or `None` if the header
        holds no usable position.
    """
    try:
        ra_deg, dec_deg = float(header["RA"]), float(header["DEC"])
        if (
            math.isfinite(ra_deg)
            and math.isfinite(dec_deg)
            and 0.0 <= ra_deg < 360.0
            and -90.0 <= dec_deg <= 90.0
        ):
            return ra_deg, dec_deg
    except KeyError, TypeError, ValueError:
        pass
    if header.get("OBJCTRA") is not None and header.get("OBJCTDEC") is not None:
        try:
            sky_position = SkyCoord(f"{header['OBJCTRA']} {header['OBJCTDEC']}", unit=("hourangle", "deg"))
            return float(sky_position.ra.deg), float(sky_position.dec.deg)
        except ValueError, TypeError:
            return None
    return None


def read_inputs(header: fits.Header, hints: dict[str, Any]) -> SolveInputs | None:
    """Gather the start position, focal length and pixel size of an image.

    The caller's hints come first, then the header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The image header.
    hints : `dict`
        The hints the plate-solve interface gives: ``center_ra`` and
        ``center_dec`` (degrees), and ``scale_lower`` and ``scale_upper``
        (arcseconds per pixel). The scale is used only to work out a focal
        length when the header has none.

    Returns
    -------
    inputs : `SolveInputs` or `None`
        What Siril needs, or `None` when any part is missing. An image with
        no position is the blind case, which Siril cannot solve.
    """
    position: tuple[float, float] | None = None
    if hints.get("center_ra") is not None and hints.get("center_dec") is not None:
        try:
            position = (float(hints["center_ra"]), float(hints["center_dec"]))
        except TypeError, ValueError:
            position = None
    if position is None:
        position = _header_position(header)
    pixel_size = _header_pixel_size(header)
    if position is None or pixel_size is None:
        return None
    focal_length = _positive_number(header.get("FOCALLEN"))
    if focal_length is None:
        low, high = _positive_number(hints.get("scale_lower")), _positive_number(hints.get("scale_upper"))
        if low is None or high is None:
            return None
        # The pixel scale is 206.265 * pixel size / focal length.
        focal_length = 206.265 * pixel_size / ((low + high) / 2.0)
    return SolveInputs(position[0], position[1], focal_length, pixel_size)


def parse_fit(output: str) -> tuple[float | None, int | None]:
    """Read the size of the fit from what Siril printed after a solve.

    Siril prints ``order: 3, nmatched: 381, sigx:0.164, sigy:0.183``: the
    order of the distortion fit, the stars it matched to the catalog and the
    spread of the matched stars' position errors on each axis, in arcseconds.

    Parameters
    ----------
    output : `str`
        What Siril printed.

    Returns
    -------
    fit_residual_rms_arcsec : `float` or `None`
        The two spreads combined, the root mean square (RMS) distance of a
        matched star from its catalog position. `None` if Siril printed none.
    matched_star_count : `int` or `None`
        The number of matched stars, or `None`.
    """
    matches = list(_FIT_PATTERN.finditer(output))
    if not matches:
        return None, None
    match = matches[-1]
    sigma_x, sigma_y = float(match.group(3)), float(match.group(4))
    return math.hypot(sigma_x, sigma_y), int(match.group(2))


def _solve_once(
    image_path: str,
    inputs: SolveInputs,
    focal_length_mm: float,
    siril_executable: str,
    catalog_path: str,
    timeout_seconds: float,
) -> PlateSolveHeader | None:
    """Run Siril once with one focal length.

    Parameters
    ----------
    image_path : `str`
        The image to solve. It is only read.
    inputs : `SolveInputs`
        The start position and the pixel size.
    focal_length_mm : `float`
        The focal length for this attempt.
    siril_executable : `str`
        The command that starts Siril.
    catalog_path : `str`
        Siril's Gaia catalog file on the disk.
    timeout_seconds : `float`
        The longest the run may take.

    Returns
    -------
    solved : `PlateSolveHeader` or `None`
        The solved header with the fit's size, or `None` if Siril found no
        solution or could not be started.
    """
    work_folder = tempfile.mkdtemp(prefix="plate_solve_", dir=scratch_directory())
    try:
        stage_file(
            os.path.abspath(os.path.expanduser(image_path)), os.path.join(work_folder, _STAGED_IMAGE_NAME)
        )
        commands = [
            f"requires {PLATE_SOLVE_MINIMUM_SIRIL_VERSION}",
            f"set core.catalogue_gaia_astro={catalog_path}",
            f'load "{_STAGED_IMAGE_NAME}"',
            platesolve_command(
                LOCAL_GAIA_CATALOG,
                ra_deg=inputs.ra_deg,
                dec_deg=inputs.dec_deg,
                focal_length_mm=focal_length_mm,
                pixel_size_um=inputs.pixel_size_um,
                force=True,
            ),
            f'save "{_SOLVED_STEM}"',
            "close",
        ]
        try:
            run_siril_script(
                work_folder,
                commands,
                siril_executable,
                timeout_seconds=timeout_seconds,
                wait_for_slot=False,
                offline=True,
                script_name="plate_solve",
            )
        except OSError as start_error:
            logger.warning("Siril could not be started for a plate solve: %s", start_error)
            return None
        output = last_siril_output(include_errors=True)
        solved_path = os.path.join(work_folder, f"{_SOLVED_STEM}.fits")
        if _SUCCESS_TEXT not in output or not os.path.isfile(solved_path):
            failure = _FAILURE_PATTERN.search(output)
            logger.info(
                "Siril did not solve %s with a focal length of %.1f mm: %s",
                image_path,
                focal_length_mm,
                failure.group(0).strip() if failure else "no solution",
            )
            return None
        try:
            solved_header = read_header(solved_path, first_hdu_only=True)
        except FITS_READ_ERRORS as read_error:
            logger.warning("Could not read Siril's solved image %s: %s", solved_path, read_error)
            return None
        residual_rms_arcsec, matched_star_count = parse_fit(output)
        return PlateSolveHeader.from_header(
            solved_header,
            fit_residual_rms_arcsec=residual_rms_arcsec,
            matched_star_count=matched_star_count,
        )
    finally:
        shutil.rmtree(work_folder, ignore_errors=True)


def solve_image(
    image_path: str,
    siril_executable: str,
    hints: dict[str, Any],
    catalog_path: str | None = None,
    timeout_seconds: float = SOLVE_TIMEOUT_SECONDS,
) -> PlateSolveHeader | None:
    """Plate-solve one image with Siril, retrying with a shorter focal length.

    Parameters
    ----------
    image_path : `str`
        The image file. It is only read.
    siril_executable : `str`
        The command that starts Siril (the ``siril_executable`` setting).
    hints : `dict`
        The hints of the plate-solve interface (see `read_inputs`).
    catalog_path : `str`, optional
        Siril's Gaia catalog file on the disk. By default
        `find_local_gaia_catalog` looks for it.
    timeout_seconds : `float`, optional
        The longest one Siril run may take. Default
        `SOLVE_TIMEOUT_SECONDS`.

    Returns
    -------
    solved : `PlateSolveHeader` or `None`
        The solved header, whose sky map and fit numbers the astrometry
        pipeline reads. `None` if the image has no start position or pixel
        size (the caller sends it to the online service), if the Gaia
        catalog is not installed, or if every attempt failed.
    """
    try:
        header = read_header(image_path, first_hdu_only=True)
    except FITS_READ_ERRORS as read_error:
        logger.warning("Could not read %s for a Siril plate solve: %s", image_path, read_error)
        return None
    inputs = read_inputs(header, hints)
    if inputs is None:
        logger.info(
            "%s has no start position, focal length or pixel size; Siril cannot solve it.", image_path
        )
        return None
    if catalog_path is None:
        catalog_path = find_local_gaia_catalog()
    if catalog_path is None:
        logger.warning("Siril's Gaia astrometry catalog is not installed; no local plate solve.")
        return None
    factors = (1.0, *(float(factor) for factor in resolve(FOCAL_LENGTH_RETRY_FACTORS).value))
    for attempt_number, factor in enumerate(factors):
        solved = _solve_once(
            image_path,
            inputs,
            inputs.focal_length_mm * factor,
            siril_executable,
            catalog_path,
            timeout_seconds,
        )
        if solved is not None:
            if attempt_number > 0:
                logger.info(
                    "Siril solved %s with a focal length of %.1f mm, %.0f%% of the header's %.1f mm.",
                    image_path,
                    inputs.focal_length_mm * factor,
                    factor * 100,
                    inputs.focal_length_mm,
                )
            return solved
    return None
