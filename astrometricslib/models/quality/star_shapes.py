"""What `QualityRecords.measure_star_shapes` returns: how stretched stars are.

The result lists the stars the frame measurement fits (bright enough, not
saturated, and away from their neighbors), with each star's width, how much it
is stretched and the direction of the stretch, plus a short summary of the
whole image. The viewer's Shapes overlay draws one short line per star from
this list. A person reads the picture to tell trailing (all stars stretched
the same way) from tilt or spacing errors (stretch toward the center or one
corner) and from field curvature (worse in the corners). Every number is a
plain Python float, int or list, so it can be sent to the app as it is.
"""

from pydantic import BaseModel

__all__ = ["StarShape", "StarShapesMeasurement"]


class StarShape(BaseModel):
    """The width and stretch of one star.

    Parameters
    ----------
    x, y : `float`
        The star's position in 1-based pixels (as in FITS and DS9): the
        center of the first pixel is (1, 1). Same convention as
        `StarDetection` and `StarProfileMeasurement`.
    width_px : `float`
        The star's width (FWHM) in pixels, the geometric mean of its two axes.
    elongation : `float`
        How much the star is stretched, ``1 - b / a`` for a long axis ``a`` and
        a short axis ``b``. 0 is a round star and 0.5 is a star twice as long
        as it is wide.
    angle_degrees : `float`
        The direction of the long axis, from 0 up to but not including 180,
        measured from the +y axis toward the +x axis. This is the same
        convention as the cut angle of `StarProfileMeasurement`. A star
        stretched along y has 0, and one stretched along x has 90.
    """

    x: float
    y: float
    width_px: float
    elongation: float
    angle_degrees: float


class StarShapesMeasurement(BaseModel):
    """The star shapes of one image, with a summary.

    Parameters
    ----------
    path : `str`
        The image that was measured.
    width : `int`
        The image width in pixels.
    height : `int`
        The image height in pixels.
    shapes : `list` [`StarShape`]
        The measured stars. Empty when the image has too few usable stars.
    star_count : `int`
        The number of stars (the length of ``shapes``).
    median_width_px : `float` or `None`
        The median star width in pixels, or `None` when not measured.
    median_elongation : `float` or `None`
        The median of the stars' ``1 - b / a``, or `None` when not measured.
    direction_degrees : `float` or `None`
        The direction the stars are stretched in as a group, from +y toward
        +x as for each star. It comes from the average of the stars' stretch
        vectors, so a round star counts for little and a stretched one for
        much. `None` when not measured.
    direction_coherence : `float` or `None`
        From 0 (the stars point every which way) to 1 (all stars point the same
        way). A high value with a high elongation points to tracking.
    center_width_px : `float` or `None`
        The median width of the stars in the middle of the frame (the middle
        4 x 4 cells of a 14 x 14 grid over the frame). `None` when the middle
        holds too few stars.
    corner_width_px : `float` or `None`
        The median width of the stars in the four corners of the frame (the
        4 x 4 cells at each corner of the same grid), taken together. `None`
        when the corners hold too few stars.
    reason : `str`
        Why nothing was measured, or an empty string.
    """

    path: str
    width: int
    height: int
    shapes: list[StarShape]
    star_count: int
    median_width_px: float | None = None
    median_elongation: float | None = None
    direction_degrees: float | None = None
    direction_coherence: float | None = None
    center_width_px: float | None = None
    corner_width_px: float | None = None
    reason: str = ""
