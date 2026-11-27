"""Purpose: Place a target's stack picture on the sky, for a sky chart.

Description: A sky chart can lay a target's processed stack picture over the
sky. For that it needs the picture file and, for a grid of points across the
picture, where each point lies on the sky. This module works that out from
the stretched stack's header (``<stack>_processed.fits``): its plate solution,
including the SIP distortion terms (polynomial corrections a plate solver
adds for lens distortion), maps a pixel to a right ascension and declination.
Only the header is read; no pixels are.

Only a real plate solution counts (`header_is_plate_solved`). A header that
holds just the place the mount was told to point can sit about 80 pixels off,
so a stack with only that map is not placed.

The picture is the color picture when the target has one
(``stacking.color_preview_image``), else the stack's ``_preview.jpg``. Both
are written on the same pixel grid as the stretched stack, with the file's
first row at the top of the picture (Siril's ``savejpg`` output is flipped
back to the FITS row order; see ``_JPEG_ORIENTATION_COMMAND`` in
``pipelines/stacking/post_processing/stack_preview.py``). So a picture pixel
(column, row from the top) is the FITS pixel (x = column, y = row), counted
from 0. This was checked on M 31: the stack's pixels and both pictures
correlate 1.0 unflipped and 0.35 when flipped.

`get_sky_picture` is the owner of ``Visualization.get_sky_picture``. It keeps
each answer and reads the header again only when the stretched stack or the
picture file changes (its modification time), so a sky chart that asks for
every target's picture again and again reads each header once.
"""

import logging
import os
import threading

import numpy as np
from astropy.io import fits
from pydantic import BaseModel, ConfigDict, Field

from astrometricslib._internal.drivers.fits_access import (
    FITS_READ_ERRORS,
    header_is_plate_solved,
    read_header,
)
from astrometricslib.foundation.paths import resolve_mounted_path
from astrometricslib.models.target import Target, TargetStackingResult
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.utilities.stack_preview_path import is_processed_fits_path, processed_fits_path_for

logger = logging.getLogger(__name__)

__all__ = [
    "GRID_POINTS_PER_SIDE",
    "SkyPicturePlacement",
    "get_sky_picture",
    "place_picture_on_sky",
    "sky_grid",
    "sky_picture_files",
]

GRID_POINTS_PER_SIDE = 17
"""Points along each side of the sky grid, edges included. 17 points (16
cells) keep the largest gap between a grid point and the true pixel well
under a pixel for the distortion a stack's plate solution holds, and give
512 triangles per picture to draw."""


