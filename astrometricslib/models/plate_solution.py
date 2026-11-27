"""What `processing.astrometry.solve` returns: where an image points.

`PlateSolution` holds the sky position of the image's center, its pixel
scale and its rotation, plus the sky map (WCS, World Coordinate System)
cards so a script can rebuild the full pixel-to-sky mapping. Every number is
a plain Python float or int, so the result can be sent as JSON as it is.
"""

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["PlateSolution"]


class PlateSolution(BaseModel):
    """The plate solution of one image.

    Parameters
    ----------
    path : `str`
        The solved image.
    ra_deg, dec_deg : `float`
        The sky position (ICRS, in degrees; right ascension 0 up to 360) of
        the center of the image.
    pixel_scale_arcsec : `float`
        The size of one pixel on the sky, in arcseconds, averaged over the
        two axes.
    rotation_deg : `float`
        Where the image's +y axis (the direction of increasing row number)
        points on the sky, in degrees from north toward east, 0 up to 360.
        0 means north is along +y.
    width, height : `int`
        The image size, in pixels.
    wcs_header : `dict` [`str`, `Any`]
        The sky map as FITS header cards, distortion (SIP) terms included.
        ``astropy.wcs.WCS(solution.wcs_header)`` rebuilds the mapping.
    """

    path: str
    ra_deg: float
    dec_deg: float
    pixel_scale_arcsec: float
    rotation_deg: float
    width: int
    height: int
    wcs_header: dict[str, Any] = Field(default_factory=dict)
