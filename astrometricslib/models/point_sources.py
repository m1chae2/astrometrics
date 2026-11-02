"""What the point-source detection returns: the stars found in a picture.

- `PointSource` is one star: where it is, how bright, and how star-like.
- `PointSourceDetection` is the list, brightest first.
"""

from pydantic import BaseModel, Field

__all__ = ["PointSource", "PointSourceDetection"]


class PointSource(BaseModel):
    """One star found in a picture.

    Parameters
    ----------
    id : `int` or `None`
        The detector's number for the star.
    x_centroid, y_centroid : `float`
        The star's center in pixels, counted from 0.
    flux : `float`
        The star's total brightness, in the picture's units.
    peak : `float` or `None`
        The brightest pixel value.
    sharpness, roundness1, roundness2 : `float` or `None`
        How star-like the shape is (the DAOStarFinder shape numbers).
    n_pixels : `int` or `None`
        How many pixels the star covers.
    mag, daofind_mag : `float` or `None`
        Instrumental magnitudes from the flux and from the finder.
    radius_px : `float` or `None`
        The star's measured radius, in pixels.
    """

    id: int | None = None
    x_centroid: float
    y_centroid: float
    flux: float
    peak: float | None = None
    sharpness: float | None = None
    roundness1: float | None = None
    roundness2: float | None = None
    n_pixels: int | None = None
    mag: float | None = None
    daofind_mag: float | None = None
    radius_px: float | None = None


class PointSourceDetection(BaseModel):
    """The stars found in one picture.

    Parameters
    ----------
    sources : `list` [`PointSource`]
        The stars, brightest first.
    """

    sources: list[PointSource] = Field(default_factory=list)
