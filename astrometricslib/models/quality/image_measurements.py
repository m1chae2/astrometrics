"""What the measurement building blocks of `QualityRecords` return.

`QualityRecords` offers a few small measurements that a script can combine:
the empty sky's level and noise, the stars in an image, and the sky's tilt
across the frame. They take a FITS path or a pixel array and store nothing.
Every number is a plain Python float, int, bool or list.
"""

from pydantic import BaseModel

from astrometricslib.models.quality.detections import StarDetection

__all__ = ["FoundStars", "SkyBackground", "SkyGradient"]


class SkyBackground(BaseModel):
    """The empty sky's level and noise in one image.

    Parameters
    ----------
    level : `float`
        The sky level, in image units. Large bright objects are clipped
        out, so a galaxy that fills much of the frame does not raise it.
    noise : `float`
        The sky noise (one standard deviation of a pixel), in image units,
        from differences between pixels a few columns apart, so smooth
        light does not widen it.
    """

    level: float
    noise: float


class FoundStars(BaseModel):
    """The stars the shared star finder found in one image.

    Parameters
    ----------
    sky_level : `float`
        The sky level subtracted before the search, in image units.
    noise : `float`
        The sky noise the threshold was set from, in image units.
    fwhm_px : `float`
        The star width, in pixels, that sized the finder's star template.
    threshold_sigma : `float`
        How many noise levels above the sky a star had to stand.
    stars : `list` [`StarDetection`]
        The stars, brightest first. ``x`` and ``y`` are 1-based pixels (as
        in FITS and DS9), the same convention as
        `QualityRecords.measure_detections`. ``flux`` is the finder's
        brightness in image units. ``merged`` is always `False`.
    star_count : `int`
        The number of stars (the length of ``stars``).
    """

    sky_level: float
    noise: float
    fwhm_px: float
    threshold_sigma: float
    stars: list[StarDetection]
    star_count: int


class SkyGradient(BaseModel):
    """How much the sky tilts across a frame, measured as the preview does.

    Parameters
    ----------
    gradient_fraction : `float` or `None`
        The sky tilt across the frame as a fraction of the sky level.
        `None` when the image could not be measured.
    limit_fraction : `float`
        The tilt below which the preview subtracts a fitted plane instead
        of running GraXpert.
    plane_used : `bool`
        `True` when the preview would subtract the fitted plane.
    sky_bin_count : `int`
        How many of the 64 by 64 bins were taken as sky.
    plane_coefficients : `list` [`tuple` [`float`, `float`]]
        For each color plane, the change of the fitted plane from the center
        to the right and to the bottom edge, in image units. Empty when the
        plane is not used.
    radial_coefficients : `list` [`tuple` [`float`, `float`]]
        For each color plane, the terms ``(d2, d4)`` of the vignetting part
        ``d2 * r^2 + d4 * r^4``, in image units. Empty when it is not used.
    radial_fraction : `float` or `None`
        The vignetting at the corners as a fraction of the sky level, or
        `None` when it was not fitted.
    reason : `str`
        A plain sentence saying what was found.
    """

    gradient_fraction: float | None
    limit_fraction: float
    plane_used: bool
    sky_bin_count: int
    plane_coefficients: list[tuple[float, float]]
    radial_coefficients: list[tuple[float, float]]
    radial_fraction: float | None
    reason: str
