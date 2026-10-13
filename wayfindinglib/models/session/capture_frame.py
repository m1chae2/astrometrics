"""Purpose: One recorded light frame, reduced to what capture analysis uses.

Description: The science library keeps a full record for every frame. The
capture analysis needs only a few numbers from each: when it was taken, how
long the exposure was, whether it clipped, how sharp the stars are, and where
the telescope pointed. `CaptureFrame` holds those and nothing else, so the
analysis works on plain data and never touches the frame library itself.

A frame's own fields come from the camera at capture time. Its measurements
(`saturated_pixel_fraction`, `background_adu`, `star_width_arcsec`,
`roundness`) come from the science library's frame scan and registration.
Registration runs only on frames that were stacked, so the star measurements
are missing for many frames, and a missing value stays `None`.
"""

from pydantic import BaseModel, ConfigDict, Field


class CaptureFrame(BaseModel):
    """One light frame taken with the equipment in use.

    Attributes
    ----------
    path : `str`
        Where the frame is stored.
    target_id : `str`
        The target the frame belongs to.
    timestamp : `float`
        When the exposure began, in seconds since the Unix epoch.
    exposure_seconds : `float`
        Exposure length, in seconds.
    filter_name : `str`
        The filter the frame was taken through.
    is_spectral : `bool`
        Whether the frame was taken through a spectroscopy filter (a
        dispersing grating). A dispersed star has no meaningful width, and
        the star being clipped matters differently, so these frames are
        judged separately from imaging frames.
    sensor_temperature_c : `float` or `None`
        Sensor temperature at capture, in degrees Celsius.
    saturated_pixel_fraction : `float` or `None`
        Share of the frame's pixels at or above the camera's saturation
        threshold, from 0 to 1.
    background_adu : `float` or `None`
        Median pixel value of the frame, in ADU (the camera's counts).
    star_width_arcsec : `float` or `None`
        Star width (FWHM) from registration, in arcseconds.
    roundness : `float` or `None`
        Star roundness from registration: the narrow axis divided by the wide
        one, so 1.0 is a circle and lower is more elongated.
    altitude_degrees : `float` or `None`
        Altitude of the pointing, in degrees.
    azimuth_degrees : `float` or `None`
        Azimuth of the pointing, in degrees.
    pier_side : `str` or `None`
        Side of the pier the telescope was on.
    pixel_scale_arcsec : `float` or `None`
        Arcseconds of sky per pixel for this frame.
    binning : `int`
        Pixel binning the frame was taken with (1 is unbinned).
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    target_id: str = Field(alias="targetId")
    timestamp: float
    exposure_seconds: float = Field(alias="exposureSeconds")
    filter_name: str = Field(alias="filterName")
    is_spectral: bool = Field(alias="isSpectral")
    sensor_temperature_c: float | None = Field(default=None, alias="sensorTemperatureC")
    saturated_pixel_fraction: float | None = Field(default=None, alias="saturatedPixelFraction")
    background_adu: float | None = Field(default=None, alias="backgroundAdu")
    star_width_arcsec: float | None = Field(default=None, alias="starWidthArcsec")
    roundness: float | None = None
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    azimuth_degrees: float | None = Field(default=None, alias="azimuthDegrees")
    pier_side: str | None = Field(default=None, alias="pierSide")
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    binning: int = 1


class StackSaturationVerdict(BaseModel):
    """The science library's verdict on one exposure length of a target.

    The science library stacks a target's frames one exposure length at a
    time and checks, on the pixels, whether a star clips at that length. It
    also works out the exposure that would keep the brightest star below the
    camera's ceiling. This holds those two results, so the capture analysis
    reuses the science library's verdict and does not repeat the check.

    Attributes
    ----------
    target_id : `str`
        The target.
    is_spectral : `bool`
        Whether the verdict is from the spectroscopy stack.
    exposure_seconds : `float`
        The exposure length the verdict is for.
    saturated : `bool`
        Whether a star clips at this exposure length.
    recommended_exposure_seconds : `float` or `None`
        The exposure that would keep the brightest star in the stack below
        the ceiling, or `None` if it could not be worked out. It describes
        the brightest star in the field, which is the target only for a
        single-star field such as a spectroscopy target.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    is_spectral: bool = Field(alias="isSpectral")
    exposure_seconds: float = Field(alias="exposureSeconds")
    saturated: bool
    recommended_exposure_seconds: float | None = Field(default=None, alias="recommendedExposureSeconds")
