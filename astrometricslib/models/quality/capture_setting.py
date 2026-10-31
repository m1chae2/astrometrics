"""The equipment and exposure settings a quality measurement was made with.

Many quality limits depend on how a frame was captured: a star width means
one thing at 400 mm focal length and another at 1000 mm. `CaptureSetting`
records those settings once, and each stored measurement points at it by id,
so the same settings are not repeated on every row.

Only the camera is required. A frame from an older session may lack some
header cards, and a partial record is still worth storing.
"""

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

PIXEL_SCALE_SIGNIFICANT_FIGURES = 4
"""How many significant figures of the pixel scale a setting keeps.

The scale comes from the frame's FITS header, and capture programs write the
same number with different precision (``1.915279`` and ``1.9152790075`` for
the same camera and telescope, found on M 13 in the #134 copy restack). Kept
in full, the two would be two settings, and the frames of one setting would
be split between them. Four figures is 0.05% at a scale near 2 arcsec, far
finer than the 25% between this observatory's optics (300 and 405 mm), and
far coarser than the header rounding. Design estimate."""

UNKNOWN_CAPTURE_SETTING_CAMERA = "unknown capture setting"
"""The camera name for a frame whose rows point at no capture setting.

`CaptureSetting` needs a camera, so a reader that must describe a frame with no
setting row uses this text. It is a placeholder, not a real camera; a reader
that sees it knows the setting is unknown and must not treat it as equipment.
"""


def round_pixel_scale(value: float | None) -> float | None:
    """Round a pixel scale to a fixed number of significant figures.

    The model and the quality store's migration both use this one function,
    so a stored value and a model value always agree.

    Parameters
    ----------
    value : `float` or `None`
        The pixel scale as given, in arcseconds per pixel.

    Returns
    -------
    rounded : `float` or `None`
        The scale to `PIXEL_SCALE_SIGNIFICANT_FIGURES` significant figures;
        `None`, zero and values that are not finite are kept as given.
    """
    if value is None or value == 0 or not math.isfinite(value):
        return value
    digits = PIXEL_SCALE_SIGNIFICANT_FIGURES - 1 - math.floor(math.log10(abs(value)))
    return round(value, digits)


class CaptureSetting(BaseModel):
    """One combination of equipment and exposure settings.

    Parameters
    ----------
    camera : `str`
        The camera model name.
    telescope : `str` or `None`
        The telescope or lens name.
    focal_length_mm : `float` or `None`
        The focal length in millimeters.
    pixel_scale_arcsec : `float` or `None`
        The size of one pixel on the sky, in arcseconds per pixel, rounded to
        `PIXEL_SCALE_SIGNIFICANT_FIGURES` significant figures.
    filter_name : `str` or `None`
        The filter in the light path, such as ``"L"`` or ``"Ha"``.
    exposure_seconds : `float` or `None`
        The exposure time of one frame, in seconds.
    gain_setting : `int` or `None`
        The camera's gain setting (a unitless number in the FITS ``GAIN``
        card, not electrons per ADU).
    offset : `int` or `None`
        The constant the camera adds to every pixel.
    binning : `int` or `None`
        The binning factor (1 means no binning).
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    camera: str
    telescope: str | None = None
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    filter_name: str | None = Field(default=None, alias="filterName")
    exposure_seconds: float | None = Field(default=None, alias="exposureSeconds")
    gain_setting: int | None = Field(default=None, alias="gainSetting")
    offset: int | None = None
    binning: int | None = None

    @field_validator("pixel_scale_arcsec")
    @classmethod
    def _round_pixel_scale(cls, value: float | None) -> float | None:
        """Round the pixel scale, so header precision does not split a setting.

        Settings read back from the store are rounded too, so rows stored
        before the rounding group with the new ones.

        Parameters
        ----------
        value : `float` or `None`
            The pixel scale as given, in arcseconds per pixel.

        Returns
        -------
        rounded : `float` or `None`
            The value from `round_pixel_scale`.
        """
        return round_pixel_scale(value)
