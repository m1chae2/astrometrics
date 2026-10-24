"""The equipment and exposure settings a quality measurement was made with.

Many quality limits depend on how a frame was captured: a star width means
one thing at 400 mm focal length and another at 1000 mm. `CaptureSetting`
records those settings once, and each stored measurement points at it by id,
so the same settings are not repeated on every row.

Only the camera is required. A frame from an older session may lack some
header cards, and a partial record is still worth storing.
"""

from pydantic import BaseModel, ConfigDict, Field


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
        The size of one pixel on the sky, in arcseconds per pixel.
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
