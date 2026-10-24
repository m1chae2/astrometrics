"""Purpose: One recorded light frame, reduced to what capture analysis uses.

Description: The science library keeps a full record for every frame. The
capture analysis needs only a few numbers from each: when it was taken, how
long the exposure was, whether it clipped, how sharp the stars are, and where
the telescope pointed. `CaptureFrame` holds those and nothing else, so the
analysis works on plain data and never touches the frame library itself.

A frame's own fields come from the camera at capture time. Its measurements
(`saturated_pixel_fraction`, `background_adu`, `star_width_arcsec`,
`roundness`) are read from the quality measurements the science library
stored for the frame. A frame that was not measured, or whose measurement
failed, has `None` for that value, and a missing value is never replaced by a
default. `measurement_rows` keeps the id of each stored row, so a statement
that rests on a frame can cite the exact measurement.
"""

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import MeasurementReference


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
        Star width (FWHM of a Moffat fit) from the stored measurement
        ``frame.star_width_moffat_arcsec``, in arcseconds.
    roundness : `float` or `None`
        Star roundness: the narrow axis divided by the wide one, so 1.0 is a
        circle and lower is more elongated. It is the inverse of the stored
        measurement ``frame.star_elongation``.
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
    measurement_rows : `dict` [`str`, `int`]
        For each stored measurement this frame's values came from, the id of
        its row in the measurement store, by metric name.
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
    measurement_rows: dict[str, int] = Field(default_factory=dict, alias="measurementRows")

    def reference(self, metric: str) -> MeasurementReference | None:
        """Point at the stored measurement of one metric for this frame.

        Parameters
        ----------
        metric : `str`
            A metric name, such as ``"frame.star_width_moffat_arcsec"``.

        Returns
        -------
        reference : `MeasurementReference` or `None`
            The reference, or `None` if this frame has no stored row of that
            metric.
        """
        row_id = self.measurement_rows.get(metric)
        return None if row_id is None else MeasurementReference(row_id=row_id)
