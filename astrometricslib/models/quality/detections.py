"""What `quality.detections.measure_detections` returns: the detected stars.

The result lists every star that the astrometry run's star finder detects in
one image, with its position and brightness, and the radius of the circle
photometry measures each star in. The viewer draws that circle at each one,
so a person can see a detection that sits on no star, a marker between two
close stars, or a star the finder missed. Every number is a plain Python
float, int, bool or list, so it can be sent to the app as it is.
"""

from pydantic import BaseModel

__all__ = ["DetectionsMeasurement", "StarDetection"]


class StarDetection(BaseModel):
    """One star the star finder reported.

    Parameters
    ----------
    x, y : `float`
        The star's position in 1-based pixels (as in FITS and DS9): the
        center of the first pixel is (1, 1). Same convention as
        `StarProfileMeasurement`.
    flux : `float`
        The finder's brightness for the star, in image units. For a merged
        star it is the sum of the reports that were merged.
    merged : `bool`
        `True` when two or more reports of one star were merged into this
        detection. The position is that of the brightest report.
    """

    x: float
    y: float
    flux: float
    merged: bool = False


class DetectionsMeasurement(BaseModel):
    """Every star the astrometry run's star finder detects in one image.

    Parameters
    ----------
    path : `str`
        The image that was searched.
    width : `int`
        The image width in pixels.
    height : `int`
        The image height in pixels.
    detections : `list` [`StarDetection`]
        The stars, brightest first.
    detection_count : `int`
        The number of stars (the length of ``detections``).
    merged_count : `int`
        How many of them are merged detections.
    kernel_fwhm_px : `float`
        The star width, in pixels, that sized the finder's kernel. For a
        color image it is the width on the binned copy, in binned pixels.
    binned_on_color : `bool`
        `True` when the image has color channels and the finder searched an
        averaged, smaller copy, as the run does. Positions are given back
        in full image pixels.
    star_width_px : `float` or `None`
        The star width measured on this image, in pixels, already put on the
        library's one star-width scale (the per-frame Gaussian of the
        brightest stars) with the ratio of ``star_width_estimator``. It sizes
        the photometry circle. `None` when no star could be measured; the
        active setup's typical width was used instead.
    star_width_estimator : `str` or `None`
        The `StarWidthEstimator` that measured the width: ``"stack
        Gaussian"`` for a stack of two or more frames, else ``"frame
        Gaussian, brightest stars"`` (the scale itself). `None` with no
        width.
    aperture_radius_px : `float` or `None`
        The radius, in image pixels, of the circle that photometry measures
        every star in, sized from ``star_width_px``. It is the same for every
        star, because all stars in one image share one shape. `None` when it
        was not set.
    """

    path: str
    width: int
    height: int
    detections: list[StarDetection]
    detection_count: int
    merged_count: int
    kernel_fwhm_px: float
    binned_on_color: bool = False
    star_width_px: float | None = None
    star_width_estimator: str | None = None
    aperture_radius_px: float | None = None
