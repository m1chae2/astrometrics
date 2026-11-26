"""Reads the sky map (WCS) from a FITS header, and an image's corners on it.

A plate-solved image carries its sky map in its header: the World Coordinate
System (WCS), which turns a pixel position into a right ascension and
declination. Many steps read it, so the reading lives here once.

`celestial_wcs` reads the two sky axes of a header. It hides astropy's
notices about cards it had to repair (`FITSFixedWarning`), and gives `None`
for a header with no usable sky map instead of raising. A header that is not
plate-solved is an ordinary case, not an error.

`image_corners_on_sky` gives the sky positions of an image's four corners.
It uses the full map, including the SIP terms (polynomial corrections for
the lens distortion that a solver adds to the header), because the
distortion is largest at the corners.

`up_angle_degrees` says where the top of a picture points on the sky.
`describe_plate_solution` turns a solved sky map into the `PlateSolution`
that the public `plate_solve` call returns: the image center, pixel scale
and rotation, and the map's header cards.
"""

import math
import warnings
from collections.abc import Mapping
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_scales

from astrometricslib.models.plate_solution import PlateSolution

__all__ = [
    "WCS_READ_ERRORS",
    "celestial_wcs",
    "describe_plate_solution",
    "image_corners_on_sky",
    "up_angle_degrees",
]

# The errors that reading a broken or partial sky map can raise. astropy's
# own WCS errors (`InvalidTransformError`, `SingularMatrixError` and the
# rest) are `ValueError`s. A header too large to parse raises `MemoryError`.
WCS_READ_ERRORS = (ValueError, KeyError, TypeError, MemoryError, fits.verify.VerifyError)


def celestial_wcs(header: fits.Header | Mapping[str, Any] | None) -> WCS | None:
    """Read a header's sky map, if it has one.

    Parameters
    ----------
    header : `astropy.io.fits.Header` or `Mapping` or `None`
        A FITS header, or a plain mapping of its cards.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The two sky axes, with any SIP distortion terms, or `None` when there
        is no header or it has no usable sky map.
    """
    if header is None:
        return None
    try:
        card_header = header if isinstance(header, fits.Header) else fits.Header(dict(header))
        with warnings.catch_warnings():
            # astropy repairs old or odd date and unit cards and says so; the
            # repaired map is what is wanted.
            warnings.simplefilter("ignore", FITSFixedWarning)
            wcs = WCS(card_header, naxis=2)
        if not wcs.has_celestial:
            return None
        return wcs.celestial
    except WCS_READ_ERRORS:
        return None


def image_corners_on_sky(wcs: WCS, width: int, height: int) -> list[tuple[float, float]]:
    """Find the sky positions of an image's four corners.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The image's sky map. Its SIP distortion terms are applied.
    width, height : `int`
        The image size, in pixels.

    Returns
    -------
    corners : `list` [`tuple` [`float`, `float`]]
        ``(ra, dec)`` in degrees, in order around the edge, at the outer
        edges of the corner pixels.
    """
    pixel_x = np.array([-0.5, width - 0.5, width - 0.5, -0.5])
    pixel_y = np.array([-0.5, -0.5, height - 0.5, height - 0.5])
    ra_values, dec_values = wcs.all_pix2world(pixel_x, pixel_y, 0)
    return [(float(ra), float(dec)) for ra, dec in zip(ra_values, dec_values, strict=True)]


def describe_plate_solution(path: str, wcs: WCS, width: int, height: int) -> PlateSolution:
    """Describe a solved sky map: its center, pixel scale and rotation.

    Parameters
    ----------
    path : `str`
        The solved image.
    wcs : `astropy.wcs.WCS`
        The image's sky map. Its SIP distortion terms are applied to the
        center.
    width, height : `int`
        The image size, in pixels.

    Returns
    -------
    solution : `PlateSolution`
        The center (at 0-based pixel ``((width - 1) / 2, (height - 1) / 2)``,
        or the map's reference point when the size is not known),
        the mean pixel scale in arcseconds, the direction of the +y axis
        from north toward east, and the sky map's header cards.
    """
    celestial = wcs.celestial
    if width > 0 and height > 0:
        ra_values, dec_values = celestial.all_pix2world([(width - 1) / 2.0], [(height - 1) / 2.0], 0)
    else:
        # The image size is not known: the reference point is the best guess.
        ra_values, dec_values = [celestial.wcs.crval[0]], [celestial.wcs.crval[1]]
    scales = proj_plane_pixel_scales(celestial)
    header = celestial.to_header(relax=True)
    return PlateSolution(
        path=str(path),
        ra_deg=float(ra_values[0]) % 360.0,
        dec_deg=float(dec_values[0]),
        pixel_scale_arcsec=float(np.mean(scales)) * 3600.0,
        rotation_deg=float(up_angle_degrees(celestial, top_down=False)),
        width=int(width),
        height=int(height),
        wcs_header={key: header[key] for key in header if key not in ("", "COMMENT", "HISTORY")},
    )


def up_angle_degrees(wcs: WCS, *, top_down: bool) -> float:
    """Say where the top of the picture points on the sky.

    This is the viewer's rule (`measurePlateGeometry` in
    ``ui/common/fitsViewer/plateGeometry.ts``): the first row is at the top
    for a file stored ``ROWORDER = TOP-DOWN`` and at the bottom otherwise.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The celestial sky map.
    top_down : `bool`
        Whether the file's first row is drawn at the top.

    Returns
    -------
    angle : `float`
        Degrees from north toward east, 0 up to 360. 0 means north is at the
        top.
    """
    matrix = np.asarray(wcs.pixel_scale_matrix, dtype=float)
    row_step = -1.0 if top_down else 1.0
    up_east = matrix[0, 1] * row_step
    up_north = matrix[1, 1] * row_step
    return math.degrees(math.atan2(up_east, up_north)) % 360.0
