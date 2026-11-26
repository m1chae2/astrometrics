"""Purpose: Place each target's stack picture on the sky, for the Planetarium.

Description: The Planetarium can lay each target's processed stack picture
over its sky map. For that it needs the picture file and, for a grid of
points across the picture, where each point lies on the sky. This module
works that out from the stretched stack's header (``<stack>_processed.fits``):
its plate solution, including the SIP distortion terms, maps a pixel to a
right ascension and declination. Only the header is read; no pixels are.

Only a real plate solution counts (`header_is_plate_solved`). A header that
holds just the place the mount was told to point can sit about 80 pixels off,
so a stack with only that map is left off the sky. That is a status, not an
error: it is logged at the information level and nothing is shown.

The picture is the color picture when the target has one
(``stacking.color_preview_image``), else the stack's ``_preview.jpg``. Both
are written on the same pixel grid as the stretched stack, with the file's
first row at the top of the picture (Siril's ``savejpg`` output is flipped
back to the FITS row order; see ``_JPEG_ORIENTATION_COMMAND`` in
``stack_preview.py``). So a picture pixel (column, row from the top) is the
FITS pixel (x = column, y = row), counted from 0. This was checked on M 31:
the stack's pixels and both pictures correlate 1.0 unflipped and 0.35 when
flipped.

Answers are kept per stack and only worked out again when the stretched
stack or the picture file changes (its modification time).
"""

import logging
import os
import threading
import warnings
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib.drivers import header_is_plate_solved

logger = logging.getLogger(__name__)

GRID_POINTS_PER_SIDE = 17
"""Points along each side of the sky grid, edges included. 17 points (16
cells) keep the largest gap between a grid point and the true pixel well
under a pixel for the distortion a stack's plate solution holds, and give
512 triangles per picture to draw."""

_PROCESSED_SUFFIX = "_processed.fits"


def processed_stack_path(stacking: Any) -> str:
    """Name the stretched stack (``_processed.fits``) of a stacking result.

    Parameters
    ----------
    stacking : `TargetStackingResult`
        The target's stacking result.

    Returns
    -------
    path : `str`
        The stretched stack's path, or an empty string when the target has no
        stack.
    """
    processed = getattr(stacking, "processed_image", "") or ""
    if processed.lower().endswith(_PROCESSED_SUFFIX):
        return processed
    stacked = getattr(stacking, "stacked_image", "") or ""
    if not stacked:
        return ""
    stem, _extension = os.path.splitext(stacked)
    return stem + _PROCESSED_SUFFIX


def picture_path(stacking: Any) -> str:
    """Name the picture to lay on the sky: the color picture, else the preview.

    Parameters
    ----------
    stacking : `TargetStackingResult`
        The target's stacking result.

    Returns
    -------
    path : `str`
        The picture's path, or an empty string when the target has none.
    """
    color = getattr(stacking, "color_preview_image", "") or ""
    if color:
        return color
    return getattr(stacking, "preview_path", None) or ""


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
    ctype1 = str(header.get("CTYPE1", ""))
    ctype2 = str(header.get("CTYPE2", ""))
    if not (ctype1.startswith("RA") and ctype2.startswith("DEC")):
        return None
    width = int(header.get("NAXIS1", 0) or 0)
    height = int(header.get("NAXIS2", 0) or 0)
    if width <= 0 or height <= 0:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        try:
            wcs = WCS(header, naxis=2, relax=True)
        except (ValueError, KeyError, MemoryError) as error:
            logger.info("Stack header has no readable plate solution: %s", error)
            return None
    if not wcs.has_celestial:
        return None
    steps = np.linspace(0.0, 1.0, points_per_side)
    column_edges = steps * width - 0.5
    row_edges = steps * height - 0.5
    columns, rows = np.meshgrid(column_edges, row_edges)
    # all_pix2world applies the SIP distortion as well as the linear part.
    ra, dec = wcs.all_pix2world(columns.ravel(), rows.ravel(), 0)
    if not (np.all(np.isfinite(ra)) and np.all(np.isfinite(dec))):
        return None
    return [
        [float(right_ascension), float(declination)]
        for right_ascension, declination in zip(ra, dec, strict=True)
    ]


class StackSkyPictures:
    """Say where each target's stack picture lies on the sky, with a cache.

    Parameters
    ----------
    resolve_path : `Callable` [[`str`], `str`]
        Gives the spelling of a stored path that exists on this computer.
    """

    def __init__(self, resolve_path: Any) -> None:
        """Start with an empty cache."""
        self._resolve_path = resolve_path
        self._cache: dict[str, tuple[tuple[float, float], dict[str, Any] | None]] = {}
        self._lock = threading.Lock()

    def entry_for(self, target: Any) -> dict[str, Any] | None:
        """Describe one target's picture on the sky, or `None` without one.

        Parameters
        ----------
        target : `Target`
            The library target.

        Returns
        -------
        entry : `dict` or `None`
            ``targetId``, ``picturePath`` (the picture file, which the app
            loads through the folders the backend serves), ``width`` and
            ``height`` (the picture's size in pixels), ``gridSize`` (points per
            side) and ``grid`` (``[ra, dec]`` pairs in degrees, row by row from
            the top-left corner). `None` when the target has no stack, no
            picture or no plate solution.
        """
        stacking = getattr(target, "stacking", None)
        if stacking is None:
            return None
        processed = self._resolve_path(processed_stack_path(stacking))
        picture = self._resolve_path(picture_path(stacking))
        if not processed or not picture or not os.path.isfile(processed) or not os.path.isfile(picture):
            return None
        stamp = (os.path.getmtime(processed), os.path.getmtime(picture))
        key = f"{processed}|{picture}"
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None and cached[0] == stamp:
            return None if cached[1] is None else {**cached[1], "targetId": target.id}
        entry = self._compute(processed, picture)
        with self._lock:
            self._cache[key] = (stamp, entry)
        return None if entry is None else {**entry, "targetId": target.id}

    @staticmethod
    def _compute(processed: str, picture: str) -> dict[str, Any] | None:
        """Read the stack header and work out the grid.

        Parameters
        ----------
        processed : `str`
            The stretched stack's path.
        picture : `str`
            The picture's path.

        Returns
        -------
        entry : `dict` or `None`
            The entry without ``targetId``, or `None` when there is no plate
            solution.
        """
        try:
            header = fits.getheader(processed, memmap=False)
        except (OSError, ValueError) as error:
            logger.info("Could not read the stack header %s: %s", processed, error)
            return None
        grid = sky_grid(header)
        if grid is None:
            logger.info("Stack %s has no plate solution; it is left off the sky.", processed)
            return None
        return {
            "picturePath": picture,
            "width": int(header["NAXIS1"]),
            "height": int(header["NAXIS2"]),
            "gridSize": GRID_POINTS_PER_SIDE,
            "grid": grid,
        }