class SkyPicturePlacement(BaseModel):
    """Where a stack picture lies on the sky.

    Attributes
    ----------
    picture_path : `str`
        The picture file.
    width, height : `int`
        The picture's size in pixels (the stretched stack's size).
    grid_size : `int`
        Points along each side of the grid, edges included.
    grid : `list` [`list` [`float`]]
        ``grid_size`` squared ``[ra, dec]`` pairs in degrees, row by row from
        the picture's top-left corner.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    picture_path: str = Field(alias="picturePath")
    width: int
    height: int
    grid_size: int = Field(alias="gridSize")
    grid: list[list[float]]


def sky_picture_files(stacking: TargetStackingResult) -> tuple[str, str]:
    """Name the stretched stack and the picture to lay on the sky.

    Parameters
    ----------
    stacking : `TargetStackingResult`
        A target's imaging stacking result.

    Returns
    -------
    processed, picture : `tuple` [`str`, `str`]
        The stretched stack's path (the recorded processed image when it is
        one, else the name `processed_fits_path_for` gives the stack) and the
        picture's path (the color picture, else the preview). Each is an
        empty string when the target has none.
    """
    processed = stacking.processed_image or ""
    if not is_processed_fits_path(processed):
        processed = processed_fits_path_for(stacking.stacked_image) if stacking.stacked_image else ""
    picture = stacking.color_preview_image or stacking.preview_path or ""
    return processed, picture


def sky_grid(header: fits.Header, points_per_side: int = GRID_POINTS_PER_SIDE) -> list[list[float]] | None:
    """Work out where a regular grid of picture points lies on the sky.

    The grid runs from the picture's left edge to its right edge and from its
    top edge to its bottom edge (pixel edges, not pixel centers), row by row
    from the top. A picture pixel (column, row from the top) is the FITS pixel
    (x = column, y = row), counted from 0, so the top edge is y = -0.5.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The stretched stack's header, with its plate solution.
    points_per_side : `int`, optional
        Points along each side, edges included.

    Returns
    -------
    grid : `list` [`list` [`float`]] or `None`
        ``points_per_side`` squared ``[ra, dec]`` pairs in degrees, or `None`
        when the header has no usable plate solution. A sky map that did not
        come from a plate solve (`header_is_plate_solved`) is not used.
    """
    if not header_is_plate_solved(header):
        return None
    if not (
        str(header.get("CTYPE1", "")).startswith("RA") and str(header.get("CTYPE2", "")).startswith("DEC")
    ):
        return None
    width = int(header.get("NAXIS1", 0) or 0)
    height = int(header.get("NAXIS2", 0) or 0)
    wcs = celestial_wcs(header)
    if width <= 0 or height <= 0 or wcs is None:
        return None
    steps = np.linspace(0.0, 1.0, points_per_side)
    columns, rows = np.meshgrid(steps * width - 0.5, steps * height - 0.5)
    # all_pix2world applies the SIP distortion as well as the linear part.
    ra, dec = wcs.all_pix2world(columns.ravel(), rows.ravel(), 0)
    if not (np.all(np.isfinite(ra)) and np.all(np.isfinite(dec))):
        return None
    return [
        [float(right_ascension), float(declination)]
        for right_ascension, declination in zip(ra, dec, strict=True)
    ]


def place_picture_on_sky(processed_path: str, picture_path: str) -> SkyPicturePlacement | None:
    """Read a stretched stack's header and place its picture on the sky.

    Parameters
    ----------
    processed_path : `str`
        The stretched stack's path.
    picture_path : `str`
        The picture's path, given back in the answer.

    Returns
    -------
    placement : `SkyPicturePlacement` or `None`
        The placement, or `None` when the header cannot be read or has no
        plate solution. That is a status, not an error: it is logged at the
        information level.
    """
    try:
        header = read_header(processed_path)
    except FITS_READ_ERRORS as error:
        logger.info("Could not read the stack header %s: %s", processed_path, error)
        return None
    grid = sky_grid(header)
    if grid is None:
        logger.info("Stack %s has no plate solution; it is left off the sky.", processed_path)
        return None
    return SkyPicturePlacement(
        picture_path=picture_path,
        width=int(header["NAXIS1"]),
        height=int(header["NAXIS2"]),
        grid_size=GRID_POINTS_PER_SIDE,
        grid=grid,
    )


# Each answer by its two paths, with the files' modification times when it
# was worked out. A target whose files are unchanged is answered from here.
_placements: dict[str, tuple[tuple[float, float], SkyPicturePlacement | None]] = {}
_placements_lock = threading.Lock()


def get_sky_picture(target: Target) -> SkyPicturePlacement | None:
    """Say where a target's stack picture lies on the sky.

    The picture is the color picture when the target has one, else the
    stack's preview (`sky_picture_files`). Both files are looked for where
    the drive is mounted now. The answer is kept until either file changes.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    placement : `SkyPicturePlacement` or `None`
        The picture and its grid of sky positions, or `None` when the target
        has no stretched stack, no picture or no plate solution. That is a
        status, not an error.
    """
    processed, picture = sky_picture_files(target.stacking)
    processed = resolve_mounted_path(processed) if processed else ""
    picture = resolve_mounted_path(picture) if picture else ""
    if not processed or not picture or not os.path.isfile(processed) or not os.path.isfile(picture):
        return None
    try:
        stamp = (os.path.getmtime(processed), os.path.getmtime(picture))
    except OSError:
        return None
    key = f"{processed}|{picture}"
    with _placements_lock:
        cached = _placements.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    placement = place_picture_on_sky(processed, picture)
    with _placements_lock:
        _placements[key] = (stamp, placement)
    return placement
